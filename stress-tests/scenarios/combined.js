// Combined realistic load test — all read-only user behaviours running simultaneously
//
// READ-ONLY — no writes, no uploads, no lasting side effects.
//
// Traffic weights (realistic usage):
//   60% → anonymous folder navigation
//   20% → authenticated home feed viewers
//   13% → authenticated material readers
//    7% → concurrent comment readers
//
// Run (unauthenticated scenarios only):
//   k6 run stress-tests/scenarios/combined.js
//
// Run with auth (enables home, material, comment scenarios):
//   TOKEN=<access_jwt> k6 run stress-tests/scenarios/combined.js
//
// Scale up (doubles all VU counts):
//   TOKEN=<access_jwt> SCALE=2 k6 run stress-tests/scenarios/combined.js
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE  = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const SCALE = parseFloat(__ENV.SCALE || "1");

const homeTrend     = new Trend("home_duration");
const materialTrend = new Trend("material_duration");
const navTrend      = new Trend("nav_duration");
const commentTrend  = new Trend("comment_read_duration");
const totalRequests = new Counter("total_scenario_requests");

// ─── Scenario definitions ────────────────────────────────────────────────────

export const options = {
  scenarios: {
    folder_navigation: {
      executor:    "ramping-vus",
      exec:        "browseScenario",
      startVUs:    0,
      stages: [
        { duration: "30s", target: Math.ceil(20 * SCALE) },
        { duration: "2m",  target: Math.ceil(30 * SCALE) },
        { duration: "1m",  target: Math.ceil(30 * SCALE) },
        { duration: "20s", target: 0 },
      ],
      gracefulRampDown: "20s",
    },

    home_feed: {
      executor:    "ramping-vus",
      exec:        "homeScenario",
      startVUs:    0,
      stages: [
        { duration: "30s", target: 0 },
        { duration: "30s", target: Math.ceil(10 * SCALE) },
        { duration: "2m",  target: Math.ceil(15 * SCALE) },
        { duration: "20s", target: 0 },
      ],
      gracefulRampDown: "15s",
    },

    material_reading: {
      executor:    "ramping-vus",
      exec:        "materialScenario",
      startVUs:    0,
      stages: [
        { duration: "30s", target: 0 },
        { duration: "30s", target: Math.ceil(8 * SCALE) },
        { duration: "2m",  target: Math.ceil(8 * SCALE) },
        { duration: "20s", target: 0 },
      ],
      gracefulRampDown: "15s",
    },

    comment_reading: {
      executor:    "ramping-vus",
      exec:        "commentScenario",
      startVUs:    0,
      stages: [
        { duration: "45s", target: 0 },
        { duration: "30s", target: Math.ceil(4 * SCALE) },
        { duration: "1m",  target: Math.ceil(4 * SCALE) },
        { duration: "20s", target: 0 },
      ],
      gracefulRampDown: "15s",
    },
  },

  thresholds: {
    http_req_failed:       ["rate<0.02"],
    http_req_duration:     ["p(95)<5000"],
    home_duration:         ["p(95)<5000"],
    material_duration:     ["p(95)<2500"],
    nav_duration:          ["p(95)<2000"],
    comment_read_duration: ["p(95)<1500"],
  },
};

// ─── Fixtures ────────────────────────────────────────────────────────────────

const SCHOOL_DIRS = [
  { id: "b8273422-6c95-4e90-9dc6-a62fa9245007", path: "tsp" },
  { id: "fdec7be9-a50f-467b-bd12-2fa7c56bb6eb", path: "imt-bs" },
  { id: "d7280ec2-f345-4e85-83b4-547731ae2071", path: "lsh" },
];

const LEAF_DIRS = [
  "04e99f86-2499-4dcf-9738-534e7b9a743d",
  "a18c4a56-2b9a-4e21-878c-4c17b51aeb3b",
  "2aa1a162-c850-480a-a853-525b9befd65b",
  "68fd6e1f-3a5b-4180-8059-bbb6c3b966ec",
];

const MATERIAL_IDS = [
  "b467248a-a0de-4451-b48b-a29467886970",
  "52208662-b5d0-4bc7-8fe8-f92918423c67",
  "baca3b89-d73e-47fa-94ff-1508059ec444",
];

