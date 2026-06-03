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
| `update_material` | Modify title, description, tags, or other metadata |
| `delete_material` | Mark a material for removal |
| `create_directory` | Add a subdirectory |
| `update_directory` | Modify directory metadata |
| `delete_directory` | Remove a directory (recursively) |

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

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/pull-requests` | Create a PR |
| `GET` | `/api/pull-requests` | List PRs (filter by status, author, type) |
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
| `GET` | `/api/pull-requests/sse` | SSE stream for real-time PR state changes |

---

## Real-time updates (SSE)

`GET /api/pull-requests/sse` opens a Server-Sent Events stream per authenticated user. Events are published when any PR the user is involved in changes state:

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
