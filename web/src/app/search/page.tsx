"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Folder,
  LayoutGrid,
  LayoutList,
  Loader2,
  RotateCcw,
  Search,
  Sparkles,
  FileText,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import {
  SearchGridItem,
  SearchListCard,
  getMaterialTypeIcon,
  getStatusIndicatorClass,
} from "@/components/search/search-results-list";
import {
  SEARCH_MATERIAL_TYPES,
  SEARCH_STATUSES,
  getValidSearchPage,
  parseSearchPageState,
  updateSearchPageParams,
  type SearchMaterialType,
  type SearchStatusFilter,
} from "@/components/search/search-page-state";
import {
  getSearchErrorMessageKey,
  getSearchErrorTitleKey,
  isRetryableSearchError,
  useSearch,
  type SearchResult,
} from "@/components/search/use-search";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

const SUGGESTED_SEARCHES = [
  "Calculus",
  "Algèbre",
  "Informatique",
  "Physique",
  "Chimie",
  "Examens",
  "TD",
  "Fiches",
];

function SearchPageContent() {
  const t = useTranslations("Search");
  const tTypes = useTranslations("MaterialTypes");
  const params = useSearchParams();
  const router = useRouter();
  const openLink = useExternalLinkStore((store) => store.openLink);
  const [viewMode, setViewMode] = React.useState<"list" | "grid">("list");
  const resultsTopRef = React.useRef<HTMLDivElement>(null);

  const state = React.useMemo(
    () => parseSearchPageState(new URLSearchParams(params.toString())),
    [params],
  );
  const [draft, setDraft] = React.useState(state.query);

  React.useEffect(() => setDraft(state.query), [state.query]);

  const {
    results,
    total,
    status,
    error,
    retry,
    requestKey,
    resolvedRequestKey,
  } = useSearch(state.query, {
    delay: 0,
    page: state.page,
    limit: PAGE_SIZE,
    kind: state.kind === "all" ? undefined : state.kind,
    materialType: state.materialType === "all" ? undefined : state.materialType,
    status: state.status === "all" ? undefined : state.status,
    directoryId: state.directoryId,
    recursive: state.recursive,
  });

  const navigate = React.useCallback(
    (next: URLSearchParams, mode: "push" | "replace" = "replace") => {
      const url = next.size ? `/search?${next.toString()}` : "/search";
      router[mode](url, { scroll: false });
    },
    [router],
  );

  const updateFilters = (patch: Parameters<typeof updateSearchPageParams>[1]) => {
    navigate(updateSearchPageParams(new URLSearchParams(params.toString()), patch));
  };

  const clearAllFilters = () => {
    updateFilters({
      kind: "all",
      materialType: "all",
      status: "all",
      directoryId: undefined,
      directoryName: undefined,
      recursive: false,
      page: 1,
    });
  };

  const validPage = getValidSearchPage(total, PAGE_SIZE, state.page);
  React.useEffect(() => {
    if (
      (status === "success" || status === "empty") &&
      resolvedRequestKey === requestKey &&
      total > 0 &&
      validPage !== state.page
    ) {
      navigate(
        updateSearchPageParams(new URLSearchParams(params.toString()), { page: validPage }),
      );
    }
  }, [navigate, params, requestKey, resolvedRequestKey, state.page, status, total, validPage]);

  const submit = (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    const next = updateSearchPageParams(new URLSearchParams(params.toString()), {
      query: draft.trim().slice(0, 200),
      page: 1,
    });
    navigate(next, "push");
  };

  const handleSuggestionClick = (term: string) => {
    setDraft(term);
    const next = updateSearchPageParams(new URLSearchParams(params.toString()), {
      query: term.slice(0, 200),
      page: 1,
    });
    navigate(next, "push");
  };

  const openResult = (result: SearchResult) => {
    const targetUrl = String(result.url || result.metadata?.url || "").trim();
    if (result.type === "link" && targetUrl) {
      openLink(targetUrl, (path) => router.push(path));
      return;
    }
    router.push(
      result.browse_path ||
        (result.search_type === "directory"
          ? `/directories/${result.id}`
          : `/materials/${result.id}`),
    );
  };

  const handlePageChange = (newPage: number) => {
    updateFilters({ page: newPage });
    if (resultsTopRef.current) {
      resultsTopRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const isInitialLoading = (status === "loading" || status === "debouncing") && results.length === 0;
  const isTransitioning =
    (status === "loading" ||
      status === "debouncing" ||
      (resolvedRequestKey !== null && resolvedRequestKey !== requestKey)) &&
    results.length > 0;

  const hasActiveFilters =
    state.kind !== "all" ||
    state.materialType !== "all" ||
    state.status !== "all" ||
    Boolean(state.directoryId);

  return (
    <div className="mx-auto w-full max-w-5xl px-3.5 py-6 pb-36 sm:px-6 sm:py-8 sm:pb-12 lg:px-8">
      {/* ── Page Header ────────────────────────────────────────── */}
      <header className="mb-6 space-y-2">
        <div className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
          <Sparkles className="size-3.5" />
          <span>{t("pageEyebrow")}</span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl text-foreground">
          {t("pageTitle")}
        </h1>
        <p className="max-w-2xl text-xs sm:text-sm text-muted-foreground leading-relaxed">
          {t("pageDescription")}
        </p>
      </header>

      {/* ── Search Input Box ────────────────────────────────────── */}
      <div className="space-y-3">
        <form onSubmit={submit} role="search" className="relative flex items-center gap-2">
          <div className="relative min-w-0 flex-1 shadow-xs rounded-xl focus-within:ring-2 focus-within:ring-primary/20">
            <Search className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground pointer-events-none" />
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={t("searchMaterialsDirs")}
              aria-label={t("searchMaterialsDirs")}
              maxLength={200}
              className="h-11 sm:h-12 pl-10 pr-10 text-sm sm:text-base rounded-xl border-border/80 bg-card shadow-inner"
            />
            {draft && (
              <button
                type="button"
                onClick={() => {
                  setDraft("");
                  updateFilters({ query: "" });
                }}
                aria-label={t("clearSearch")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <Button
            type="submit"
            className="h-11 sm:h-12 px-4 sm:px-6 rounded-xl font-medium shadow-xs shrink-0 text-sm"
          >
            {t("searchAction")}
          </Button>
        </form>

        {/* Quick Suggestion Chips (Shown when query is empty) */}
        {!state.query && (
          <div className="flex items-center gap-1.5 flex-wrap text-xs text-muted-foreground pt-1">
            <span className="font-medium shrink-0">{t("startTyping")}:</span>
            {SUGGESTED_SEARCHES.map((term) => (
              <button
                key={term}
                type="button"
                onClick={() => handleSuggestionClick(term)}
                className="rounded-full bg-muted/70 px-2.5 py-1 text-foreground/80 hover:bg-accent hover:text-foreground transition-colors cursor-pointer"
              >
                {term}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Filter Controls Bar ─────────────────────────────────── */}
      <div className="mt-6 space-y-3" aria-label={t("filters")}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 border-b border-border/60 pb-3">
          {/* Kind Segmented Control (full width on mobile, auto on desktop) */}
          <div className="grid grid-cols-3 sm:flex items-center gap-1 bg-muted/60 p-1 rounded-lg border border-border/40 w-full sm:w-auto">
            {(["all", "material", "directory"] as const).map((kind) => {
              const isActive = state.kind === kind;
              return (
                <button
                  key={kind}
                  type="button"
                  onClick={() =>
                    updateFilters({
                      kind,
                      materialType: kind === "material" ? state.materialType : "all",
                      page: 1,
                    })
                  }
                  className={cn(
                    "flex items-center justify-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer truncate",
                    isActive
                      ? "bg-background text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground hover:bg-background/50",
                  )}
                  aria-pressed={isActive}
                >
                  {kind === "all" ? (
                    <Sparkles className="size-3.5 shrink-0" />
                  ) : kind === "material" ? (
                    <FileText className="size-3.5 shrink-0" />
                  ) : (
                    <Folder className="size-3.5 shrink-0" />
                  )}
                  <span className="truncate">{t(`kinds.${kind}`)}</span>
                </button>
              );
            })}
          </div>

          {/* View Mode Toggle */}
          <div className="flex items-center justify-end gap-1 self-end sm:self-auto bg-muted/60 p-1 rounded-lg border border-border/40 shrink-0">
            <button
              type="button"
              onClick={() => setViewMode("list")}
              title="List View"
              aria-label="List View"
              className={cn(
                "p-1.5 rounded-md text-xs font-medium transition-all cursor-pointer",
                viewMode === "list"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <LayoutList className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode("grid")}
              title="Grid View"
              aria-label="Grid View"
              className={cn(
                "p-1.5 rounded-md text-xs font-medium transition-all cursor-pointer",
                viewMode === "grid"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <LayoutGrid className="size-4" />
            </button>
          </div>
        </div>

        {/* Secondary Filter Dropdowns & Scope */}
        <div className="flex flex-wrap items-center gap-2 sm:gap-2.5">
          {state.kind !== "directory" && (
            <Select
              value={state.materialType}
              onValueChange={(value) =>
                updateFilters({
                  materialType: value as SearchMaterialType,
                  kind: value === "all" ? state.kind : "material",
                  page: 1,
                })
              }
            >
              <SelectTrigger
                size="sm"
                aria-label={t("filterByMaterialType")}
                className="h-8.5 text-xs bg-card w-auto min-w-[130px] sm:min-w-[140px]"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allMaterialTypes")}</SelectItem>
                {SEARCH_MATERIAL_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    <span className="flex items-center gap-2">
                      {getMaterialTypeIcon(type)}
                      <span>{tTypes(type)}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Select
            value={state.status}
            onValueChange={(value) =>
              updateFilters({ status: value as SearchStatusFilter, page: 1 })
            }
          >
            <SelectTrigger
              size="sm"
              aria-label={t("filterByStatus")}
              className="h-8.5 text-xs bg-card w-auto min-w-[110px] sm:min-w-[125px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("allStatuses")}</SelectItem>
              {SEARCH_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  <span className="flex items-center gap-2">
                    <span className={cn("size-2 rounded-full", getStatusIndicatorClass(value))} />
                    <span>{t(`statuses.${value}`)}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {state.directoryId && (
            <div className="flex items-center gap-1 rounded-md bg-secondary/80 pl-2.5 pr-1 py-1 text-xs font-medium text-secondary-foreground border border-border/50 max-w-full">
              <Folder className="size-3.5 text-primary shrink-0" />
              <span className="truncate max-w-[140px] sm:max-w-[200px]">
                {t("scopeLabel", { name: state.directoryName || t("currentFolder") })}
              </span>
              <button
                type="button"
                onClick={() =>
                  updateFilters({
                    directoryId: undefined,
                    directoryName: undefined,
                    recursive: false,
                    page: 1,
                  })
                }
                aria-label="Clear folder scope"
                className="rounded p-0.5 hover:bg-accent hover:text-foreground transition-colors ml-1 shrink-0"
              >
                <X className="size-3" />
              </button>
            </div>
          )}

          {hasActiveFilters && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={clearAllFilters}
              className="text-xs text-muted-foreground hover:text-foreground gap-1.5 h-8 px-2 shrink-0"
            >
              <RotateCcw className="size-3" />
              <span>Reset</span>
            </Button>
          )}
        </div>
      </div>

      {/* ── Results Container & States ──────────────────────────── */}
      <div ref={resultsTopRef} className="scroll-mt-6" />

      <section className="mt-6" aria-live="polite" aria-busy={isInitialLoading || isTransitioning}>
        {/* 1. Idle State: No query entered */}
        {!state.query ? (
          <div className="rounded-2xl border border-dashed border-border/80 bg-muted/20 px-4 sm:px-6 py-12 sm:py-20 text-center">
            <div className="mx-auto flex size-12 sm:size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary mb-4">
              <Search className="size-6 sm:size-7" />
            </div>
            <h2 className="text-base sm:text-lg font-semibold text-foreground">
              {t("startTyping")}
            </h2>
            <p className="mx-auto mt-1.5 max-w-md text-xs sm:text-sm text-muted-foreground">
              {t("searchHint")}
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2 max-w-md mx-auto">
              {SUGGESTED_SEARCHES.slice(0, 6).map((term) => (
                <button
                  key={term}
                  type="button"
                  onClick={() => handleSuggestionClick(term)}
                  className="rounded-lg border border-border/80 bg-card px-2.5 sm:px-3 py-1.5 text-xs font-medium text-foreground hover:border-primary hover:bg-accent transition-all cursor-pointer shadow-2xs"
                >
                  {term}
                </button>
              ))}
            </div>
          </div>
        ) : isInitialLoading ? (
          /* 2. Initial Loading Skeleton (only when no results exist yet) */
          <div role="status">
            <span className="sr-only">{t("searching")}</span>
            <div className="flex items-center justify-between mb-4">
              <Skeleton className="h-5 w-32 rounded-md" />
              <Skeleton className="h-4 w-20 rounded-md" />
            </div>
            {viewMode === "grid" ? (
              <div className="grid grid-cols-1 xs:grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
                {Array.from({ length: 10 }).map((_, index) => (
                  <div key={index} className="flex flex-col gap-2 rounded-xl border bg-card p-2">
                    <Skeleton className="aspect-[4/3] w-full rounded-lg" />
                    <Skeleton className="h-4 w-3/4 rounded" />
                    <Skeleton className="h-3 w-1/2 rounded" />
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div
                    key={index}
                    className="flex flex-col sm:flex-row items-stretch sm:items-start gap-3 sm:gap-4 rounded-xl border p-3.5 sm:p-4 bg-card"
                  >
                    <div className="flex items-center gap-3">
                      <Skeleton className="size-10 sm:size-12 rounded-lg shrink-0" />
                      <div className="sm:hidden flex-1 space-y-1.5">
                        <Skeleton className="h-4 w-3/4 rounded" />
                        <Skeleton className="h-3 w-1/3 rounded" />
                      </div>
                    </div>
                    <div className="flex-1 space-y-2">
                      <Skeleton className="hidden sm:block h-4.5 w-1/3 rounded-md" />
                      <Skeleton className="h-3.5 w-2/3 rounded-md" />
                      <Skeleton className="h-3 w-1/4 rounded-md" />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : status === "error" ? (
          /* 3. Error State */
          <div
            className="rounded-2xl border border-destructive/30 bg-destructive/5 px-4 sm:px-6 py-8 sm:py-12 text-center"
            role="alert"
          >
            <AlertCircle className="mx-auto size-8 sm:size-9 text-destructive" />
            <h2 className="mt-3 text-sm sm:text-base font-semibold text-foreground">
              {t(getSearchErrorTitleKey(error))}
            </h2>
            <p className="mt-1 text-xs sm:text-sm text-muted-foreground max-w-md mx-auto">
              {t(getSearchErrorMessageKey(error))}
            </p>
            {isRetryableSearchError(error) && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={retry}
                className="mt-4 gap-2 text-xs"
              >
                <RotateCcw className="size-3.5" />
                <span>{t("retry")}</span>
              </Button>
            )}
          </div>
        ) : results.length === 0 ? (
          /* 4. Empty Results State */
          <div className="rounded-2xl border border-dashed border-border/80 bg-muted/20 px-4 sm:px-6 py-12 sm:py-16 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground/60 mb-3">
              <Search className="size-6" />
            </div>
            <h2 className="text-sm sm:text-base font-semibold text-foreground">
              {t("noResults")}
            </h2>
            <p className="mx-auto mt-1 max-w-md text-xs sm:text-sm text-muted-foreground">
              {t("noResultsHint")}
            </p>
            {hasActiveFilters && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={clearAllFilters}
                className="mt-4 gap-1.5 text-xs"
              >
                <RotateCcw className="size-3.5" />
                <span>Reset filters</span>
              </Button>
            )}
          </div>
        ) : (
          /* 5. Results List / Grid with Smooth Transitions (Zero-Flicker) */
          <>
            {/* Header: keep its height stable while filters refresh. */}
            <div className="mb-4">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-2">
                  <h2 className="text-xs sm:text-sm font-semibold text-foreground">
                    {t("resultCount", { count: total })}
                  </h2>
                  {isTransitioning && (
                    <Loader2 className="size-3.5 animate-spin text-primary" />
                  )}
                </div>
                <span className="text-xs text-muted-foreground">
                  {t("pageOf", { page: state.page, pages: pageCount })}
                </span>
              </div>
            </div>

            {/* Results Display */}
            <div
              className={cn(
                viewMode === "grid"
                  ? "grid grid-cols-1 xs:grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3"
                  : "space-y-2.5",
              )}
            >
              {results.map((result) =>
                viewMode === "grid" ? (
                  <SearchGridItem
                    key={`${result.search_type}-${result.id}`}
                    result={result}
                  />
                ) : (
                  <SearchListCard
                    key={`${result.search_type}-${result.id}`}
                    result={result}
                    onSelect={openResult}
                  />
                ),
              )}
            </div>

            {/* Pagination Bar */}
            {pageCount > 1 && (
              <nav
                className="mt-8 flex flex-wrap items-center justify-center gap-2 border-t border-border/60 pt-6"
                aria-label={t("pagination")}
              >
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={state.page <= 1}
                  onClick={() => handlePageChange(state.page - 1)}
                  className="gap-1.5 h-9 px-3 sm:px-4 text-xs sm:text-sm"
                >
                  <ChevronLeft className="size-4" />
                  <span>{t("previousPage")}</span>
                </Button>

                <div className="flex items-center gap-1 mx-1 sm:mx-2">
                  <span className="px-3 py-1.5 text-xs font-medium rounded-md bg-muted text-foreground">
                    {state.page}
                  </span>
                  <span className="text-xs text-muted-foreground px-1">/</span>
                  <span className="text-xs text-muted-foreground px-1">{pageCount}</span>
                </div>

                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={state.page >= pageCount}
                  onClick={() => handlePageChange(state.page + 1)}
                  className="gap-1.5 h-9 px-3 sm:px-4 text-xs sm:text-sm"
                >
                  <span>{t("nextPage")}</span>
                  <ChevronRight className="size-4" />
                </Button>
              </nav>
            )}
          </>
        )}
      </section>
      <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
    </div>
  );
}

export default function SearchPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto w-full max-w-5xl px-3.5 py-6 sm:px-6 sm:py-8 lg:px-8">
          <Skeleton className="h-8 w-56 rounded-lg" />
          <Skeleton className="mt-3 h-4 w-96 max-w-full rounded-md" />
          <Skeleton className="mt-7 h-12 w-full rounded-xl" />
        </div>
      }
    >
      <SearchPageContent />
    </React.Suspense>
  );
}
