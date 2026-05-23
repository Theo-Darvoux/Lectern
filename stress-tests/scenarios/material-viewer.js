// Scenario: many users opening and reading materials simultaneously
//
// READ-ONLY — no view recording, no likes, no favourites.
// Tests: material detail fetch, comment loading, version listing.
//
// Run: TOKEN=<access_jwt> k6 run stress-tests/scenarios/material-viewer.js
// Or:  TEST_EMAIL=x TEST_PASSWORD=y k6 run stress-tests/scenarios/material-viewer.js
import http from "k6/http";
import { check, group, sleep, fail } from "k6";
import { Trend } from "k6/metrics";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const PEAK_VUS = parseInt(__ENV.PEAK_VUS || "25");

const materialLoad = new Trend("material_load_duration");
const commentLoad  = new Trend("comment_load_duration");

export const options = {
  stages: [
    { duration: "20s", target: 10 },
    { duration: "1m",  target: PEAK_VUS },
    { duration: "1m",  target: PEAK_VUS },
    { duration: "20s", target: 0 },
  ],
  thresholds: {
    http_req_failed:        ["rate<0.02"],
    http_req_duration:      ["p(95)<3000", "p(99)<6000"],
    material_load_duration: ["p(95)<2500"],
    comment_load_duration:  ["p(95)<1500"],
  },
};

// Real material IDs from prod (fetched 2026-05-23)
const MATERIAL_IDS = [
  "b467248a-a0de-4451-b48b-a29467886970",  // Poly de cours — MAT3601
  "52208662-b5d0-4bc7-8fe8-f92918423c67",  // CoursBilanLiaison — PHY3601
  "baca3b89-d73e-47fa-94ff-1508059ec444",  // CoursComposants — PHY3601
];

const LEAF_DIR_IDS = [
  "04e99f86-2499-4dcf-9738-534e7b9a743d",
  "2aa1a162-c850-480a-a853-525b9befd65b",
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
  const mid = pick(MATERIAL_IDS);

  // Step 1 — browse into the directory first (realistic nav)
  group("directory browse", () => {
    const r = http.get(`${BASE}/api/directories/${pick(LEAF_DIR_IDS)}/children`, { headers: h });
    check(r, { "dir children 200": (r) => r.status === 200 });
    sleep(Math.random() * 1 + 0.3);
  });

  // Step 2 — open the material detail
  group("material detail", () => {
    const start = Date.now();
    const r = http.get(`${BASE}/api/materials/${mid}`, { headers: h });
    materialLoad.add(Date.now() - start);
    if (!check(r, { "material 200": (r) => r.status === 200 })) return;

    // Step 3 — load comments and version list in parallel (what the frontend does)
    const start2 = Date.now();
    const [commentsRes, versionsRes] = http.batch([
      ["GET", `${BASE}/api/comments?targetType=material&targetId=${mid}&limit=20&offset=0`, null, { headers: h }],
      ["GET", `${BASE}/api/materials/${mid}/versions`, null, { headers: h }],
    ]);
    commentLoad.add(Date.now() - start2);

    check(commentsRes, { "comments 200": (r) => r.status === 200 });
    check(versionsRes, { "versions 200": (r) => r.status === 200 });

    sleep(Math.random() * 6 + 3);  // user reads the document
  });

  sleep(Math.random() * 2 + 1);
}
