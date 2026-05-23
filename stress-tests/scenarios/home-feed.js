// Scenario: many users loading their personalised home page simultaneously
//
// /api/home/ is the most expensive endpoint: it runs 5 separate DB queries
// (popular_today, popular_14d, featured, recent_prs, recent_favourites) in a
// single request. This scenario isolates that load and measures how the DB
// holds up when many authenticated users all hit the home feed at once.
//
// Run: TOKEN=<jwt> k6 run stress-tests/scenarios/home-feed.js
// Or:  TEST_EMAIL=x TEST_PASSWORD=y k6 run stress-tests/scenarios/home-feed.js
import http from "k6/http";
import { check, group, sleep, fail } from "k6";
import { Trend } from "k6/metrics";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const PEAK_VUS = parseInt(__ENV.PEAK_VUS || "40");

const homeTrend    = new Trend("home_feed_duration");
const popularTrend = new Trend("popular_duration");

export const options = {
  stages: [
    { duration: "20s", target: 10 },
    { duration: "30s", target: PEAK_VUS },
    { duration: "2m",  target: PEAK_VUS },
    { duration: "20s", target: 0 },
  ],
  thresholds: {
    http_req_failed:    ["rate<0.02"],
    http_req_duration:  ["p(95)<5000", "p(99)<10000"],
    home_feed_duration: ["p(95)<5000"],
    popular_duration:   ["p(95)<3000"],
  },
};

export function setup() {
  const token = __ENV.TOKEN;
  if (token) return { token };

  const email    = __ENV.TEST_EMAIL;
  const password = __ENV.TEST_PASSWORD;
  if (!email || !password) {
    fail("Provide TOKEN or TEST_EMAIL + TEST_PASSWORD");
  }

  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email, password }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) fail(`Login failed (${res.status}): ${res.body}`);
  return { token: res.json("access_token") };
}

export default function (data) {
  const h = { Authorization: `Bearer ${data.token}` };

  // Main home feed — 5 DB queries in one round-trip
  group("home feed", () => {
    const start = Date.now();
    const r = http.get(`${BASE}/api/home/`, { headers: h });
    homeTrend.add(Date.now() - start);

    if (!check(r, { "home 200": (r) => r.status === 200 })) return;

    // Parse and verify the payload shape
    const body = r.json();
    check(body, {
      "has popular_today": (b) => Array.isArray(b.popular_today),
      "has popular_14d":   (b) => Array.isArray(b.popular_14d),
      "has featured":      (b) => Array.isArray(b.featured),
      "has recent_prs":    (b) => Array.isArray(b.recent_prs),
    });

    sleep(Math.random() * 3 + 1);  // user scrolls and reads the home page
  });

  // ~40% of users then click "see all" on popular — hits a separate paginated endpoint
  if (Math.random() < 0.4) {
    group("popular see-all", () => {
      const period = Math.random() < 0.5 ? "today" : "14d";
      const start  = Date.now();
      const r = http.get(`${BASE}/api/home/popular?period=${period}&limit=20&offset=0`, { headers: h });
      popularTrend.add(Date.now() - start);
      check(r, { "popular 200": (r) => r.status === 200 });
      sleep(Math.random() * 2 + 1);
    });
  }

  sleep(Math.random() * 3 + 2);
}
