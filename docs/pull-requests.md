# Pull Request Workflow

The pull-request system is how all changes to materials and directories are proposed, reviewed, and applied. Direct edits are not possible, every mutation goes through a PR.

---

## Concepts

**PR** : a named set of operations on the wiki tree. A PR may create, update, or delete materials and directories in a single atomic transaction.

**Operation** : a single mutation within a PR. Each operation has a `type`, a `targetType` (material or directory), a `targetId`, and a `payload`. Operations within a PR are ordered and applied in sequence.

**Diff** : the computed delta between the current state and the state after the PR is applied. Used for review.

---

## Lifecycle

```
open ──► approved
  │
  ├──► rejected
  │
  └──► cancelled
         (by author)

approved ──► reverted  (creates a new PR that undoes it)
```

### States

| State | Meaning |
|---|---|
| `open` | Awaiting review |
| `approved` | Operations applied, materials live |
| `rejected` | Declined with reason |
| `cancelled` | Withdrawn by the author |
| `reverted` | Approved but subsequently undone |

---

## Operation types

| Type | Effect |
|---|---|
| `create_material` | Add a new file to a directory |
| `edit_material` | Modify title, description, tags, other metadata, or replace the file |
| `delete_material` | Mark a material for removal |
| `create_directory` | Add a subdirectory |
| `edit_directory` | Modify directory metadata |
| `delete_directory` | Remove a directory (recursively) |
| `move_item` | Move a material or directory to another parent directory |

### Moving items

In the file browser, use **Move** from an item's menu (or select several items
and click **Move**). A banner then stays at the top of every folder you open
and lists what you're moving. Open the destination folder and click
**Move here**. You can then add the move to your draft or apply it right away.

A pending move is shown in amber, both in the source folder and at its
destination. The item can still be edited while the move is pending: the edit
is staged next to the move, and both are applied together. Use **Move
elsewhere** to change the destination, which replaces the pending move. Use
**Cancel move** to drop it. Moving an item back into its original folder also
cancels the pending move.

---

## Drafts

Changes can be collected in a draft and submitted together as one contribution.
The draft lives in the browser (`localStorage`), not on the server, and belongs
to the signed-in account: signing out keeps it, and another account signing in
on the same browser clears it.

- An item has at most one pending edit and one pending move. Editing it again
  merges into the existing edit, and moving it again replaces the destination.
- Deleting an item replaces whatever was pending on it. Deleting something that
  only exists in the draft simply removes it, along with anything inside it.
- Staged files stay usable for 48 hours. An expired file blocks the submission
  until it is removed from the draft; nothing is removed automatically.
- A quiz saved to the draft is stored on the server like any staged file
  (`POST /api/qcm/stage`) and read back with `GET /api/qcm/staged?file_key=…`.
- A contribution that refers to a temporary id (`$…`) nothing in it creates is
  rejected when submitted.

---

## Roles and permissions

| Action | Required role |
|---|---|
| Create PR | Authenticated user |
| Cancel PR | Author only |
| Approve PR | `moderator` |
| Reject PR | `moderator` |
| Revert PR | `bureau` or `vieux` (admin) |

Reverting creates a new PR containing the inverse operations and auto-approves it immediately.

---

## Anonymous contributions

When submitting from the review drawer, the author can tick **Submit anonymously**
(`is_anonymous: true` on `POST /api/pull-requests`). The choice is fixed at creation.

- `author_id` is still stored, so quotas, cancellation and moderation work as usual.
- Moderators (`moderator`, `bureau`, `vieux`) see the author, with an "Anonymous" badge.
- Everyone else, the author included, gets `author: null` and `author_id: null` in every
  response: PR list and detail, home feed, profile contributions. The author is never sent
  to them, so it can't be read from the network tab. The author still gets `is_own: true`,
  which unlocks the cancel action.
- Anonymity carries over on approval. The materials and versions created get
  `is_anonymous = true`, their `author_id` is never serialized, and the search index drops
  `authorName`. Text edits are copied to `materials/anonymous/{upload_id}/…` instead of
  `materials/{user_id}/…`, so the storage key doesn't reveal the author either.
