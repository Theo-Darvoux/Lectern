import type { SearchKind, SearchResult } from "./use-search";

export const SEARCH_MATERIAL_TYPES = [
  "document",
  "polycopie",
  "annal",
  "cheatsheet",
  "tip",
  "review",
  "discussion",
  "video",
  "qcm",
  "link",
  "other",
] as const;

export const SEARCH_STATUSES = ["important", "current", "deprecated", "archived"] as const;

export type SearchPageKind = SearchKind | "all";
export type SearchMaterialType = (typeof SEARCH_MATERIAL_TYPES)[number] | "all";
export type SearchStatusFilter = NonNullable<SearchResult["status"]> | "all";

export interface SearchPageState {
  query: string;
  kind: SearchPageKind;
  materialType: SearchMaterialType;
  status: SearchStatusFilter;
  page: number;
  directoryId?: string;
  directoryName?: string;
  recursive: boolean;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function parseSearchPageState(params: URLSearchParams): SearchPageState {
  const rawKind = params.get("kind");
  const rawMaterialType = params.get("type");
  const rawStatus = params.get("status");
  const rawPage = Number.parseInt(params.get("page") || "1", 10);
  const rawDirectoryId = params.get("directory_id") || undefined;
  const directoryId = rawDirectoryId && UUID_PATTERN.test(rawDirectoryId) ? rawDirectoryId : undefined;

  return {
    query: (params.get("q") || "").trim().slice(0, 200),
    kind: rawKind === "material" || rawKind === "directory" ? rawKind : "all",
    materialType: SEARCH_MATERIAL_TYPES.includes(rawMaterialType as never)
      ? (rawMaterialType as SearchMaterialType)
      : "all",
    status: SEARCH_STATUSES.includes(rawStatus as never)
      ? (rawStatus as SearchStatusFilter)
      : "all",
    page: Number.isFinite(rawPage) && rawPage > 0 ? Math.min(rawPage, 50) : 1,
    directoryId,
    directoryName: directoryId ? params.get("directory_name") || undefined : undefined,
    recursive: Boolean(directoryId && params.get("recursive") === "true"),
  };
}

export function updateSearchPageParams(
  current: URLSearchParams,
  patch: Partial<SearchPageState>,
): URLSearchParams {
  const next = new URLSearchParams(current);
  const mappings: Array<[keyof SearchPageState, string]> = [
    ["query", "q"],
    ["kind", "kind"],
    ["materialType", "type"],
    ["status", "status"],
    ["directoryId", "directory_id"],
    ["directoryName", "directory_name"],
  ];

  for (const [stateKey, paramKey] of mappings) {
    if (!(stateKey in patch)) continue;
    const value = patch[stateKey];
    if (value === undefined || value === "" || value === "all") next.delete(paramKey);
    else next.set(paramKey, String(value));
  }

  if ("recursive" in patch) {
    if (patch.recursive) next.set("recursive", "true");
    else next.delete("recursive");
  }

  if ("page" in patch && patch.page && patch.page > 1) next.set("page", String(patch.page));
  else next.delete("page");
  return next;
}
