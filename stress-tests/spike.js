// Spike test — sudden burst to find breaking point
// Run: k6 run stress-tests/spike.js
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "https://intellect.clubcode.fr";

export const options = {
  stages: [
    { duration: "10s", target: 5   },  // baseline
    { duration: "5s",  target: 100 },  // instant spike
    { duration: "1m",  target: 100 },  // hold spike
    { duration: "5s",  target: 5   },  // drop back
    { duration: "30s", target: 5   },  // recovery watch
  ],
  thresholds: {
    http_req_failed:   ["rate<0.10"],
    http_req_duration: ["p(95)<5000"],
  },
};

export default function () {
  const r = http.get(`${BASE}/api/health`);
  check(r, { "alive": (r) => r.status === 200 });

  const browse = http.get(`${BASE}/api/browse`);
  check(browse, { "browse ok": (r) => r.status < 500 });

  sleep(0.5);
}
