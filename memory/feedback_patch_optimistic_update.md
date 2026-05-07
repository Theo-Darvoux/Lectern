---
name: PATCH endpoints must update state immediately from response
description: After any PATCH/PUT, capture the response and update local state directly — never re-fetch
type: feedback
---

After a PATCH call, always capture the response and call `setProfile`/`setUser` immediately with the returned data. Do NOT trigger a re-fetch (`fetchProfile()`) as the callback — this causes visible stale state until the second request resolves.

**Why:** This bug has recurred multiple times. The edit profile form was discarding the PATCH response and calling `fetchProfile()` instead, leaving the UI showing old data until the extra GET completed.

**How to apply:** In any mutation handler (PATCH/PUT/POST that returns updated data): `const updated = await apiFetch<T>(...); setState(prev => ({ ...prev, ...updated }));`. Only call a re-fetch if you genuinely need data the PATCH response doesn't return (e.g., server-computed fields like stats).
