// Authenticated load test
//
// Option 1 — login at test start (recommended):
//   TEST_EMAIL=you@example.com TEST_PASSWORD=secret k6 run stress-tests/authenticated.js
//
// Option 2 — pass a pre-obtained bearer token:
//   TOKEN=<jwt> k6 run stress-tests/authenticated.js
//
// The script logs in once in setup(), shares the token with all VUs.
import http from "k6/http";
import { check, group, sleep, fail } from "k6";

const BASE     = __ENV.BASE_URL  || "https://intellect.clubcode.fr";
const EMAIL    = __ENV.TEST_EMAIL;
const PASSWORD = __ENV.TEST_PASSWORD;
const TOKEN    = __ENV.TOKEN;

export const options = {
  stages: [
    { duration: "30s", target: 5  },
    { duration: "2m",  target: 20 },
    { duration: "30s", target: 0  },
  ],
  thresholds: {
    http_req_failed:   ["rate<0.02"],
    http_req_duration: ["p(95)<3000"],
  },
};

// setup() runs once before VUs start; its return value is passed to each VU.
export function setup() {
  if (TOKEN) {
    console.log("Using pre-provided TOKEN");
    return { token: TOKEN };
  }
  if (!EMAIL || !PASSWORD) {
    fail("Set TEST_EMAIL + TEST_PASSWORD (or TOKEN) env vars");
  }

  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );

  if (res.status !== 200) {
    fail(`Login failed (${res.status}): ${res.body}`);
  }

  const token = res.json("access_token");
  if (!token) {
    fail("Login response missing access_token");
  }
  console.log("Login successful, token obtained");
  return { token };
}

export default function (data) {
  const headers = {
    Authorization: `Bearer ${data.token}`,
    "Content-Type": "application/json",
  };

  group("home feed", () => {
    const home = http.get(`${BASE}/api/home/`, { headers });
    check(home, { "home 200": (r) => r.status === 200 });

    const popular = http.get(`${BASE}/api/home/popular`, { headers });
    check(popular, { "popular 200": (r) => r.status === 200 });
    sleep(0.5);
  });

  group("browse + search", () => {
    const browse = http.get(`${BASE}/api/browse`, { headers });
    check(browse, { "browse 200": (r) => r.status === 200 });

    const search = http.get(`${BASE}/api/search?q=math`, { headers });
    check(search, { "search 200": (r) => r.status === 200 });
    sleep(1);
  });

  group("notifications", () => {
    const notifs = http.get(`${BASE}/api/notifications`, { headers });
    check(notifs, { "notifications ok": (r) => [200, 404].includes(r.status) });
  });

  sleep(Math.random() * 2 + 1);
}
