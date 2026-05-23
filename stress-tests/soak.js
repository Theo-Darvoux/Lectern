// Soak test — sustained low-medium load for memory/leak detection (default 30m)
// Run: k6 run stress-tests/soak.js
// Short run: DURATION=5m k6 run stress-tests/soak.js
import http from "k6/http";
import { check, sleep } from "k6";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const DURATION = __ENV.DURATION || "30m";
const VUS      = parseInt(__ENV.VUS || "10");

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_failed:   ["rate<0.01"],
    http_req_duration: ["p(95)<3000"],
  },
};

export default function () {
  const health = http.get(`${BASE}/api/health`);
  check(health, { "healthy": (r) => r.status === 200 });

  const home = http.get(`${BASE}/api/home/`);
  check(home, { "home ok": (r) => r.status === 200 });

  const browse = http.get(`${BASE}/api/browse`);
  check(browse, { "browse ok": (r) => r.status === 200 });

  sleep(3);
}
