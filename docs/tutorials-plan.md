# In-App Tutorials — Implementation Plan

Status: **proposed** · Author: assistant · Date: 2026-06-08

Interactive, role-aware, step-by-step product tutorials (spotlight coachmarks +
guided tooltips) that teach users how to use Lectern. Built with a custom
lightweight engine (no new dependency), launched **auto on first visit** and
replayable from a **Help / Learn center**, with completion tracked **server-side**
on the user profile.

---

## 1. Decisions (locked)

| Question | Decision |
|---|---|
| Engine | **Custom lightweight engine** (Radix + Tailwind, no new dep) |
| Trigger | **Auto on first visit** + **Help / Learn center** (replay on demand) |
| Persistence | **Server-side** for logged-in users; **localStorage** for guests |
| Role scope | **Role-aware** — steps reference only UI the user can actually see |
| Help entry | **Profile-dropdown item + `/help` page** (no navbar `?` icon) |
| Kill-switch | **`NEXT_PUBLIC_TUTORIALS`** env flag (build-time) |
| Step visuals | **Text + Lucide icons only**; interactive screen highlighting |
| Admin tutorial | **Deferred** (kept in TODO, see §2.D) |

Roles in the system (`api/app/models/user.py`, `web/src/lib/guest.ts`):
`pending`, `student`, `moderator`, `bureau`, `vieux`, `guest`.
- **Guest** (`role === "guest"`): read-only browsing.
- **Student**: browse + contribute (PRs), QCM, annotations, uploads.
- **Staff** (`moderator | bureau | vieux`): everything + review/moderation.
- **Admin**: staff + admin config. (Admin is surfaced via permissions, not a
  distinct role enum value — confirm gating against existing admin guards.)

---

## 2. Inventory — what needs a tutorial

Derived from `web/src/app/` routes and feature components. Each tutorial is a
named flow. `min role` = lowest role that should see it.

### A. Core (everyone)
1. **Welcome / Orientation** (`welcome`) — min: guest
   - Navbar tour: site logo/home, search, notifications, inbox, profile menu, theme.
   - Sidebar / directory tree, breadcrumb, view-mode toggle.
   - Where the Help center lives. Anchors: `navbar.tsx`, `sidebar/`, `layout-shell.tsx`.
2. **Browsing files** (`browse`) — min: guest
   - Navigate the directory tree (course → chapter), open a directory.
   - Material cards: icons, metadata, like/favourite, download.
   - Opening a material → the viewer; switching versions; fullscreen; print.
   - Search (modal + inline), filters. Anchors: `app/browse/[[...path]]`,
     `components/browse/`, `components/viewers/`, `components/search/`.
3. **Annotations** (`annotations`) — min: student
   - Add/anchor an annotation on a material, reply, resolve.
   - Anchors: `components/annotations/`, `hooks/use-annotations.ts`.

### B. Contributing (student+)
4. **Uploading material** (`upload`) — min: student
   - Drag-and-drop / picker, the staging area, resumable upload, CAS dedup,
     supported types. Anchors: `lib/upload-client.ts`, `hooks/use-upload-engine.ts`,
     `lib/staging-store.ts`, `components/browse/` drop zones, `lib/drop-zone-store.ts`.
5. **Contributing via Pull Requests** (`contribute`) — min: student
   - What a PR is, propose a change/new file, add description, submit.
   - Track status, respond to review comments. Anchors: `app/pull-requests`,
     `app/pull-requests/[id]`, `components/pr/`, `lib/pr-client.ts`.
6. **Making a QCM** (`qcm`) — min: student
   - Create a QCM (`qcm/new`), the editor (`qcm/[materialId]/edit`): add
     questions/answers, markdown + LaTeX (KaTeX), images (self-contained embed),
     preview (`qcm/preview`), PDF export, limits. Anchors: `app/qcm/*`,
     `components/qcm/`, `lib/qcm-*`.

### C. Review & moderation (staff+)
7. **Reviewing pull requests** (`review-pr`) — min: moderator
   - Review queue, diff/preview per op, approve/request-changes/merge, comments.
   - Anchors: `app/moderator/pull-requests`, `app/admin/pull-requests`,
     `app/pull-requests/[id]/preview`.
8. **Moderation tools** (`moderation`) — min: moderator
   - Flags queue, featured content, directory management.
   - Anchors: `app/moderator/{flags,featured,directories}`.

### D. Admin (admin only)
9. **Admin configuration** (`admin-config`) — min: admin
   - Config tabs (branding/wordmark, etc.), users, featured, directories, flags,
     backup, DLQ. Anchors: `app/admin/*`.
   - (Lower priority — admins are few; ship after A–C.)

