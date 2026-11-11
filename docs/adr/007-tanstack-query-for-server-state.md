# ADR 007 — TanStack Query for server state

## Status

Accepted

## Context

The web app had no data-fetching library. Each component kept its own `loading`,
`error` and data state, cancelled stale requests by hand (`let active = true`), and
some kept private module-level caches. This caused visible bugs:

- a failed author lookup was cached as "Unknown" until a full page reload;
- switching documents quickly could show the previous document's attachments;
- a failed request was often shown as an empty list (`.catch(() => setX([]))`),
  so "no data" and "could not load" looked the same.

Components that showed the same resource (a pull request, a user, the home feed)
also fetched it separately.

## Decision

1. Server state is read with `@tanstack/react-query`. Zustand stays for client
   state (auth session, config, UI preferences).
2. One `QueryClient` per browser, created in `web/src/lib/query-client.ts` and
   provided by `QueryProvider`. Code outside React (hover prefetch, live-update
   invalidation) reaches it through `getQueryClient()`.
3. Retries: at most 2, and only for errors that `isRetriableError` accepts
   (network failures and 5xx). `apiFetch` already refreshes expired tokens.
   `refetchOnWindowFocus` is off; live updates come from the SSE stream (ADR 006).
4. The cache is cleared when the signed-in user changes, so one account never
   sees another account's responses.
5. Query keys start with the resource name, so one `invalidateQueries` call
   refreshes every view of it: `["pull-requests", …]`, `["users", id, …]`,
   `["browse", path]`, `["home", …]`, `["leaderboard", …]`.
6. A failed query renders `<QueryError>` with a Retry button, never an empty state.
7. Tests wrap components with `withQueryClient` (`web/src/test/query.tsx`), which
   disables retries.

## Consequences

- Stale responses can no longer overwrite newer ones, and errors are not cached.
- Views of the same resource share one request and one cache entry.
- New screens should use `useQuery` / `useMutation` rather than `useEffect` +
  `useState` fetching. Some staff pages still use the old pattern and are
  migrated as they are touched.
