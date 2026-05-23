// Scenario: high concurrent load on the comments read endpoint
//
// READ-ONLY — no comment creation. Tests comment pagination under concurrent load:
// multiple users simultaneously reading comments on the same and different targets.
//
// Run: TOKEN=<access_jwt> k6 run stress-tests/scenarios/comment-storm.js
// Or:  TEST_EMAIL=x TEST_PASSWORD=y k6 run stress-tests/scenarios/comment-storm.js
import http from "k6/http";
import { check, group, sleep, fail } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const PEAK_VUS = parseInt(__ENV.PEAK_VUS || "30");

const commentReadTrend = new Trend("comment_read_duration");
const totalReads       = new Counter("comment_pages_read");

export const options = {
  stages: [
    { duration: "15s", target: PEAK_VUS },
    { duration: "2m",  target: PEAK_VUS },
    { duration: "15s", target: 0 },
  ],
  thresholds: {
    http_req_failed:       ["rate<0.02"],
    http_req_duration:     ["p(95)<2000"],
    comment_read_duration: ["p(95)<1500"],
  },
};

// Mix of targets to simulate many users reading different materials' comments
const TARGETS = [
  { type: "material",  id: "b467248a-a0de-4451-b48b-a29467886970" },
  { type: "material",  id: "52208662-b5d0-4bc7-8fe8-f92918423c67" },
  { type: "material",  id: "baca3b89-d73e-47fa-94ff-1508059ec444" },
  { type: "directory", id: "04e99f86-2499-4dcf-9738-534e7b9a743d" },
  { type: "directory", id: "2aa1a162-c850-480a-a853-525b9befd65b" },
];

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

export function setup() {
  const token = __ENV.TOKEN;
  if (token) return { token };
  const email = __ENV.TEST_EMAIL, password = __ENV.TEST_PASSWORD;
  if (!email || !password) fail("Provide TOKEN or TEST_EMAIL + TEST_PASSWORD");
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

  // Read first page
  const target = pick(TARGETS);
  group("read comments page 1", () => {
    const start = Date.now();
    const r = http.get(
      `${BASE}/api/comments?targetType=${target.type}&targetId=${target.id}&limit=20&offset=0`,
      { headers: h },
    );
    commentReadTrend.add(Date.now() - start);
    if (!check(r, { "comments 200": (r) => r.status === 200 })) return;
    totalReads.add(1);

    // ~25% of users page through to page 2
    if (Math.random() < 0.25) {
      sleep(0.5);
      const r2 = http.get(
        `${BASE}/api/comments?targetType=${target.type}&targetId=${target.id}&limit=20&offset=20`,
        { headers: h },
      );
      check(r2, { "comments page 2 200": (r) => r.status === 200 });
      totalReads.add(1);
    }
  });

  sleep(Math.random() * 2 + 1);
}