### E. Account
10. **Profile & settings** (`account`) — min: student
    - Edit profile, academic year, notifications, About/system info, replay
      tutorials. Anchors: `app/profile`, `app/settings`, `app/notifications`.

**Build priority:** Phase 1 = engine + `welcome`, `browse`. Phase 2 =
`contribute`, `qcm`, `upload`, `annotations`. Phase 3 = `review-pr`,
`moderation`. Phase 4 = `admin-config`, `account` + polish.

---

## 3. Architecture — the custom engine

### 3.1 Concept

A **driver-style spotlight tour**: dims the page, cuts a highlight "hole" around
the current step's target element, and shows a tooltip/card anchored to it with
title, body, illustration/icon, progress, and Back/Next/Skip. Steps without a
DOM target render as a centered modal card (intro/outro steps).

### 3.2 Files (frontend)

```
web/src/lib/tutorials/
  types.ts            # Step, Tutorial, TutorialId, TutorialContext types
  registry.ts         # all Tutorial definitions (id, minRole, route, steps[])
  tutorial-store.ts   # Zustand: active tutorial, step index, start/next/prev/stop
  use-tutorial.ts     # hook: completion state (from user), launch, gating by role
web/src/components/tutorials/
  tutorial-overlay.tsx    # the spotlight + tooltip renderer (portal)
  tutorial-spotlight.tsx  # SVG/box mask highlight around target rect
  tutorial-tooltip.tsx    # the step card (Radix Popover/Floating positioning)
  tutorial-provider.tsx   # mounts overlay globally; handles auto-launch
  help-center.tsx         # dialog/page listing all tutorials with replay/progress
  help-button.tsx         # navbar entry point (?-icon) opening the Help center
```

Mount `<TutorialProvider>` inside `client-providers.tsx` (inside
`ConfigProvider`/`LayoutShell`, so it has auth + config). Overlay renders via a
React portal above app chrome.

### 3.3 Step model

```ts
type TutorialStep = {
  id: string;
  target?: string;          // CSS selector, usually [data-tutorial="..."]
  title: string;            // i18n key
  body: string;             // i18n key (supports rich text)
  placement?: "top"|"bottom"|"left"|"right"|"center";
  route?: string;           // navigate here before showing (e.g. "/browse")
  spotlightPadding?: number;
  action?: "click"|"none";  // optional: advance when target clicked
  waitForSelector?: boolean;// poll until target exists (async-rendered UI)
  disableInteraction?: boolean;
};

type Tutorial = {
  id: TutorialId;
  minRole: Role;            // gating
  titleKey: string; descriptionKey: string; icon: string;
  steps: TutorialStep[];
  autoStartOn?: string;     // route pattern that triggers first-visit auto-launch
};
```

### 3.4 Anchoring strategy

- Add `data-tutorial="<key>"` attributes to the real UI elements (navbar items,
  upload zone, QCM "add question" button, PR submit, etc.). This decouples the
  tour from class names and is resilient to refactors.
- Engine resolves `target` via `document.querySelector`, measures
  `getBoundingClientRect()`, recomputes on scroll/resize (ResizeObserver +
  scroll listener), and scrolls the target into view before highlighting.
- `waitForSelector` polls (rAF, capped ~3s) for async-rendered targets; if not
  found, gracefully skip the step (log in dev).

### 3.5 Behavior / UX details (modern, professional)

- Smooth animated transitions of the spotlight rect between steps
  (`tw-animate-css` / CSS transitions already available).
- Backdrop dim with blur; highlighted target stays crisp and (optionally)
  clickable for `action: "click"` steps.
- Keyboard: → / Enter = next, ← = back, Esc = skip/close. Focus trap on tooltip.
- Progress dots + "Step n / N". "Skip tour" always available; "Don't show again"
  on auto-launched tours.
- Fully responsive: on mobile, tooltip docks to bottom sheet (reuse `vaul`),
  spotlight still highlights the target.
- Respect `prefers-reduced-motion`.
- A11y: ARIA roles (dialog), `aria-live` for step changes, focus management.
- Dark-mode aware (uses existing theme tokens).

### 3.6 Auto-launch logic

On route change, `TutorialProvider` checks: is there a tutorial whose
`autoStartOn` matches the current path, the user's role qualifies, and it's not
in `completed_tutorials`? If so, launch after a short delay (let page settle).
Never auto-launch for guests on first paint before they've interacted —
debounce, and never interrupt an in-progress action.

---

## 4. Backend changes (server-side persistence)

### 4.1 Model — `api/app/models/user.py`

Add:
```python
completed_tutorials: Mapped[list[str]] = mapped_column(
    JSON, default=list, server_default="[]", nullable=False
)
```
(Use `sqlalchemy.JSON`; Postgres → JSONB is fine via `JSON`. Confirm import.)

