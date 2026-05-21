# Annotations Feature — Audit & Implementation Plan

Target executor: Sonnet 4.6. Branch base: `main` (current `HEAD = 08a7787`). The only uncommitted change is `web/src/components/viewers/pdf-viewer.tsx` (range-based highlight rects — keep as-is, don't revert).

User decisions baked in:
- Scope: **bugs + perf + UX polish** (no full hook rewrite).
- ACL/visibility: **out of scope** (annotations remain world-readable per current site model).
- SSE: **switch to payload-bearing events** so the client can merge in place.
- List ordering: **switch to `created_at DESC` (newest first)**.

The plan is split into five PR-sized chunks. Do them in order; each one should be a separate commit and is independently testable.

---

## 0. Repo orientation (read first)

Key files you will touch:

| Layer | File | Lines |
|---|---|---|
| API | `api/app/models/annotation.py` | 54 |
| API | `api/app/schemas/annotation.py` | 75 |
| API | `api/app/routers/annotations.py` | 138 |
| API | `api/app/services/annotation.py` | 276 |
| API | `api/app/core/sse.py` | 92 |
| API tests | `api/tests/test_annotations.py` | 342 |
| Web hook | `web/src/hooks/use-annotations.ts` | 169 |
| Web UI | `web/src/components/annotations/annotation-thread.tsx` | 284 |
| Web UI | `web/src/components/annotations/annotation-selection-tooltip.tsx` | 255 |
| Web UI | `web/src/components/sidebar/annotations-tab.tsx` | 208 |
| Web UI | `web/src/components/browse/material-viewer.tsx` | 548 |
| Web UI | `web/src/components/viewers/pdf-viewer.tsx` | 493 (has WIP) |
| Web UI | `web/src/components/viewers/markdown-viewer.tsx` | 241 |
| i18n | `web/messages/en.json`, `web/messages/fr.json` | — |

Critical project convention — see `memory/feedback_patch_optimistic_update.md`:
> After a PATCH/PUT/POST returning updated data, capture the response and update local state directly. **Do NOT call a re-fetch.** This bug has recurred multiple times in this repo.

The current `use-annotations` hook violates this rule on every mutation. Fixing it is part of PR #2 below.

---

## PR #1 — Backend correctness & ordering

### 1.1 Soft-deleted materials must be invisible to annotations

`Material` has a `deleted_at` column (see `api/app/models/material.py:40-48` for the partial unique indices keyed on `deleted_at IS NULL`). The annotation service treats deleted materials as live.

Edits in `api/app/services/annotation.py`:
- `_get_material_current_version` (line 35): add `Material.deleted_at.is_(None)` to the `where` clause.
- `get_annotations` (line 65): same — filter the existence check.
- That's enough; both create and list go through these helpers.

Add tests in `api/tests/test_annotations.py`:
- `test_list_annotations_soft_deleted_material_404`
- `test_create_annotation_soft_deleted_material_404`

(Use `material.deleted_at = datetime.now(UTC)` then commit before calling the endpoint.)

### 1.2 List ordering → newest-first

`api/app/services/annotation.py:106` orders `Annotation.created_at.asc(), Annotation.id.asc()`.

Switch to DESC:
```python
.order_by(Annotation.created_at.desc(), Annotation.id.desc())
```

Cursor comparison logic at lines 92-102 must invert too:
```python
base = base.where(
    or_(
        Annotation.created_at < cursor_dt,
        and_(Annotation.created_at == cursor_dt, Annotation.id < cursor_id),
    )
)
```

Replies inside a thread stay ASC (chronological within a discussion) — line 128 is fine as-is.

Tests to add:
- `test_list_annotations_returns_newest_first` — create three annotations with explicit `created_at` offsets, assert ordering.
- `test_pagination_cursor_with_desc_order` — create 3 root annotations, paginate with `limit=2`, follow cursor, assert no overlap and no missing items.

### 1.3 Notification failure must not abort annotation creation

`api/app/services/annotation.py:205-213` calls `notify_user` inside `create_annotation` before the final `flush`. If notifications throw (DB blip, etc.), the annotation insert is rolled back.

Wrap it:
```python
try:
    await notify_user(db, reply_target.author_id, "annotation_reply", ...)
except Exception:
    logger.exception("Failed to send annotation_reply notification")
```
Use the existing logger pattern from elsewhere in `app/services` (look at `app/services/notification.py` or `app/services/user.py` for the canonical `logger = logging.getLogger(__name__)` setup).

### 1.4 Hard-coded notification link points at the wrong route

The link `f"/browse?material={material_id}"` (line 212) is a query-style hack. The real browse path on the frontend is `/browse/<slug-path>`, not a query string. Match what other notifications do — search for similar `link=` calls in `app/services/` (e.g. PR comment notifications) and copy the convention. If there's no clean slug at hand, link to `/profile/me/notifications` or `/browse` (the materials index). Don't fabricate a route that doesn't render.

### 1.5 Position-data shape is opaque — at least document it

`position_data: dict[str, object] | None` accepts anything up to 20 keys. The frontend only ever sends `{ page, textContent }`. Add a TypedDict / Pydantic sub-model:

```python
class AnnotationPosition(BaseModel):
    page: int | None = Field(None, ge=0, le=100_000)
    textContent: str | None = Field(None, max_length=1000)
    # allow extra keys for forward-compat (PDF coordinates etc.)
    model_config = {"extra": "allow"}
```

Replace `position_data: dict[str, object] | None` with `position_data: AnnotationPosition | None` in `AnnotationCreateIn` and `AnnotationOut`. Keep the column type as JSONB. This gives validation today without locking out future coordinate data.

### 1.6 No rate limit on listing

`POST` has `10/minute`; `GET` is unlimited. Cursor pagination + reply joinedloads can be expensive. Add:
```python
@material_annotations_router.get("/{material_id}/annotations", ...)
@limiter.limit("120/minute")
async def list_annotations(...): ...
```

---

## PR #2 — SSE payloads + frontend state hygiene

### 2.1 SSE: broadcast the annotation, not just an event type

Today the router does:
```python
broadcast_to_topic(material_id, {"type": "annotation_created"})
broadcast_to_topic(str(material_id), {"type": "annotation_deleted"})
```
The client refetches the whole first page on every event. That's lossy under pagination and wasteful.

Edits in `api/app/routers/annotations.py`:
- `add_annotation` (line 80): broadcast the full serialized annotation.
  ```python
  out = AnnotationOut.model_validate(annotation)
  broadcast_to_topic(material_id, {
      "type": "annotation_created",
      "annotation": out.model_dump(mode="json"),
  })
  return out
  ```
- `remove_annotation` (line 112): broadcast `{ "type": "annotation_deleted", "id": annotation_id, "thread_id": <root id at delete time> }`. `delete_annotation` must return both ids — change its signature.
- Add a PATCH broadcast: `edit_annotation` (line 95) currently doesn't broadcast at all. After update, emit `{ "type": "annotation_updated", "annotation": out.model_dump(mode="json") }`.

In `api/app/services/annotation.py`:
- `delete_annotation` (line 246) currently returns `material_id`. Change return type to `tuple[uuid.UUID, uuid.UUID, uuid.UUID]` → `(material_id, annotation_id, thread_id)`. Capture `thread_id` **before** the row is removed.

### 2.2 Frontend SSE listeners: merge into state, don't refetch

Rewrite the SSE block in `web/src/hooks/use-annotations.ts:94-107`:

```ts
useEffect(() => {
    if (!materialId) return;
    const connection = createSSEConnection({
        url: `/materials/${materialId}/sse`,
        listeners: {
            annotation_created: (e) => applyCreated(JSON.parse(e.data).annotation),
            annotation_updated: (e) => applyUpdated(JSON.parse(e.data).annotation),
            annotation_deleted: (e) => {
                const { id, thread_id } = JSON.parse(e.data);
                applyDeleted(id, thread_id);
            },
        },
        startupDelay: 50,
    });
    return () => connection.close();
}, [materialId, applyCreated, applyUpdated, applyDeleted]);
```

Note: `createSSEConnection` currently passes the handler as `() => void` (see `web/src/lib/sse-client.ts`). Widen its signature to `(event: MessageEvent) => void` so handlers can read `event.data`. Update all existing callers (grep for `createSSEConnection`). The other call sites are notification feeds; verify with `git grep "createSSEConnection"` and adjust.

`apply*` helpers operate on the `threads` array:
- **created (root)**: prepend to `threads` (newest-first ordering matches PR #1.2). Increment `total`.
- **created (reply)**: find the thread by `annotation.thread_id`, append to its `replies` array.
- **updated**: walk threads, find by `id` in root or replies, replace in place.
- **deleted**: if `id === thread_id`, drop the whole thread and `total--`. Else, drop the matching reply from its thread.

De-dup: when the local user creates an annotation, `createAnnotation` already appends it (see 2.3). The SSE event will arrive too. Apply each apply-fn idempotently — check `if threads.some(t => t.root.id === ann.id)` before prepending, and likewise for replies.

### 2.3 Mutations must update state from the response, not refetch

Per `memory/feedback_patch_optimistic_update.md`. Today in `web/src/hooks/use-annotations.ts`:
- `createAnnotation` (line 109) calls `await fetchAnnotations(true)` after POST.
- `editAnnotation` (line 131) calls `await fetchAnnotations(true)` after PATCH.
- `deleteAnnotation` (line 142) calls `await fetchAnnotations(true)` after DELETE.

Rewrite:
- `createAnnotation`: capture the returned `AnnotationData`; if it's a root (`reply_to_id === null`), prepend `{ root: ann, replies: [] }` to `threads`; if reply, append into matching thread. Increment `total` for roots.
- `editAnnotation`: capture the returned `AnnotationData` and mutate the matching node in `threads` in place.
- `deleteAnnotation`: optimistically remove client-side using the same apply-deleted helper. No refetch.

The `apply*` helpers from 2.2 should be used by both the SSE path and the local-mutation path so behavior is identical.

### 2.4 No more double-fire on create

After 2.3, when the local user creates an annotation, the POST response updates state and the SSE `annotation_created` event arrives shortly after with the same payload. The idempotent dedup in 2.2 absorbs this. Verify by adding a Vitest unit test (or at minimum a manual test): create an annotation → confirm only one entry appears even though both code paths run.

### 2.5 `loadMore` still works under prepend semantics

After PR #1.2 the API returns newest-first; `loadMore` appends older items at the end. With state mutations from SSE/local create prepending new entries to the top, the merged array stays monotonically DESC. Confirm by walking through the order manually in your head, then write an integration test.

### 2.6 Error handling

`fetchAnnotations` currently does `catch {}` (line 72). Surface failure via a `toast.error` and an `error` state field so the UI can show a retry button. Skeleton state already exists in `annotations-tab.tsx`; add an error branch alongside.

---

## PR #3 — Tooltip & sidebar UX polish

### 3.1 Fix duplicate i18n keys

In `web/messages/en.json` AND `web/messages/fr.json`, the `Annotations` block has:
- `"shiftEnterForNewline"` (lowercase `line`) — line 453 in each file
- `"shiftEnterForNewLine"` (camelCase `Line`) — line 465 in each file

Only one consumer references `Newline` — `annotation-selection-tooltip.tsx:217`. Change that reference to `t("shiftEnterForNewLine")` (camelCase, matching the rest of the codebase — see `annotation-thread.tsx:268`, `Sidebar.shiftEnterForNewLine` at line 441, and the global key at line 65). Then delete the duplicate `shiftEnterForNewline` key from both locales.

### 3.2 Tooltip closes when you click the textarea on touch devices

`annotation-selection-tooltip.tsx:101-111` listens for `mousedown` on `document` and closes when the click is outside the tooltip. On mobile the `mouseup` path fires before the user can interact. Two issues:
1. The selection state is captured on `mouseup` only — touch selection often doesn't fire mouseup the same way. Add a `touchend` listener alongside `mouseup` (call `handleMouseUp` from both).
2. The outside-click handler should also listen for `touchstart` (use the same handler).

### 3.3 Tooltip can't see the page number for non-react-pdf viewers

`annotation-selection-tooltip.tsx:80-89` walks up looking for `data-page-number`. This attribute is set by react-pdf only. For markdown/code/csv/epub etc., `page` ends up `undefined` (which is fine — backend stores null). Document this in the tooltip comment so future readers don't assume it's broken.

### 3.4 Inline edit instead of bottom-of-list edit form

`web/src/components/sidebar/annotations-tab.tsx:166-203` renders the edit form *at the bottom of the list*, not adjacent to the annotation being edited. When the list scrolls, the editor disappears off-screen.

Refactor:
- Pass `editingId` and `editBody` plus an `onChangeBody` callback into `AnnotationThread` (or render the editor inside `AnnotationItem` when `annotation.id === editingId`).
- Remove the bottom-of-list editor block.
- Keep `handleSaveEdit` in `annotations-tab.tsx`; thread it through props.

### 3.5 Cursor on selection highlights

`pdf-viewer.tsx:209-210` and `markdown-viewer.tsx:232-233` set `cursor: "pointer"` and `pointerEvents: "auto"` only when `onAnnotationClick` is set. That's fine, but the highlight has `mixBlendMode: "multiply"` which behaves badly in dark mode (the highlight goes black). Switch to:
```ts
backgroundColor: "rgba(255, 213, 0, 0.35)",
mixBlendMode: "multiply",
// add dark variant via inline media query: prefers-color-scheme or use a CSS class
```
Cleanest: replace inline styles with a CSS class `.annotation-highlight` + `.dark .annotation-highlight` so dark mode can use `screen`/`lighten` blend. Add the class to `web/src/app/globals.css` (or wherever app-wide highlight styles live — check `web/src/app/`).

### 3.6 `data-page-number` is read for the FIRST containing element only

Currently in tooltip line 82-87:
```ts
let n: Node | null = range.commonAncestorContainer;
while (n && n !== container) {
    if (n instanceof Element && n.hasAttribute("data-page-number")) { ... break; }
    n = n.parentElement;
}
```
If the selection spans **two** pages (rare but possible in two-page view), this only records the page of the start. That's acceptable; just confirm by ensuring the highlight renderer also picks the same page (`pdf-viewer.tsx:478` filters by `a.page === pageNum || a.page == null`). A null page falls back to all pages — fine.

---

## PR #4 — Highlight rendering robustness

### 4.1 Markdown viewer's `searchFrom = idx + 1` advances by one

`web/src/components/viewers/markdown-viewer.tsx:104` increments `searchFrom = idx + 1`. PDF viewer correctly uses `searchFrom = matchEnd` (line 119). Both should be `matchEnd` to avoid overlapping matches when the selection string is short. Change to:
```ts
searchFrom = matchEnd;
```

### 4.2 Hash-based memoization for highlights

In `pdf-viewer.tsx:369-372`, `allAnnotations` is recomputed every render via `useMemo` on `annotations` (reference identity). Because `threads` in the hook is a new array on every state update (after PR #2.3), this memo invalidates often. Cache on a stable signature instead:
```ts
const annotationsKey = annotations.map(t => `${t.root.id}:${t.root.selection_text ?? ""}:${t.root.page ?? "_"}`).join("|");
const allAnnotations = useMemo(() => annotations.map(t => ({ selection_text: t.root.selection_text, page: t.root.page })), [annotationsKey]);
```

### 4.3 Highlight recomputation on zoom change

`AnnotatedPage` recomputes via MutationObserver — but a pure zoom change re-renders the `<Page>` with a new `width` prop, which doesn't always trigger childList mutations in pdf.js's text layer (text layer is rebuilt; children are added — should fire). Verify in dev mode by zooming with annotations active. If highlights don't follow, add a `useEffect` watching the `width` prop to call `scheduleRecalc()`.

### 4.4 Search match crossing page boundary in markdown

`buildHighlights` in `markdown-viewer.tsx` builds `fullText` over the entire DOM; matches can span block boundaries (e.g. a list item across `<li>` siblings). Range API handles cross-element ranges fine — but `range.getClientRects()` may return huge full-width rects when the text crosses block boundaries with whitespace. Add a sanity check:
```ts
if (r.width > 0 && r.height > 0 && r.width < containerRect.width * 1.1) { highlights.push(...) }
```

---

## PR #5 — Test coverage gap

There are **no frontend tests** for annotations. The repo uses Vitest (`web/vitest.config.ts`). Add unit tests for:

### 5.1 `use-annotations.ts` hook
- `web/src/hooks/__tests__/use-annotations.test.ts`
- Mock `apiFetch` and `createSSEConnection`.
- Cases:
  - Initial fetch populates `threads`.
  - `createAnnotation` (root) prepends to threads and increments total.
  - `createAnnotation` (reply) appends to the matching thread.
  - SSE `annotation_created` event is idempotent vs local create.
  - `editAnnotation` mutates in place without refetching.
  - `deleteAnnotation` (root) removes thread and decrements total.
  - `deleteAnnotation` (reply) removes from thread.
  - `loadMore` follows cursor and concatenates.

### 5.2 Tooltip positioning
- `web/src/components/annotations/__tests__/annotation-selection-tooltip.test.tsx`
- Cases:
  - Renders nothing when no selection.
  - Captures `data-page-number` when present.
  - Closes on outside click but not on textarea click.

### 5.3 Backend tests still missing (add to `api/tests/test_annotations.py`)
- `test_list_orders_newest_first` (after PR #1.2).
- `test_cursor_pagination_desc` (after PR #1.2).
- `test_create_annotation_broadcasts_payload` (after PR #2.1) — connect to SSE in a background task, create an annotation, assert the SSE payload contains the annotation body.
- `test_edit_annotation_broadcasts_update` (after PR #2.1).
- `test_notification_failure_does_not_abort_create` (after PR #1.3) — monkeypatch `notify_user` to raise; assert annotation is still persisted.
- `test_soft_deleted_material_404` (after PR #1.1).

---

## Verification checklist (run before opening each PR)

1. **API**: `cd api && uv run pytest tests/test_annotations.py -v`
2. **Web typecheck**: `cd web && pnpm tsc --noEmit`
3. **Web lint**: `cd web && pnpm lint`
4. **Web tests**: `cd web && pnpm test`
5. **Manual**: start the stack (`docker compose up`), open a material with annotations, verify:
   - Creating a root annotation appears once (not duplicated).
   - Editing updates immediately (no flash of stale text).
   - Deleting removes from list without re-fetch.
   - Newest annotations appear at top.
   - Two-page PDF view filters annotations correctly per page.
   - Dark mode highlight color is visible (not black).

---

## What NOT to change

- Don't revert the WIP changes in `pdf-viewer.tsx` (range-based highlight rects). They're an improvement.
- Don't add visibility/ACL checks to annotation endpoints (out of scope per user).
- Don't change the threading model (root + replies via `thread_id`).
- Don't touch `migrations/versions/001_initial.py` — schema is fine.
- Don't add comments explaining WHAT the code does. The codebase convention is sparse comments — only add a comment when explaining non-obvious WHY (and the project memory rule about PATCH responses is one of those WHYs worth a one-line note in the new mutation handlers).
