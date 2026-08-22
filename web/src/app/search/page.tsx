"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileText,
  Folder,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { SearchResultRow } from "@/components/search/search-modal";
import {
  SEARCH_MATERIAL_TYPES,
  SEARCH_STATUSES,
  getValidSearchPage,
  parseSearchPageState,
  updateSearchPageParams,
  type SearchPageKind,
  type SearchMaterialType,
  type SearchStatusFilter,
} from "@/components/search/search-page-state";
import {
  getSearchErrorMessageKey,
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

function SearchPageContent() {
  const t = useTranslations("Search");
  const tTypes = useTranslations("MaterialTypes");
  const params = useSearchParams();
  const router = useRouter();
  const openLink = useExternalLinkStore((store) => store.openLink);
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

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const next = updateSearchPageParams(new URLSearchParams(params.toString()), {
      query: draft.trim().slice(0, 200),
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

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const waiting = status === "debouncing" || status === "loading";

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 pb-24 sm:px-6 sm:pb-10 lg:px-8">
      <header className="mb-6">
        <p className="text-sm font-medium text-primary">{t("pageEyebrow")}</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{t("pageTitle")}</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{t("pageDescription")}</p>
      </header>

      <form onSubmit={submit} role="search" className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t("searchMaterialsDirs")}
            aria-label={t("searchMaterialsDirs")}
            maxLength={200}
            className="h-11 pl-9 pr-9"
          />
          {draft && (
            <button
              type="button"
              onClick={() => setDraft("")}
              aria-label={t("clearSearch")}
              className="absolute right-2 top-1/2 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
        <Button type="submit" className="h-11 px-5">
          {t("searchAction")}
        </Button>
      </form>

      <div className="mt-4 flex flex-wrap items-center gap-2" aria-label={t("filters")}>
        <Select
          value={state.kind}
          onValueChange={(value) =>
            updateFilters({
              kind: value as SearchPageKind,
              materialType: value === "material" ? state.materialType : "all",
            })
          }
        >
          <SelectTrigger size="sm" aria-label={t("filterByKind")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("kinds.all")}</SelectItem>
            <SelectItem value="material"><FileText />{t("kinds.material")}</SelectItem>
            <SelectItem value="directory"><Folder />{t("kinds.directory")}</SelectItem>
          </SelectContent>
        </Select>

        {state.kind !== "directory" && (
          <Select
            value={state.materialType}
            onValueChange={(value) =>
              updateFilters({
                materialType: value as SearchMaterialType,
                kind: value === "all" ? state.kind : "material",
              })
            }
          >
            <SelectTrigger size="sm" aria-label={t("filterByMaterialType")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("allMaterialTypes")}</SelectItem>
              {SEARCH_MATERIAL_TYPES.map((type) => (
                <SelectItem key={type} value={type}>{tTypes(type)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <Select
          value={state.status}
          onValueChange={(value) => updateFilters({ status: value as SearchStatusFilter })}
        >
          <SelectTrigger size="sm" aria-label={t("filterByStatus")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("allStatuses")}</SelectItem>
            {SEARCH_STATUSES.map((value) => (
              <SelectItem key={value} value={value}>{t(`statuses.${value}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        {state.directoryId && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() =>
              updateFilters({ directoryId: undefined, directoryName: undefined, recursive: false })
            }
          >
            {t("scopeLabel", { name: state.directoryName || t("currentFolder") })}
            <X className="size-3.5" />
          </Button>
        )}
      </div>

      <section className="mt-7" aria-live="polite" aria-busy={waiting}>
        {!state.query ? (
          <div className="rounded-2xl border border-dashed px-6 py-20 text-center">
            <Search className="mx-auto size-9 text-muted-foreground/40" />
            <h2 className="mt-4 font-semibold">{t("startTyping")}</h2>
            <p className="mx-auto mt-2 max-w-lg text-sm text-muted-foreground">{t("searchHint")}</p>
          </div>
        ) : waiting ? (
          <div className="space-y-2" role="status">
            <span className="sr-only">{t("searching")}</span>
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="flex items-center gap-3 rounded-xl border p-4">
                <Skeleton className="size-9 rounded-lg" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-2/5" />
                  <Skeleton className="h-3 w-3/5" />
                </div>
              </div>
            ))}
          </div>
        ) : status === "error" ? (
          <div className="rounded-2xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center" role="alert">
            <AlertCircle className="mx-auto size-8 text-destructive" />
            <h2 className="mt-3 font-semibold">{t("searchUnavailable")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {t(getSearchErrorMessageKey(error))}
            </p>
            <Button type="button" variant="outline" size="sm" onClick={retry} className="mt-4">
              {t("retry")}
            </Button>
          </div>
        ) : results.length === 0 ? (
          <div className="rounded-2xl border border-dashed px-6 py-16 text-center">
            <Search className="mx-auto size-8 text-muted-foreground/40" />
            <h2 className="mt-3 font-semibold">{t("noResults")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{t("noResultsHint")}</p>
          </div>
        ) : (
          <>
            <div className="mb-3 flex items-center justify-between gap-4">
              <h2 className="text-sm font-semibold">{t("resultCount", { count: total })}</h2>
              <span className="text-xs text-muted-foreground">
                {t("pageOf", { page: state.page, pages: pageCount })}
              </span>
            </div>
            <div className="overflow-hidden rounded-2xl border bg-card">
              {results.map((result) => (
                <button
                  key={`${result.search_type}-${result.id}`}
                  type="button"
                  onClick={() => openResult(result)}
                  className="flex w-full items-start gap-3 border-b p-4 text-left transition-colors last:border-b-0 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <SearchResultRow result={result} />
                </button>
              ))}
            </div>

            {pageCount > 1 && (
              <nav className="mt-6 flex items-center justify-center gap-3" aria-label={t("pagination")}>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={state.page <= 1}
                  onClick={() => updateFilters({ page: state.page - 1 })}
                >
                  <ChevronLeft />{t("previousPage")}
                </Button>
                <span className="min-w-24 text-center text-sm text-muted-foreground">
                  {t("pageOf", { page: state.page, pages: pageCount })}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={state.page >= pageCount}
                  onClick={() => updateFilters({ page: state.page + 1 })}
                >
                  {t("nextPage")}<ChevronRight />
                </Button>
              </nav>
            )}
          </>
        )}
      </section>
    </div>
  );
}

export default function SearchPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="mt-3 h-4 w-96 max-w-full" />
          <Skeleton className="mt-7 h-11 w-full" />
        </div>
      }
    >
      <SearchPageContent />
    </React.Suspense>
  );
}