- For non-moderators, `GET /api/pull-requests?author_id=X` leaves out X's anonymous PRs,
  unless X is the person asking. The same goes for the profile contribution lists and counters.
- The `search` parameter of the PR list never matches an anonymous PR by its author's name,
  except for moderators and the author.
- Anonymous PRs don't count in reputation or on the leaderboard.
- Comments are unchanged: if the author comments on their own anonymous PR, the comment
  shows their name.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/pull-requests` | Create a PR |
| `GET` | `/api/pull-requests` | List PRs, paginated (`page`, `limit`; total in `X-Total-Count`). See [Listing filters](#listing-filters) |
| `GET` | `/api/pull-requests/counts` | Number of PRs per status, plus the caller's own |
| `GET` | `/api/pull-requests/{id}` | Fetch a single PR |
| `GET` | `/api/pull-requests/for-item` | PRs affecting a specific material or directory |
| `GET` | `/api/pull-requests/{id}/diff` | Compute the diff |
| `GET` | `/api/pull-requests/{id}/preview` | Preview state after applying operation N (`?opIndex=N`) |
| `POST` | `/api/pull-requests/{id}/approve` | Approve (moderator) |
| `POST` | `/api/pull-requests/{id}/reject` | Reject with reason (moderator) |
| `POST` | `/api/pull-requests/{id}/revert` | Revert (admin) |
| `POST` | `/api/pull-requests/{id}/cancel` | Cancel (author) |
| `GET` | `/api/pull-requests/{id}/comments` | List comments |
| `POST` | `/api/pull-requests/{id}/comments` | Add a comment (supports threading via `parent_id`) |
| `GET` | `/api/events/sse` | Master SSE stream for notifications, PRs, and selected entity topics |

### Listing filters

`GET /api/pull-requests` applies every filter to the whole list before paginating:

| Parameter | Values | Effect |
|---|---|---|
| `status` | `open`, `approved`, `rejected`, `cancelled` | Keep PRs in that state |
| `author_id` | user ID | Keep that user's PRs (see anonymity rules above) |
| `search` | text, up to 200 characters | Match the title, the author's display name, or the start of the PR ID (`#1a2b3c4d`) |
| `op_type` | `create_material`, `edit_material`, `delete_material`, `directory`, `move_item`, `revert` | Keep PRs containing that operation. `directory` covers every folder operation; `revert` covers revert PRs and the PRs they undid |
| `sort` | `newest` (default), `oldest`, `changes` | `changes` puts the PRs with the most operations first |

### File previews

Reviewers can preview supported files before approving an operation. Jupyter
notebooks (`.ipynb`) are shown as a read-only sequence of Markdown and code
cells with common text, error, Markdown, and raster-image outputs. The preview
never executes notebook code; active HTML and SVG outputs are not rendered.

---

## Real-time updates (SSE)

`GET /api/events/sse` is the only Server-Sent Events endpoint. The frontend elects one leader tab per browser, which holds one stream carrying the notification, PR, upload, material, and directory channels of all that browser's tabs. Each user may have one stream per browser (up to 8 per API process, so a laptop and a phone both receive live updates), and a reconnect from the same browser replaces its older stream. PR events use the `pull_requests` channel; entity interests are supplied as repeated `topic` query parameters, and topics that no longer exist are ignored. See [ADR 006](adr/006-single-sse-stream-per-browser.md).

| Event | Trigger |
|---|---|
| `pr_opened` | A PR is created |
| `pr_approved` | A PR is approved |
| `pr_rejected` | A PR is rejected |
| `pr_reverted` | A PR is reverted |
| `pr_cancelled` | A PR is cancelled |

---

## Auto-merge

If all uploads referenced by a PR reach `processing_status=complete` (post-scan compression finished), the PR is eligible for auto-merge. This prevents a PR from being blocked on background processing when the moderator approves before compression finishes.

---

## Approval transaction

When a moderator approves a PR, the operations are applied atomically inside a single database transaction:

1. `MaterialVersion` rows created for each added or updated material
2. `Material` records updated (title, description, directory, etc.)
3. CAS ref counts decremented for deleted materials
4. PR status set to `approved`
5. SSE event published to all watching users

If the transaction fails, the PR remains `open` and no partial changes are persisted.
