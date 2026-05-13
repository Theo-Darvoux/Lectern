"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { apiFetch } from "@/lib/api-client";
import { AuthGuard } from "@/components/auth-guard";
import { DirectoryListing } from "@/components/browse/directory-listing";
import { MaterialViewer } from "@/components/browse/material-viewer";
import { SharedSidebar } from "@/components/sidebar/shared-sidebar";
import { useIsDesktop } from "@/hooks/use-media-query";
import { useUIStore, useBrowseRefreshStore } from "@/lib/stores";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
import { Eye, X } from "lucide-react";
import { createSSEConnection } from "@/lib/sse-client";
import type { Operation } from "@/lib/staging-store";

interface BrowseResponse {
  type: "directory_listing" | "material" | "attachment_listing";
  directory?: Record<string, unknown> | null;
  directories?: Record<string, unknown>[];
  materials?: Record<string, unknown>[];
  material?: Record<string, unknown>;
  parent_material?: Record<string, unknown> | null;
  breadcrumbs?: { id: string; name: string; slug: string }[];
}

function BrowseSkeleton({ isMaterial = false }: { isMaterial?: boolean }) {
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
            {/* A4 proportioned paper skeleton */}
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
    <div className="space-y-4 px-4 py-6 pb-20 md:pb-6">
      <Skeleton className="h-6 w-48" />
      <div className="divide-y rounded-lg border">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3">
            <Skeleton className="h-5 w-5 rounded" />
            <div className="flex-1 space-y-1">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/4" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const browseCache = new Map<string, BrowseResponse>();
let previousPath: string | null = null;

function BrowseContent() {
  const params = useParams();
  const router = useRouter();
  const isDesktop = useIsDesktop();
  const { sidebarOpen, sidebarTarget, setSidebarTarget } = useUIStore();
  const refreshCount = useBrowseRefreshStore((s) => s.refreshCount);

  const t = useTranslations("Browse");
  const path = params.path
    ? Array.isArray(params.path)
      ? params.path.join("/")
      : params.path
    : "";

  const getInitialData = () => {
    if (browseCache.has(path)) return browseCache.get(path)!;
    if (previousPath && browseCache.has(previousPath))
      return browseCache.get(previousPath)!;
    return null;
  };

  const [data, setData] = useState<BrowseResponse | null>(getInitialData);
  const [isFetching, setIsFetching] = useState(!browseCache.has(path));
  const [error, setError] = useState<string | null>(null);

  // PR Preview mode
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
        const endpoint = path ? `/browse/${path}` : "/browse";
        const result = await apiFetch<BrowseResponse>(endpoint);
        browseCache.set(path, result);
        previousPath = path;
        setData(result);
      } catch (err) {
        if (!isBackground) {
          setError(err instanceof Error ? err.message : t("loadError"));
          setData(null);
        }
      } finally {
        if (!isBackground) setIsFetching(false);
      }
    },
    [path],
  );

  useEffect(() => {
    // If path changed, it's a fresh load
    fetchData(false);
  }, [path, fetchData]);

  // Sync the sidebar target with the current directory context.
  //
  // Key rule: seed only when the path (i.e. the directory the user is
  // browsing) changes. Background refetches on the same path — e.g. a like or
  // favourite triggering triggerBrowseRefresh — must NOT reset a child target
  // the user explicitly opened. That was the source of the "sidebar jumps
  // back to the parent directory after liking a material" bug.
  const isDirectoryListing = data?.type === "directory_listing";
  const seededPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isDirectoryListing) return;

    const dir = data?.directory;
    const currentDirId = dir ? String(dir.id) : path === "" ? "root" : null;
    if (!currentDirId) return;

    // Only seed once per path. Subsequent refetches on the same path leave
    // the user-selected target alone.
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

  // If the directory data refreshes on the same path AND the sidebar is still
  // showing this directory, refresh its data in place (counts, like state)
  // without disturbing a child target.
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
    // sidebarTarget intentionally omitted to avoid re-firing on every store
    // update — we only want to react to data refreshes.
     
  }, [isDirectoryListing, data, path, setSidebarTarget, t]);

  useEffect(() => {
    if (data) {
      if (data.type === "material" && data.material) {
        document.title = `${data.material.title} • WikINT`;
      } else if (data.directory) {
        document.title = `${data.directory.name as string} • WikINT`;
      } else if (path === "") {
        document.title = `${t("courseMaterials")} • WikINT`;
      }
    }
  }, [data, path]);

  const prevRefreshCountRef = useRef(refreshCount);
  useEffect(() => {
    if (refreshCount > prevRefreshCountRef.current) {
      prevRefreshCountRef.current = refreshCount;
      browseCache.delete(path);
      // Use background fetch (isBackground=true) to avoid dimming/opacity flicker
      // when liking/starring from the sidebar.
      fetchData(true);
    }
  }, [path, refreshCount, fetchData]);

  // Redirect on deletion / refresh on child creation via SSE
  useEffect(() => {
    if (!data) return;

    let sseUrl: string | null = null;
    const listeners: Record<string, () => void> = {};
    const breadcrumbSlugs = data.breadcrumbs?.map((b) => b.slug) ?? [];

    if (data.type === "material" && data.material) {
      sseUrl = `/materials/${data.material.id}/sse`;
      const parentPath = breadcrumbSlugs.length > 0 ? `/browse/${breadcrumbSlugs.join("/")}` : "/browse";
      listeners["material_deleted"] = () => {
        browseCache.delete(path);
        router.replace(parentPath);
      };
    } else if (data.type === "directory_listing") {
      const dirId = data.directory ? String(data.directory.id) : "root";
      sseUrl = `/directories/${dirId}/sse`;
      if (data.directory) {
        const parentSlugs = breadcrumbSlugs.slice(0, -1);
        const parentPath = parentSlugs.length > 0 ? `/browse/${parentSlugs.join("/")}` : "/browse";
        listeners["directory_deleted"] = () => {
          browseCache.delete(path);
          router.replace(parentPath);
        };
      }
      listeners["child_added"] = () => {
        browseCache.delete(path);
        fetchData(true);
      };
    } else if (data.type === "attachment_listing" && data.parent_material) {
      sseUrl = `/materials/${(data.parent_material as Record<string, unknown>).id}/sse`;
      const parentPath = breadcrumbSlugs.length > 0 ? `/browse/${breadcrumbSlugs.join("/")}` : "/browse";
      listeners["material_deleted"] = () => {
        browseCache.delete(path);
        router.replace(parentPath);
      };
    }

    if (!sseUrl || Object.keys(listeners).length === 0) return;

    const connection = createSSEConnection({
      url: sseUrl,
      listeners,
      startupDelay: 50,
    });

    return () => connection.close();
  }, [data, path, router, fetchData]);

  const isLikelyMaterial = Boolean(
    params.path && Array.isArray(params.path) && params.path.length >= 3,
  );

  if (!data && isFetching)
    return <BrowseSkeleton isMaterial={isLikelyMaterial} />;

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 px-4 text-muted-foreground">
        <p className="text-lg font-medium">{t("notFound")}</p>
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (!data) return null;

  const isDirectoryView =
    data.type === "directory_listing" || data.type === "attachment_listing";

  if (data.type === "material" && data.material) {
    return (
      <MaterialViewer material={data.material} breadcrumbs={data.breadcrumbs} />
    );
  }

  return (
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
            isAttachmentListing={data.type === "attachment_listing"}
            parentMaterial={data.parent_material ?? null}
            previewOperations={previewPr?.payload}
            previewPrId={previewPr?.id}
          />
        )}
      </div>
      {!isDesktop && <SharedSidebar />}
      {isDesktop && isDirectoryView && (
        <SharedSidebar />
      )}
    </div>
  );
}

export default function BrowsePage() {
  return (
    <AuthGuard requireOnboarded>
      <BrowseContent />
    </AuthGuard>
  );
}
