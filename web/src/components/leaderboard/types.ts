import type { components } from "@/lib/api-types";

export type LeaderboardEntry = components["schemas"]["LeaderboardEntry"];
export type LeaderboardResponse = components["schemas"]["LeaderboardResponse"];
export type LeaderboardPeriod = LeaderboardResponse["period"];
