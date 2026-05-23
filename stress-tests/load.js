// Load test — realistic traffic ramp, read-only public endpoints
// Run: k6 run stress-tests/load.js
// Override base: BASE_URL=https://... k6 run stress-tests/load.js
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "https://intellect.clubcode.fr";

const browseTrend = new Trend("browse_duration");
const searchTrend = new Trend("search_duration");

export const options = {
  stages: [
    { duration: "30s", target: 10 },  // ramp up
    { duration: "2m",  target: 10 },  // steady
    { duration: "30s", target: 30 },  // peak
    { duration: "1m",  target: 30 },  // hold peak
    { duration: "30s", target: 0  },  // ramp down
  ],
  thresholds: {
    http_req_failed:   ["rate<0.05"],
    http_req_duration: ["p(95)<3000", "p(99)<5000"],
    browse_duration:   ["p(95)<3000"],
    search_duration:   ["p(95)<4000"],
  },
};

const SEARCH_QUERIES = ["math", "physique", "informatique", "chimie", "algo"];

export default function () {
  group("public pages", () => {
    // /api/home/ and /api/home/popular require auth; tell k6 that 401 is expected
    // so it doesn't count these as failures in http_req_failed.
    const homeParams = { responseCallback: http.expectedStatuses(200, 401) };
    const home = http.get(`${BASE}/api/home/`, homeParams);
    check(home, { "home responds": (r) => [200, 401].includes(r.status) });

    const popular = http.get(`${BASE}/api/home/popular`, homeParams);
    check(popular, { "popular responds": (r) => [200, 401].includes(r.status) });
    sleep(0.5);

    const browse = http.get(`${BASE}/api/browse`);
    check(browse, { "browse ok": (r) => r.status === 200 });
    browseTrend.add(browse.timings.duration);
    sleep(0.5);
  });

  group("auth discovery", () => {
    const methods = http.get(`${BASE}/api/auth/methods`);
    check(methods, { "auth/methods ok": (r) => r.status === 200 });
  });

  group("search", () => {
    const q = SEARCH_QUERIES[Math.floor(Math.random() * SEARCH_QUERIES.length)];
    const res = http.get(
      `${BASE}/api/search?q=${encodeURIComponent(q)}`,
      { responseCallback: http.expectedStatuses(200, 401, 403) },
    );
    check(res, { "search responds": (r) => [200, 401, 403].includes(r.status) });
    searchTrend.add(res.timings.duration);
    sleep(1);
  });

  sleep(Math.random() * 2 + 1);
}
