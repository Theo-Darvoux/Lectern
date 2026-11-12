"use client";

import { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ApiError, apiFetch } from "@/lib/api-client";
import { AuthGuard } from "@/components/auth-guard";
import { useUIStore, useConfigStore } from "@/lib/stores";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
import { AlertCircle, Eye, FileQuestion, X } from "lucide-react";
import { useBrowseSSE } from "@/hooks/use-browse-sse";
import type { Operation } from "@/lib/staging-store";
import { browseQuery, isKnownMaterialPath } from "@/lib/browse-prefetch";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useViewMode } from "@/hooks/use-view-mode";

function BrowseSkeleton({ isMaterial = false }: { isMaterial?: boolean }) {
  const { mode: viewMode } = useViewMode();

  if (isMaterial) {
    return (
      <div className="flex flex-1 w-full gap-0 px-4 py-6 pb-36 md:pb-6">
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
    <div className="w-full space-y-4 px-4 py-6 pb-36 md:pb-6">
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


/** The browse path of the parent folder ("" for the root), as an href. */
function parentHref(path: string): string {
  const parent = path.split("/").slice(0, -1).join("/");
  return parent ? `/browse/${parent}` : "/browse";
}

function BrowseContent() {
  const pathname = usePathname();
  const setSidebarTarget = useUIStore((s) => s.setSidebarTarget);

  const t = useTranslations("Browse");
  const config = useConfigStore((state) => state.config);
  const path = pathname.replace(/^\/browse\/?/, "").replace(/\/$/, "");

  // A path already cached (e.g. by a hover prefetch) renders at once and
  // revalidates in the background; another path shows a skeleton, never the
  // previous directory's payload.
  const browse = useQuery(browseQuery(path));
  const data = browse.data ?? null;
  const notFound = !data && browse.error instanceof ApiError && browse.error.status === 404;

  const searchParams = useSearchParams();
  const previewPrId = searchParams.get("preview_pr");
  // The preview banner is optional: if the PR cannot load, the listing shows as is.
  const { data: previewPr = null } = useQuery({
    queryKey: ["pull-requests", previewPrId],
    queryFn: ({ signal }) =>
      apiFetch<{ id: string; title: string; payload: Operation[] }>(`/pull-requests/${previewPrId}`, {
        signal,
      }),
    enabled: !!previewPrId,
  });

  // The first listing of a path seeds the inspector with its folder; later
  // refetches only refresh the counts, and only while the inspector still
  // shows that folder (the user may have selected another item since).
  const seededPathRef = useRef<string | null>(null);
  useEffect(() => {
    if (data?.type !== "directory_listing") return;
    const dir = data.directory;
    const id = dir ? dir.id : "root";
    if (seededPathRef.current === path) {
      const current = useUIStore.getState().sidebarTarget;
      if (current?.type !== "directory" || current.id !== id) return;
    }
    seededPathRef.current = path;
    setSidebarTarget({
      type: "directory",
      id,
      data: {
        ...(dir ?? { name: t("home"), type: "folder" }),
        child_directory_count: data.directories.length,
        child_material_count: data.materials.length,
      },
    });
  }, [data, path, setSidebarTarget, t]);

  useEffect(() => {
    if (!data) return;
    const siteName = config?.site_name || "";
    const name =
      data.type === "material" ? data.material.title : data.directory?.name ?? null;
    if (name) {
      document.title = siteName ? `${name} • ${siteName}` : name;
    } else if (path === "") {
      document.title = siteName;
    }
  }, [data, path, config]);

  useBrowseSSE(data, path);

  let inner: React.ReactNode;
  if (!data && browse.isPending) {
    inner = <BrowseSkeleton isMaterial={isKnownMaterialPath(path)} />;
  } else if (notFound) {
    inner = (
      <div className="flex w-full flex-col items-center justify-center gap-5 px-4 py-20 text-muted-foreground">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <FileQuestion className="h-6 w-6" />
        </span>
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-lg font-medium text-foreground">{t("notFoundTitle")}</p>
          <p className="max-w-md text-sm">{t("notFoundDescription")}</p>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2">
          {path.includes("/") && (
            <Button variant="outline" size="sm" asChild>
              <Link href={parentHref(path)}>{t("backToParentFolder")}</Link>
            </Button>
          )}
          <Button size="sm" asChild>
            <Link href="/browse">{t("backToBrowse")}</Link>
          </Button>
        </div>
      </div>
    );
  } else if (!data) {
    inner = (
      <div className="flex w-full flex-col items-center justify-center gap-5 px-4 py-20 text-muted-foreground">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertCircle className="h-6 w-6" />
        </span>
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-lg font-medium text-foreground">{t("loadFailedTitle")}</p>
          <p className="max-w-md text-sm">{t("loadError")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/browse">{t("backToBrowse")}</Link>
          </Button>
          <Button size="sm" onClick={() => void browse.refetch()}>{t("retry")}</Button>
        </div>
      </div>
    );
  } else if (data.type === "material") {
    inner = (
      <MaterialViewer material={data.material} breadcrumbs={data.breadcrumbs} />
    );
  } else {
    inner = (
      <div className="relative flex h-full w-full overflow-hidden gap-0">
        <div className="flex-1 min-h-0 min-w-0 overflow-y-auto px-4 py-6 pb-36 md:pb-6">
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

          <DirectoryListing
            directory={data.directory}
            directories={data.directories}
            materials={data.materials}
            breadcrumbs={data.breadcrumbs}
            previewOperations={previewPr?.payload}
            previewPrId={previewPr?.id}
          />
          <div className="h-28 md:hidden shrink-0 pointer-events-none" aria-hidden="true" />
        </div>
        <SharedSidebar />
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
