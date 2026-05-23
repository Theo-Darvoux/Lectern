// Scenario: users rapidly navigating through folders
//
// Models the most common unauthenticated user journey:
//   root → pick a school → pick a semester → pick a subject → see materials
//
// Run: k6 run stress-tests/scenarios/folder-navigation.js
// Heavier: PEAK_VUS=60 k6 run stress-tests/scenarios/folder-navigation.js
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE     = __ENV.BASE_URL || "https://intellect.clubcode.fr";
const PEAK_VUS = parseInt(__ENV.PEAK_VUS || "30");

const navDepth  = new Trend("nav_depth_duration");
const navErrors = new Counter("nav_errors");

export const options = {
  stages: [
    { duration: "20s", target: PEAK_VUS / 3 },
    { duration: "1m",  target: PEAK_VUS },
    { duration: "1m",  target: PEAK_VUS },
    { duration: "20s", target: 0 },
  ],
  thresholds: {
    http_req_failed:          ["rate<0.02"],
    http_req_duration:        ["p(95)<2000", "p(99)<4000"],
    // Full user journey including think-time sleeps (3–4 steps × 0.5–2s each)
    nav_depth_duration:       ["p(95)<10000"],
  },
};

// Real prod directory tree (fetched 2026-05-23)
// Root schools → semesters → subjects (with materials)
const TREE = [
  {
    id:   "b8273422-6c95-4e90-9dc6-a62fa9245007",
    path: "tsp",
    children: [
      {
        id:   "68fd6e1f-3a5b-4180-8059-bbb6c3b966ec",
        path: "tsp/semestre-6-1a",
        children: [
          { id: "04e99f86-2499-4dcf-9738-534e7b9a743d", path: "tsp/semestre-6-1a/mat3601-statistique-et-analyse-de-donnees" },
          { id: "a18c4a56-2b9a-4e21-878c-4c17b51aeb3b", path: "tsp/semestre-6-1a/mat3602-optimisation" },
          { id: "b3ebdc29-dded-40b6-aae0-243fb9795dde", path: "tsp/semestre-6-1a/net3601-performances-de-reseaux" },
          { id: "2aa1a162-c850-480a-a853-525b9befd65b", path: "tsp/semestre-6-1a/phy3601-systemes-de-transmission-optique" },
        ],
      },
      { id: "6bcc62be-1438-44fa-be4f-f665bc3d0869", path: "tsp/semestre-5-1a",  children: [] },
      { id: "8e7f2260-7c9d-49c5-8c97-ff7b610213fb", path: "tsp/semestre-7-2a",  children: [] },
      { id: "ced79917-4843-4a38-8466-1af65bf62892", path: "tsp/semestre-8-2a",  children: [] },
    ],
  },
  {
    id:   "fdec7be9-a50f-467b-bd12-2fa7c56bb6eb",
    path: "imt-bs",
    children: [],
  },
  {
    id:   "d7280ec2-f345-4e85-83b4-547731ae2071",
    path: "lsh",
    children: [],
  },
];

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export default function () {
  const start = Date.now();

  // Step 1 — landing on the root browse page
  group("root browse", () => {
    const r = http.get(`${BASE}/api/browse`);
    if (!check(r, { "root 200": (r) => r.status === 200 })) navErrors.add(1);
    sleep(Math.random() * 1 + 0.3);   // user reads the page
  });

  // Step 2 — picks a school
  const school = pick(TREE);
  group("school level", () => {
    // Both path-based and children-based endpoints get hit depending on how
    // the frontend renders: browse path for breadcrumb, children for grid.
    const r1 = http.get(`${BASE}/api/browse/${school.path}`);
    if (!check(r1, { "school browse ok": (r) => r.status === 200 })) navErrors.add(1);

    const r2 = http.get(`${BASE}/api/directories/${school.id}/children`);
    if (!check(r2, { "school children ok": (r) => r.status === 200 })) navErrors.add(1);

    sleep(Math.random() * 1.5 + 0.5);
  });

  if (!school.children || school.children.length === 0) {
    navDepth.add(Date.now() - start);
    return;
  }

  // Step 3 — picks a semester
  const semester = pick(school.children);
  group("semester level", () => {
    const r1 = http.get(`${BASE}/api/browse/${semester.path}`);
    if (!check(r1, { "semester browse ok": (r) => r.status === 200 })) navErrors.add(1);

    const r2 = http.get(`${BASE}/api/directories/${semester.id}/children`);
    if (!check(r2, { "semester children ok": (r) => r.status === 200 })) navErrors.add(1);

    sleep(Math.random() * 2 + 0.5);   // user scans subjects
  });

  if (!semester.children || semester.children.length === 0) {
    navDepth.add(Date.now() - start);
    return;
  }

  // Step 4 — picks a subject / leaf folder
  const subject = pick(semester.children);
  group("subject level", () => {
    const r1 = http.get(`${BASE}/api/browse/${subject.path}`);
    if (!check(r1, { "subject browse ok": (r) => r.status === 200 })) navErrors.add(1);

    const r2 = http.get(`${BASE}/api/directories/${subject.id}/children`);
    if (!check(r2, { "subject children ok": (r) => r.status === 200 })) navErrors.add(1);

    // Also fetch the breadcrumb path (frontend does this on every nav)
    const r3 = http.get(`${BASE}/api/directories/${subject.id}/path`);
    check(r3, { "breadcrumb ok": (r) => r.status === 200 });

    sleep(Math.random() * 2 + 1);     // user reads material list
  });

  navDepth.add(Date.now() - start);
  sleep(Math.random() * 2);
}
