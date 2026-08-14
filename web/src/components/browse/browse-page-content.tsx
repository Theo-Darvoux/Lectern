"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { apiFetch } from "@/lib/api-client";
import { AuthGuard } from "@/components/auth-guard";
import { useIsDesktop } from "@/hooks/use-media-query";
import { useUIStore, useBrowseRefreshStore, useConfigStore } from "@/lib/stores";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
import { AlertCircle, Eye, X } from "lucide-react";
import { useBrowseSSE } from "@/hooks/use-browse-sse";
import type { Operation } from "@/lib/staging-store";
import { browseCache, setPreviousBrowsePath, fetchBrowsePath, invalidateBrowsePath } from "@/lib/browse-prefetch";
import dynamic from "next/dynamic";

interface BrowseResponse {
  type: "directory_listing" | "material";
  directory?: Record<string, unknown> | null;
  directories?: Record<string, unknown>[];
  materials?: Record<string, unknown>[];
  material?: Record<string, unknown>;
  breadcrumbs?: { id: string; name: string; slug: string }[];
}

function BrowseSkeleton({ isMaterial = false }: { isMaterial?: boolean }) {
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  useEffect(() => {
    try {
      const stored = localStorage.getItem("browse-view-mode");
      if (stored === "grid" || stored === "list") setViewMode(stored);
    } catch {}
  }, []);

  if (isMaterial) {
    return (
      <div className="flex flex-1 w-full gap-0 px-4 py-6 pb-20 md:pb-6">
        <div className="flex-1 space-y-4 pr-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Skeleton className="h-10 w-10 rounded-md" />
              <div>
                <Skeleton className="h-6 w-48 mb-2" />
                <Skeleton className="h-4 w-24" />
              </div>
            </div>
            <Skeleton className="h-10 w-28 rounded-md" />
          </div>
          <div className="flex w-full flex-col items-center justify-start py-4 md:py-8">
            <div className="flex w-full max-w-4xl aspect-[1/1.414] flex-col rounded bg-white p-8 shadow-sm dark:bg-zinc-950/50">
              <Skeleton className="mb-12 h-10 w-3/4 rounded-md" />
              <div className="space-y-4">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-[90%]" />
                <Skeleton className="h-4 w-[95%]" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-[85%]" />
              </div>
              <div className="mt-12 space-y-4">
                <Skeleton className="h-4 w-[92%]" />
                <Skeleton className="h-4 w-[88%]" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-[96%]" />
              </div>
            </div>
          </div>
        </div>
        <div className="hidden w-[30%] min-w-[300px] shrink-0 border-l px-4 py-0 md:block">
          <Skeleton className="h-8 w-full mb-4" />
          <Skeleton className="h-24 w-full mb-4" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="w-full space-y-4 px-4 py-6 pb-20 md:pb-6">
      {/* Breadcrumb row */}
      <div className="flex items-center gap-2">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-4 rounded-full" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-4 rounded-full" />
        <Skeleton className="h-4 w-32" />
      </div>
      {/* Toolbar row */}
      <div className="flex items-center justify-between h-11">
        <div className="flex items-center gap-2">
          <Skeleton className="h-8 w-16 rounded-md" />
          <Skeleton className="h-8 w-8 rounded-md" />
        </div>
        <div className="flex items-center gap-2">
          <Skeleton className="h-8 w-24 rounded-md" />
          <Skeleton className="h-8 w-24 rounded-md" />
        </div>
      </div>
      {viewMode === "grid" ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
          {Array.from({ length: 10 }, (_, i) => (
            <div key={i} className="rounded-lg border overflow-hidden">
              <Skeleton className="aspect-[4/3] w-full rounded-none" />
              <div className="p-2 space-y-1">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="divide-y rounded-lg border">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="h-5 w-5 shrink-0 rounded" />
              <div className="flex-1 min-w-0 space-y-1">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/4" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const DirectoryListing = dynamic(() => import("@/components/browse/directory-listing").then(mod => mod.DirectoryListing), {
  loading: () => <BrowseSkeleton isMaterial={false} />,
  ssr: false
});

const MaterialViewer = dynamic(() => import("@/components/browse/material-viewer").then(mod => mod.MaterialViewer), {
  loading: () => <BrowseSkeleton isMaterial={true} />,
  ssr: false
});

const SharedSidebar = dynamic(() => import("@/components/sidebar/shared-sidebar").then(mod => mod.SharedSidebar), {
  loading: () => <Skeleton className="h-full w-full" />,
  ssr: false
});

const DirectoryTreeSidebar = dynamic(() => import("@/components/browse/directory-tree-sidebar").then(mod => mod.DirectoryTreeSidebar), {
  loading: () => null,
  ssr: false
});


function BrowseContent() {
  const pathname = usePathname();
  const isDesktop = useIsDesktop();
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const sidebarTarget = useUIStore((s) => s.sidebarTarget);
  const setSidebarTarget = useUIStore((s) => s.setSidebarTarget);
  const refreshCount = useBrowseRefreshStore((s) => s.refreshCount);
  const triggerBrowseRefresh = useBrowseRefreshStore((s) => s.triggerBrowseRefresh);

  const t = useTranslations("Browse");
  const config = useConfigStore((state) => state.config);
  const path = pathname.replace(/^\/browse\/?/, "").replace(/\/$/, "");

  // Track the path each fetched payload belongs to so a stale payload from the
  // previous directory is never rendered against the new path (no flash).
  const [fetched, setFetched] = useState<{ path: string; data: BrowseResponse } | null>(
    () => (browseCache.has(path) ? { path, data: browseCache.get(path) as BrowseResponse } : null),
  );
  const [isFetching, setIsFetching] = useState(!browseCache.has(path));
  const [error, setError] = useState<string | null>(null);

  // Cache-first: a freshly prefetched/cached path renders instantly, even on the
  // first frame after navigation (before the effect below runs). Falls back to
  // the fetched payload only when it matches the current path.
  const data: BrowseResponse | null = browseCache.has(path)
    ? (browseCache.get(path) as BrowseResponse)
    : fetched?.path === path
      ? fetched.data
      : null;

  const searchParams = useSearchParams();
  const previewPrId = searchParams.get("preview_pr");
  const [previewPr, setPreviewPr] = useState<{
    id: string;
    title: string;
    payload: Operation[];
  } | null>(null);

  useEffect(() => {
    if (previewPrId) {
      apiFetch<{ id: string; title: string; payload: Operation[] }>(`/pull-requests/${previewPrId}`)
        .then((pr) => {
          setPreviewPr({
            id: pr.id,
            title: pr.title,
            payload: pr.payload,
          });
        })
        .catch(() => setPreviewPr(null));
    } else {
      setPreviewPr(null);
    }
  }, [previewPrId]);

  const fetchData = useCallback(
    async (isBackground = false) => {
      if (!isBackground) setIsFetching(true);
      setError(null);
      try {
        // Background fetches revalidate (bypass cache); foreground fetches join
        // any in-flight prefetch for this path instead of duplicating it.
        const result = (await fetchBrowsePath(path, {
          force: isBackground,
        })) as BrowseResponse;
        setPreviousBrowsePath(path);
        // Bail out of the state update when a background revalidation returned
        // the same (identity-preserved) payload — avoids a redundant full
        // re-render of the listing on every cache-hit navigation.
        setFetched((prev) =>
          prev && prev.path === path && prev.data === result
            ? prev
            : { path, data: result },
        );
      } catch (err) {
        if (!isBackground) {
          setError(err instanceof Error ? err.message : t("loadError"));
        }
      } finally {
        if (!isBackground) setIsFetching(false);
      }
    },
    [path],
  );

  // On path change: if the target is already cached (e.g. via hover-prefetch),
  // render it instantly (via the cache-first `data` above) and revalidate in the
  // background. Otherwise show a skeleton while we fetch the target.
  useEffect(() => {
    if (browseCache.has(path)) {
      setError(null);
      setIsFetching(false);
      fetchData(true);
    } else {
      fetchData(false);
    }
  }, [path, fetchData]);

  const isDirectoryListing = data?.type === "directory_listing";
  const seededPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isDirectoryListing) return;

    const dir = data?.directory;
    const currentDirId = dir ? String(dir.id) : path === "" ? "root" : null;
    if (!currentDirId) return;

    if (seededPathRef.current === path) return;
    seededPathRef.current = path;

    if (dir) {
      setSidebarTarget({
        type: "directory",
        id: currentDirId,
        data: {
          ...dir,
          child_directory_count: data?.directories?.length ?? 0,
          child_material_count: data?.materials?.length ?? 0,
        },
      });
    } else {
      setSidebarTarget({
        type: "directory",
        id: "root",
        data: {
          name: t("home"),
          type: "folder",
          child_directory_count: data?.directories?.length ?? 0,
          child_material_count: data?.materials?.length ?? 0,
        },
      });
    }
  }, [isDirectoryListing, path, data, setSidebarTarget, t]);

  useEffect(() => {
    if (!isDirectoryListing || !data) return;
    const dir = data.directory;
    const currentDirId = dir ? String(dir.id) : path === "" ? "root" : null;
    if (!currentDirId) return;
    if (
      sidebarTarget?.type === "directory" &&
      sidebarTarget.id === currentDirId
    ) {
      setSidebarTarget({
        type: "directory",
        id: currentDirId,
        data: dir
          ? {
            ...dir,
            child_directory_count: data.directories?.length ?? 0,
            child_material_count: data.materials?.length ?? 0,
          }
          : {
            name: t("home"),
            type: "folder",
            child_directory_count: data.directories?.length ?? 0,
            child_material_count: data.materials?.length ?? 0,
          },
      });
    }
  }, [isDirectoryListing, data, path, setSidebarTarget, t]);

  useEffect(() => {
    if (data) {
      const siteName = config?.site_name || "";
      if (data.type === "material" && data.material) {
        document.title = `${data.material.title} • ${siteName}`;
      } else if (data.directory) {
        document.title = `${data.directory.name as string} • ${siteName}`;
      } else if (path === "") {
        document.title = `${t("courseMaterials")} • ${siteName}`;
      }
    }
  }, [data, path, config]);

  const prevRefreshCountRef = useRef(refreshCount);
  useEffect(() => {
    if (refreshCount > prevRefreshCountRef.current) {
      prevRefreshCountRef.current = refreshCount;
      invalidateBrowsePath(path);
      fetchData(true);
    }
  }, [path, refreshCount, fetchData]);

  useBrowseSSE(data, path, fetchData, triggerBrowseRefresh);

  const isLikelyMaterial = path.split("/").filter(Boolean).length >= 3;

  const isDirectoryView = data?.type === "directory_listing";

  let inner: React.ReactNode;
  if (!data && isFetching) {
    inner = <BrowseSkeleton isMaterial={isLikelyMaterial} />;
  } else if (error) {
    inner = (
      <div className="flex w-full flex-col items-center justify-center gap-5 px-4 py-20 text-muted-foreground">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertCircle className="h-6 w-6" />
        </span>
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-lg font-medium text-foreground">{t("loadFailedTitle")}</p>
          <p className="max-w-md text-sm">{error}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/browse">{t("backToBrowse")}</Link>
          </Button>
          <Button size="sm" onClick={() => fetchData(false)}>{t("retry")}</Button>
        </div>
      </div>
    );
  } else if (!data) {
    inner = null;
  } else if (data.type === "material" && data.material) {
    inner = (
      <MaterialViewer material={data.material} breadcrumbs={data.breadcrumbs} />
    );
  } else {
    inner = (
      <div
        className={`flex h-full w-full overflow-hidden gap-0 transition-opacity duration-200 ${isFetching ? "opacity-50 pointer-events-none" : "opacity-100"}`}
      >
        <div
          className={`flex-1 min-h-0 overflow-y-auto px-4 py-6 pb-20 md:pb-6 ${isDesktop && sidebarOpen ? "min-w-0" : ""}`}
        >
          {previewPr && (
            <div className="mb-6 flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50/50 px-4 py-3 dark:border-blue-800/40 dark:bg-blue-950/20">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100 dark:bg-blue-900/50">
                  <Eye className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-200">
                    {t("contributionPreview")}
                  </h3>
                  <p className="text-xs text-muted-foreground truncate">
                    {previewPr.title}
                  </p>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="gap-2 text-blue-700 hover:bg-blue-100 dark:text-blue-300 dark:hover:bg-blue-900/50"
                asChild
              >
                <Link href={path ? `/browse/${path}` : "/browse"}>
                  <X className="h-4 w-4" />
                  {t("exitPreview")}
                </Link>
              </Button>
            </div>
          )}

          {isDirectoryView && (
            <DirectoryListing
              directory={data.directory ?? null}
              directories={data.directories ?? []}
              materials={data.materials ?? []}
              breadcrumbs={data.breadcrumbs}
              previewOperations={previewPr?.payload}
              previewPrId={previewPr?.id}
            />
          )}
        </div>
        {!isDesktop && <SharedSidebar />}
        {isDesktop && isDirectoryView && <SharedSidebar />}
      </div>
    );
  }

  return (
    <div className="flex h-full w-full overflow-hidden">
      <DirectoryTreeSidebar />
      <div className="flex-1 min-w-0 min-h-0 flex overflow-hidden">
        {inner}
      </div>
    </div>
  );
}

export function BrowsePageContent() {
  return (
    <AuthGuard requireOnboarded>
      <BrowseContent />
    </AuthGuard>
  );
}
