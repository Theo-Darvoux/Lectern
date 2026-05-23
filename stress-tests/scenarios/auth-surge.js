// Scenario: login + token refresh storm
//
// Simulates a burst of authentications — e.g. after a class ends and everyone
// opens the app at once. Tests JWT issuance throughput, Redis session writes,
// and the bcrypt/password-check CPU cost under concurrency.
//
// Two sub-scenarios run in parallel via k6 scenarios:
//   - "login_storm":   repeated POST /api/auth/login (CPU-bound: bcrypt)
//   - "refresh_storm": repeated POST /api/auth/refresh (fast: JWT verify only)
//
// Run: TEST_EMAIL=x TEST_PASSWORD=y k6 run stress-tests/scenarios/auth-surge.js
//
// Note: if classic_auth is disabled on prod, login will return 400 — the script
// tracks this as expected and measures the rejection speed.
import http from "k6/http";
import { check, sleep, fail } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const EMAIL    = __ENV.TEST_EMAIL;
const PASSWORD = __ENV.TEST_PASSWORD;

const loginOk      = new Counter("login_ok");
const loginFailed  = new Counter("login_failed");
const refreshOk    = new Counter("refresh_ok");
const loginTrend   = new Trend("login_duration");
const refreshTrend = new Trend("refresh_duration");

export const options = {
  scenarios: {
    login_storm: {
      executor:        "ramping-vus",
      startVUs:        0,
      stages: [
        { duration: "15s", target: 10 },
        { duration: "45s", target: 20 },
        { duration: "15s", target: 0  },
      ],
      gracefulRampDown: "10s",
      exec:            "doLogin",
    },
    refresh_storm: {
      executor:        "ramping-vus",
      startVUs:        0,
      stages: [
        { duration: "10s", target: 0  },  // wait for logins to establish sessions
        { duration: "10s", target: 15 },
        { duration: "45s", target: 30 },
        { duration: "10s", target: 0  },
      ],
      gracefulRampDown: "10s",
      exec:            "doRefresh",
    },
  },
  thresholds: {
    // Login is bcrypt-heavy; 5s p(95) is a reasonable ceiling
    login_duration:   ["p(95)<5000"],
    // Refresh is just JWT verify + Redis read; should be fast
    refresh_duration: ["p(95)<1000"],
    http_req_failed:  ["rate<0.05"],
  },
};

// Global token store — populated by the first successful login so
// refresh VUs have something to refresh.
let sharedRefreshToken = null;

export function doLogin() {
  if (!EMAIL || !PASSWORD) {
    // No creds: test auth/methods discovery only (still measures the round-trip)
    const r = http.get(`${BASE}/api/auth/methods`);
    check(r, { "methods 200": (r) => r.status === 200 });
    sleep(2);
    return;
  }

  const start = Date.now();
  const r = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    {
      headers: { "Content-Type": "application/json" },
      // 200 success, 400 (classic auth disabled), 401 (wrong creds) all expected
      responseCallback: http.expectedStatuses(200, 400, 401),
    },
  );
  loginTrend.add(Date.now() - start);

  if (r.status === 200) {
    loginOk.add(1);
    check(r, { "login ok": (r) => r.status === 200 });
    // Store cookies for refresh scenario (k6 shares cookie jar per VU only,
    // so we extract the access_token for the shared pool).
    const body = r.json();
    if (body && body.access_token && !sharedRefreshToken) {
      sharedRefreshToken = body.access_token;
    }
  } else {
    loginFailed.add(1);
    check(r, { "auth rejected cleanly": (r) => [400, 401].includes(r.status) });
  }

  // Users don't login every second — simulate realistic inter-login gap
  sleep(Math.random() * 3 + 2);
}

export function doRefresh() {
  // The refresh endpoint requires the HttpOnly cookie set by login.
  // Since k6 VUs don't share cookie jars, we simulate via the Authorization
  // header fallback: pass the access token and expect either 200 (refreshed)
  // or 401 (token expired/missing).
  const headers = sharedRefreshToken
    ? { Authorization: `Bearer ${sharedRefreshToken}`, "x-client-id": "stress-test" }
    : { "x-client-id": "stress-test" };

  const start = Date.now();
  const r = http.post(`${BASE}/api/auth/refresh`, null, {
    headers,
    responseCallback: http.expectedStatuses(200, 401, 422),
  });
  refreshTrend.add(Date.now() - start);

  if (r.status === 200) {
    refreshOk.add(1);
    check(r, { "refresh ok": (r) => r.status === 200 });
  } else {
    // 401/422 expected when cookie not present in k6 VU context
    check(r, { "refresh rejected cleanly": (r) => [401, 422].includes(r.status) });
  }

  sleep(Math.random() * 2 + 1);
}
