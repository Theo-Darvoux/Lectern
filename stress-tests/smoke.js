// Smoke test — quick sanity check, 1 VU, 30s
// Run: k6 run stress-tests/smoke.js
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "https://intellect.clubcode.fr";

export const options = {
  vus: 1,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<2000"],
  },
};

export default function () {
  const health = http.get(`${BASE}/api/health`);
  check(health, { "health 200": (r) => r.status === 200 });

  // /api/home/ requires auth; tell k6 that 401 is expected (not a failure)
  const home = http.get(`${BASE}/api/home/`, {
    responseCallback: http.expectedStatuses(200, 401),
  });
  check(home, { "home responds": (r) => [200, 401].includes(r.status) });

  const browse = http.get(`${BASE}/api/browse`);
  check(browse, { "browse 200": (r) => r.status === 200 });

  const methods = http.get(`${BASE}/api/auth/methods`);
  check(methods, { "auth/methods 200": (r) => r.status === 200 });

  sleep(1);
}