const COMMENT_TARGETS = [
  { type: "material",  id: "b467248a-a0de-4451-b48b-a29467886970" },
  { type: "material",  id: "52208662-b5d0-4bc7-8fe8-f92918423c67" },
  { type: "directory", id: "04e99f86-2499-4dcf-9738-534e7b9a743d" },
];

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

// ─── setup() — exchange token / login once ────────────────────────────────────

export function setup() {
  const token = __ENV.TOKEN;
  if (token) {
    console.log("Using provided TOKEN — authenticated scenarios active");
    return { token };
  }
  const email = __ENV.TEST_EMAIL, password = __ENV.TEST_PASSWORD;
  if (!email || !password) {
    console.warn("No TOKEN / TEST_EMAIL+TEST_PASSWORD — authenticated scenarios skip (will sleep)");
    return { token: null };
  }
  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email, password }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) {
    console.warn(`Login failed (${res.status}) — authenticated scenarios will sleep`);
    return { token: null };
  }
  console.log("Login OK — authenticated scenarios active");
  return { token: res.json("access_token") };
}

// ─── Scenario executors ───────────────────────────────────────────────────────

export function browseScenario() {
  const start  = Date.now();
  const school = pick(SCHOOL_DIRS);

  const r1 = http.get(`${BASE}/api/browse`);
  check(r1, { "root browse 200": (r) => r.status === 200 });
  totalRequests.add(1);
  sleep(0.3);

  const r2 = http.get(`${BASE}/api/directories/${school.id}/children`);
  check(r2, { "school children 200": (r) => r.status === 200 });
  totalRequests.add(1);
  sleep(Math.random() * 1 + 0.5);

  const leaf = pick(LEAF_DIRS);
  const r3 = http.get(`${BASE}/api/directories/${leaf}/children`);
  check(r3, { "leaf children 200": (r) => r.status === 200 });
  totalRequests.add(1);

  navTrend.add(Date.now() - start);
  sleep(Math.random() * 2 + 1);
}

export function homeScenario(data) {
  if (!data.token) { sleep(5); return; }
  const h = { Authorization: `Bearer ${data.token}` };

  const start = Date.now();
  const r = http.get(`${BASE}/api/home/`, { headers: h });
  homeTrend.add(Date.now() - start);
  check(r, { "home 200": (r) => r.status === 200 });
  totalRequests.add(1);

  // ~40% also hit popular paginated list
  if (r.status === 200 && Math.random() < 0.4) {
    const period = Math.random() < 0.5 ? "today" : "14d";
    const r2 = http.get(`${BASE}/api/home/popular?period=${period}&limit=20&offset=0`, { headers: h });
    check(r2, { "popular 200": (r) => r.status === 200 });
    totalRequests.add(1);
  }

  sleep(Math.random() * 4 + 2);
}

export function materialScenario(data) {
  if (!data.token) { sleep(5); return; }
  const h   = { Authorization: `Bearer ${data.token}` };
  const mid = pick(MATERIAL_IDS);

  const start = Date.now();
  const r = http.get(`${BASE}/api/materials/${mid}`, { headers: h });
  materialTrend.add(Date.now() - start);
  totalRequests.add(1);

  if (!check(r, { "material 200": (r) => r.status === 200 })) { sleep(3); return; }

  // Parallel: comments + versions (mirrors frontend behaviour)
  const [commentsRes, versionsRes] = http.batch([
    ["GET", `${BASE}/api/comments?targetType=material&targetId=${mid}&limit=20`, null, { headers: h }],
    ["GET", `${BASE}/api/materials/${mid}/versions`, null, { headers: h }],
  ]);
  check(commentsRes, { "comments 200": (r) => r.status === 200 });
  check(versionsRes, { "versions 200": (r) => r.status === 200 });
  totalRequests.add(2);

  sleep(Math.random() * 5 + 3);
}

export function commentScenario(data) {
  if (!data.token) { sleep(5); return; }
  const h      = { Authorization: `Bearer ${data.token}` };
  const target = pick(COMMENT_TARGETS);

  const start = Date.now();
  const r = http.get(
    `${BASE}/api/comments?targetType=${target.type}&targetId=${target.id}&limit=20&offset=0`,
    { headers: h },
  );
  commentTrend.add(Date.now() - start);
  check(r, { "comment read 200": (r) => r.status === 200 });
  totalRequests.add(1);

  sleep(Math.random() * 3 + 2);
}
