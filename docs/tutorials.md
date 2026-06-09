# In-App Tutorials

Lectern ships interactive, role-aware guided tours that teach users how to
browse, contribute, build QCMs, review, and moderate. They use a small in-house
spotlight engine — no third-party tour library.

For the original design rationale and decisions, see
[`tutorials-plan.md`](./tutorials-plan.md).

## How it works

- **Engine** lives in `web/src/lib/tutorials/` and
  `web/src/components/tutorials/`. `TutorialProvider` is mounted globally in
  `client-providers.tsx`; it renders the overlay and auto-launches tours.
- **Spotlight overlay** dims the page, cuts an animated highlight around the
  current step's target element, and shows a tooltip card with progress and
  Back / Next / Skip controls. Steps without a target render a centered card.
- **Anchoring**: steps point at `[data-tutorial="<key>"]` attributes placed on
  real UI elements. The engine resolves the first *visible* match (so duplicated
  mobile/desktop variants both work) and **skips any step whose target is
  absent** — e.g. a nav item the current role can't see. This lets one tutorial
  contain role-specific steps.
- **Triggers**: a tour auto-launches the first time a qualifying user lands on
  its `autoStartOn` route (once per session per tutorial), and any tour can be
  replayed from the **Help center** (`/help`, also linked in the profile menu).

## Persistence & roles

- **Completion** is stored server-side for logged-in users
  (`users.completed_tutorials`, a JSON column) via
  `POST /api/users/me/tutorials/complete` and reset via
  `DELETE /api/users/me/tutorials`. Guests have no profile, so their completion
  lives in `localStorage`. `useTutorial()` hides this split behind one API.
- **Gating** uses capability tiers (`web/src/lib/tutorials/types.ts`):
  `guest → student → staff (moderator) → admin (bureau/vieux)`. A tutorial's
  `minTier` is shown to that tier and every more-privileged one.

## Kill-switch

Set `NEXT_PUBLIC_TUTORIALS=off` (build-time) to disable the feature entirely —
no auto-launch and the Help center is hidden. See
[`environment-variables.md`](./environment-variables.md).

## Adding a new tutorial

1. **Anchor the UI**: add `data-tutorial="my-thing"` to the relevant elements.
2. **Define the tutorial** in `web/src/lib/tutorials/registry.ts` — give it a
   kebab-case `id`, a `minTier`, an `icon` (must exist in
   `components/tutorials/tutorial-icons.tsx`), the `steps`, and optionally an
   `autoStartOn` route. To avoid stacking auto-tours, keep at most one per route.
3. **Add translations** under the `Tutorials.<id>` namespace in all four message
   files (`web/messages/{en,fr}.json` **and** `web/public/messages/{en,fr}.json`):
   `title`, `description`, and `steps.<stepId>.{title,body}`.
4. **Allowlist the id** server-side if you tightened validation (the id pattern
   is enforced in `api/app/schemas/user.py`).
5. **Verify**: `pnpm test src/lib/tutorials/registry.test.ts` (checks ids,
   targets and that every step has en/fr strings) and `pnpm i18n:check`.

> Avoid naming a step `title` — the i18n duplicate-key linter trips on a step key
> that collides with the surrounding `title` field. Use `name` instead.

## Status

Shipped: `welcome`, `browse`, `upload`, `contribute`, `qcm`, `annotations`,
`review-pr`, `moderation`.

**TODO (deferred):** an `admin-config` tutorial for the admin area
(`/admin/*`) and an `account` tutorial for profile/settings. The engine and
gating (`minTier: "admin"`) already support them; they just need anchors,
registry entries, and i18n.