### 4.2 Migration

`uv run alembic revision --autogenerate -m "add completed_tutorials to users"`
then verify + `alembic upgrade head` against test DB. Provide a non-null default
`[]` so existing rows backfill.

### 4.3 Schema — `api/app/schemas/user.py`

- Add `completed_tutorials: list[str] = []` to `UserOut` (so `/users/me` returns it).
- New input model:
```python
class TutorialCompleteIn(BaseModel):
    tutorial_id: str = Field(..., max_length=64)
```

### 4.4 Endpoints — `api/app/routers/users.py` + service

- `POST /users/me/tutorials/{tutorial_id}/complete` → marks done (idempotent,
  appends if absent). Returns `UserOut`.
- `DELETE /users/me/tutorials` → reset all (so "replay all"/testing works).
  Optionally `DELETE /users/me/tutorials/{id}` to reset one.
- Business logic in `api/app/services/users.py` (no HTTP coupling). Validate
  `tutorial_id` against a known set (keep a constant list mirrored from the
  frontend registry, or accept any short string — prefer a server-side allowlist
  to avoid junk).

### 4.5 Frontend client

- Regenerate types: `pnpm generate-api-types` (API running).
- `tutorial-store` / `use-tutorial` calls `apiFetch` to mark complete; optimistic
  update of `useAuthStore.user.completed_tutorials`.

---

## 5. i18n

- All strings via `next-intl`. New namespace `Tutorials` in
  `web/messages/{en,fr}.json` **and** `web/public/messages/{en,fr}.json`
  (both are kept in sync in this repo).
- Structure: `Tutorials.<tutorialId>.title`, `.description`,
  `.steps.<stepId>.title` / `.body`, plus shared `Tutorials.controls.*`
  (next/back/skip/done/step), `Tutorials.helpCenter.*`.
- Run `pnpm i18n:check` before committing.

---

## 6. Help / Learn center

- `help-button.tsx`: a `?` icon in the navbar (and/or in the profile dropdown +
  a `/help` route for deep-linking).
- `help-center.tsx`: dialog listing all tutorials available to the user's role,
  each with icon, title, description, completion checkmark, and "Start"/"Replay".
- "Reset all tutorials" action (calls the DELETE endpoint) for users who want to
  re-see auto-launches. Also link this from `app/settings` About section.

---

## 7. Testing

- **Engine unit tests** (vitest, `web/vitest.config.ts`, no testing-library —
  follow existing test style): step navigation, role gating, completion
  filtering, target-resolution fallback/skip.
- **Registry validation test**: every step's i18n keys exist; every `target`
  selector is a `data-tutorial` key documented; `minRole` valid.
- **API tests** (pytest): complete endpoint is idempotent, reset works,
  `/me` returns `completed_tutorials`, unknown id rejected (if allowlisted).
- Manual: run through each tutorial per role (guest/student/staff/admin).

---

## 8. Docs

- Add `docs/tutorials.md` (operator/contributor guide: how tutorials work, how to
  add a new one, the `data-tutorial` convention).
- Update `docs/environment-variables.md` only if any toggle env var is added
  (none planned — feature is always on; could add `NEXT_PUBLIC_TUTORIALS=off`
  kill-switch if desired — **open question**).

---

## 9. Resolved decisions

1. **Help entry point:** profile-dropdown item + `/help` page (no navbar `?` icon).
2. **Guests:** get tours; completion persisted in **localStorage** (no server
   profile). Logged-in users persist server-side. `use-tutorial` abstracts the
   two behind one interface.
3. **Kill-switch:** add `NEXT_PUBLIC_TUTORIALS` (build-time, `NEXT_PUBLIC_*`
   are GitHub Actions repo vars per env-wiring model). `off`/`false` disables.
4. **Step visuals:** text + Lucide icons only, **interactive** with on-screen
   highlighting (spotlight on real elements, click-through `action` steps).
5. **Admin tutorial:** deferred — kept as TODO in §2.D / rollout Phase 6.
6. **"Don't show again":** per-tutorial (skipping an auto-tour marks it done) +
   a global "reset all tutorials" in Help center / settings.

---

## 10. Rollout order (PR-sized chunks)

1. Backend: model + migration + schema + endpoints + tests.
2. Engine: store, overlay, spotlight, tooltip, provider, types — with
   `welcome` + `browse` tutorials and `data-tutorial` anchors in navbar/sidebar/browse.
3. Help center + navbar button + settings link + i18n.
4. `contribute`, `qcm`, `upload`, `annotations` (+ anchors).
5. `review-pr`, `moderation` (+ anchors).
6. `admin-config`, `account`, polish, docs.
