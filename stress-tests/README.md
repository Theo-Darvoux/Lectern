# Stress Tests

k6 stress test suite for WikINT / intellect.clubcode.fr.

## Quick reference

### Generic templates (`stress-tests/`)

| Script | Purpose | Duration | Auth |
|---|---|---|---|
| `smoke.js` | Sanity check — is prod alive? | 30s, 1 VU | No |
| `load.js` | Realistic traffic with ramp | ~5m | No |
| `spike.js` | Sudden burst to find breaking point | ~2m | No |
| `soak.js` | Memory/leak detection, sustained load | 30m (configurable) | No |
| `authenticated.js` | Auth-gated endpoints | ~3m | Yes |

### Behaviour scenarios (`stress-tests/scenarios/`)

Each scenario models a specific user behaviour with realistic pacing.

| Script | What it simulates | Peak VUs (default) | Auth |
|---|---|---|---|
| `folder-navigation.js` | Users rapidly clicking through the folder tree | 30 | No |
| `material-viewer.js` | Opening a document, recording a view, reading comments, liking | 25 | Yes |
| `comment-storm.js` | Many users posting comments, hitting the rate limiter | 20 | Yes |
| `home-feed.js` | Personalised home page (5 heavy DB queries per request) | 40 | Yes |
| `auth-surge.js` | Login + token refresh burst (bcrypt CPU stress) | 20+30 | Optional |
| `combined.js` | All of the above running concurrently with realistic weights | ~60 | Optional |

## Running

```bash
# Smoke
k6 run stress-tests/smoke.js

# Folder navigation (no auth)
k6 run stress-tests/scenarios/folder-navigation.js

# Scale up
PEAK_VUS=60 k6 run stress-tests/scenarios/folder-navigation.js

# Authenticated scenarios — two ways:

# Option 1: log in at test start (credentials passed as env vars)
TEST_EMAIL=you@example.com TEST_PASSWORD=secret k6 run stress-tests/scenarios/home-feed.js

# Option 2: pass a pre-obtained JWT (grab from DevTools → Network → Authorization header)
TOKEN=eyJ... k6 run stress-tests/scenarios/material-viewer.js

# Combined realistic mix (unauthenticated only)
k6 run stress-tests/scenarios/combined.js

# Combined with auth
TOKEN=eyJ... k6 run stress-tests/scenarios/combined.js

# Scale combined to 2× (doubles all VU counts)
TOKEN=eyJ... SCALE=2 k6 run stress-tests/scenarios/combined.js
```

## Auth surge notes

`auth-surge.js` runs two parallel sub-scenarios:
- `login_storm` — repeated bcrypt-heavy logins (CPU-bound)
- `refresh_storm` — repeated token refreshes (Redis + JWT verify only)

If `TEST_EMAIL` / `TEST_PASSWORD` are not set, it degrades to auth method discovery only.

## Comment storm notes

The API rate-limits comment creation to **10/minute per IP**. Under load all k6
VUs share the same source IP, so 429s will appear quickly. The script tracks
them as expected (`http.expectedStatuses(201, 429)`) and backs off. The goal
is verifying the server stays stable (no 500s), not that all comments post.

## Output formats

```bash
# JSON for post-processing
k6 run --out json=results.json stress-tests/scenarios/combined.js

# CSV
k6 run --out csv=results.csv stress-tests/scenarios/combined.js
```
