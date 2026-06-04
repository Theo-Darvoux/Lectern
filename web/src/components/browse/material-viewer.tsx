"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  ArrowLeft,
  Download,
  Printer,
  MoreVertical,
  Loader2,
  PanelRight,
  FileText,
  Code2,
  Paperclip,
} from "lucide-react";
import { useIsMobile, useIsDesktop } from "@/hooks/use-media-query";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  formatFileSize,
  getFileBadgeColor,
  getFileBadgeLabel,
  getViewerType,
} from "@/lib/file-utils";
import { useUIStore, useAuthStore } from "@/lib/stores";
import { isGuest } from "@/lib/guest";
// useUIStore provides: sidebarOpen, openSidebar, closeSidebar
import { apiFetch } from "@/lib/api-client";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import type { QCMFile } from "@/lib/qcm-types";
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

// --- Dynamic Viewer Imports ---
// This prevents large libraries (like react-pdf, mermaid, monaco) from being compiled
// simultaneously when only one is needed, drastically reducing dev memory pressure.

const PdfViewer = dynamic(
  () => import("@/components/viewers/pdf-viewer").then((mod) => mod.PdfViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const MarkdownViewer = dynamic(
  () =>
    import("@/components/viewers/markdown-viewer").then(
      (mod) => mod.MarkdownViewer,
    ),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const ImageViewer = dynamic(
  () =>
    import("@/components/viewers/image-viewer").then((mod) => mod.ImageViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const SvgViewer = dynamic(
  () =>
    import("@/components/viewers/svg-viewer").then((mod) => mod.SvgViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const VideoPlayer = dynamic(
  () =>
    import("@/components/viewers/video-player").then((mod) => mod.VideoPlayer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const AudioPlayer = dynamic(
  () =>
    import("@/components/viewers/audio-player").then((mod) => mod.AudioPlayer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const CodeViewer = dynamic(
  () =>
    import("@/components/viewers/code-viewer").then((mod) => mod.CodeViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const CsvViewer = dynamic(
  () => import("@/components/viewers/csv-viewer").then((mod) => mod.CsvViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const OfficeViewer = dynamic(
  () =>
    import("@/components/viewers/office-viewer").then(
      (mod) => mod.OfficeViewer,
    ),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const EpubViewer = dynamic(
  () =>
    import("@/components/viewers/epub-viewer").then((mod) => mod.EpubViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const DjvuViewer = dynamic(
  () =>
    import("@/components/viewers/djvu-viewer").then((mod) => mod.DjvuViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const GenericViewer = dynamic(
  () =>
    import("@/components/viewers/generic-viewer").then(
      (mod) => mod.GenericViewer,
    ),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);

const QCMViewer = dynamic(
  () =>
    import("@/components/viewers/qcm-viewer").then((mod) => mod.QCMViewer),
  {
    loading: () => <Skeleton className="h-full w-full rounded-none" />,
    ssr: false,
  },
);


import { SharedSidebar } from "@/components/sidebar/shared-sidebar";
import { ViewerFab } from "@/components/browse/viewer-fab";
import { Breadcrumbs } from "@/components/browse/breadcrumbs";
import { AnnotationSelectionTooltip } from "@/components/annotations/annotation-selection-tooltip";
import { useAnnotations, AnnotationsContext } from "@/hooks/use-annotations";
import { ItemActionsMenu, ItemActionsDropdownTrigger, type ItemData } from "@/components/browse/item-actions-menu";
import { useDownload } from "@/hooks/use-download";
import { usePrint } from "@/hooks/use-print";
import { useTranslations } from "next-intl";

interface MaterialViewerProps {
  material: Record<string, unknown>;
  breadcrumbs?: { id: string; name: string; slug: string }[];
}



export function MaterialViewer({
  material,
  breadcrumbs = [],
}: MaterialViewerProps) {
  const t = useTranslations("Browse");
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const isRestricted = (material.id as string)?.startsWith("$") || !!searchParams.get("preview_pr");
  const guest = isGuest(useAuthStore((s) => s.user));

  // Derive the parent folder URL by dropping the last path segment
  const parentFolderHref = (() => {
    const stripped = pathname.replace(/^\/browse\/?/, "").replace(/\/$/, "");
    const segments = stripped ? stripped.split("/") : [];
    const parentSegments = segments.slice(0, -1);
    return parentSegments.length > 0
      ? `/browse/${parentSegments.join("/")}`
      : "/browse";
  })();
  const isMobile = useIsMobile();
  const isDesktop = useIsDesktop();
  const {
    openSidebar,
    setSidebarTarget,
    closeSidebar,
    sidebarOpen,
    setHideFooter,
    materialActionsOpen,
    setMaterialActionsOpen,
    setActiveViewerType,
    navbarVisible,
    setNavbarVisible,
  } = useUIStore();
  const viewerContainerRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const [headerHeight, setHeaderHeight] = useState(0);
  const headerHeightRef = useRef(0);
  useEffect(() => { headerHeightRef.current = headerHeight; }, [headerHeight]);
  const navbarVisibleRef = useRef(navbarVisible);
  const hoveredOpen = useRef(false);
  useEffect(() => { navbarVisibleRef.current = navbarVisible; }, [navbarVisible]);

  // Hide footer, enter immersive mode, and prevent page scroll while viewer is active
  useEffect(() => {
    setHideFooter(true);
    setNavbarVisible(true);
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    return () => {
      setHideFooter(false);
      setNavbarVisible(true);
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
    };
  }, [setHideFooter, setNavbarVisible]);

  // Measure the header height so the viewer can reserve exactly that space permanently.
  // Using ResizeObserver means it stays accurate if fonts/content change.
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setHeaderHeight(entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Show navbar on mouse proximity to top edge; hide again when mouse leaves (only if we opened it).
  useEffect(() => {
    if (isMobile) return;
    const THRESHOLD = 20;
    const onMouseMove = (e: MouseEvent) => {
      if (e.clientY < THRESHOLD) {
        if (!navbarVisibleRef.current) {
          hoveredOpen.current = true;
          setNavbarVisible(true);
        }
      } else if (hoveredOpen.current && e.clientY > (headerHeightRef.current || THRESHOLD)) {
        // Only hide once the cursor drops below the navbar itself, so the user
        // can move down onto the breadcrumbs / download button without it closing.
        hoveredOpen.current = false;
        setNavbarVisible(false);
      }
    };
    window.addEventListener("mousemove", onMouseMove);
    return () => window.removeEventListener("mousemove", onMouseMove);
  }, [isMobile, setNavbarVisible]);



  const materialId = String(material.id ?? "");

  // Overlay any staged (draft) edits for this material so the viewer reflects
  // the drafted version rather than the live one.
  const stagingOps = useStagingStore((s) => s.operations);
  const stagedEdit = useMemo(() => {
    const op = stagingOps.map(unwrapOp).find(
      (o) => o.op === "edit_material" && o.material_id === materialId,
    );
    return op?.op === "edit_material" ? op : null;
  }, [stagingOps, materialId]);

  const displayMaterial = useMemo(() => {
    if (!stagedEdit) return material;
    return {
      ...material,
      ...(stagedEdit.title != null ? { title: stagedEdit.title } : {}),
      ...(stagedEdit.description != null ? { description: stagedEdit.description } : {}),
      ...(stagedEdit.tags != null ? { tags: stagedEdit.tags } : {}),
    };
  }, [material, stagedEdit]);

  // For QCM: pass the staged draft data directly to QCMViewer to bypass fetch.
  const stagedQcmDraft = useMemo(() => {
    if (!stagedEdit) return null;
    return (stagedEdit.metadata as Record<string, unknown> | undefined)?.qcm_draft ?? null;
  }, [stagedEdit]);

  const title = String(displayMaterial.title ?? "");
  const directoryId = String(material.directory_id ?? "");
  const parentMaterialId = material.parent_material_id
    ? String(material.parent_material_id)
    : null;
  const versionInfo = material.current_version_info as Record<
    string,
    unknown
  > | null;
  const fileName = String(versionInfo?.file_name ?? "");
  const fileSize = Number(versionInfo?.file_size ?? 0);
  const mimeType = String(
    versionInfo?.file_mime_type ?? "application/octet-stream",
  );
  const fileKey = String(versionInfo?.file_key ?? "");

  // Record view in background
  useEffect(() => {
    if (!materialId) return;
    apiFetch(`/materials/${materialId}/view`, { method: "POST" }).catch(() => {
      // Silently fail if view tracking fails
    });
  }, [materialId]);

  const viewerType = getViewerType(mimeType, fileName);

  useEffect(() => {
    setActiveViewerType(viewerType);
    return () => setActiveViewerType(null);
  }, [viewerType, setActiveViewerType]);

  const annotationsData = useAnnotations(materialId);
  const { createAnnotation, threads } = annotationsData;
  const { downloadMaterial, downloadQcmAsXml, downloadQcmAsPdf, isDownloading } = useDownload();
  const { print, isPrinting, canPrint } = usePrint({
    viewerType,
    materialId,
    fileName,
    mimeType,
  });

  // Intercept Ctrl+P to print the material instead of the whole page
  useEffect(() => {
    if (!canPrint) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "p") {
        e.preventDefault();
        print();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [canPrint, print]);

  const handleAnnotationSubmit = async (
    body: string,
    selectionText: string,
    positionData: Record<string, unknown>,
  ) => {
    const docPage =
      typeof positionData.page === "number" ? positionData.page : undefined;
    await createAnnotation(body, selectionText, positionData, docPage);
    openSidebar("annotations", {
      type: "material",
      id: materialId,
      data: material,
    });
  };

  useEffect(() => {
    // Seed the sidebar target with the current material so any updates
    // (likes, favourites) flow through the shared store and stay in sync
    // across surfaces (FAB, Details tab). Runs on both desktop and mobile.
    setSidebarTarget({
      type: "material",
      id: materialId,
      data: { ...displayMaterial, __viewerType: viewerType },
    });
    // Depend on materialId/viewerType only — `material` is a fresh object each
    // render, so including it would re-fire the effect on unrelated parent
    // re-renders and override the user's currently-selected sidebar tab.
     
  }, [materialId, viewerType, setSidebarTarget]);

  return (
    <AnnotationsContext.Provider value={annotationsData}>
      <div className="flex h-full w-full overflow-hidden gap-0">
        <div className="flex-1 flex flex-col min-w-0 min-h-0 relative">
          {/*
           * Header — absolutely positioned so it overlays the viewer.
           * Slides in/out via translateY (GPU composited, zero layout reflow).
           * The viewer below has a permanent paddingTop equal to the measured
           * header height, so it never resizes when the header shows/hides.
           */}
          <div
            ref={headerRef}
            className="absolute top-0 left-0 right-0 z-20 flex flex-col gap-3 p-2 sm:p-4 md:p-6 bg-background/95 backdrop-blur-sm transition-transform duration-300 ease-in-out"
            style={{ transform: navbarVisible ? "translateY(0)" : "translateY(-110%)" }}
          >
          <div>
            <Breadcrumbs items={breadcrumbs} linkLast={true} />
          </div>

          {/* Compact header */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0"
                onClick={() => router.push(parentFolderHref)}
                title={t("backToParentFolder")}
              >
                <ArrowLeft className="h-5 w-5" />
              </Button>
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-base sm:text-lg font-semibold truncate">
                    {title}
                  </h1>
                  <span
                    className={`inline-block shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${getFileBadgeColor(fileName)}`}
                  >
                    {getFileBadgeLabel(fileName, mimeType)}
                  </span>
                </div>
                {fileSize > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {formatFileSize(fileSize)}
                  </p>
                )}
              </div>
            </div>
            {isMobile ? (
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0"
                onClick={() => setMaterialActionsOpen(true)}
                aria-label={t("documentActions")}
              >
                <MoreVertical className="h-5 w-5" />
              </Button>
            ) : (
              <div className="flex items-center gap-2 shrink-0">
                <ItemActionsMenu
                  item={{
                    id: materialId,
                    type: "material",
                    data: displayMaterial,
                  } as ItemData}
                  itemPath={pathname}
                >
                    <div className="flex items-center gap-2">
                      <ItemActionsDropdownTrigger />
                      {canPrint && (
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-8 w-8 shrink-0"
                          onClick={() => print()}
                          disabled={isPrinting}
                          title={t("printDocument")}
                        >
                          {isPrinting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Printer className="h-4 w-4" />
                          )}
                        </Button>
                      )}
                      {viewerType === "qcm" ? (
                        <DropdownMenu modal={false}>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="outline"
                              size="icon"
                              className="h-8 w-8 shrink-0"
                              disabled={isDownloading}
                              title={t("downloadDocument")}
                            >
                              {isDownloading ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <Download className="h-4 w-4" />
                              )}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => downloadQcmAsPdf(materialId, title)} className="cursor-pointer">
                              <FileText className="mr-2 h-4 w-4" />
                              <span>{t("downloadPdf")}</span>
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => downloadQcmAsXml(materialId)} className="cursor-pointer">
                              <Code2 className="mr-2 h-4 w-4" />
                              <span>{t("downloadXml")}</span>
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : (
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-8 w-8 shrink-0"
                          onClick={() => downloadMaterial(materialId)}
                          disabled={isDownloading}
                          title={t("downloadDocument")}
                        >
                          {isDownloading ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Download className="h-4 w-4" />
                          )}
                        </Button>
                      )}
                    </div>
                </ItemActionsMenu>

                {!parentMaterialId && (
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8 shrink-0 relative"
                    title={t("attachments")}
                    onClick={() =>
                      openSidebar("details", {
                        type: "material",
                        id: materialId,
                        data: { ...displayMaterial, __viewerType: viewerType, __path: pathname },
                      })
                    }
                  >
                    <Paperclip className="h-4 w-4" />
                    {Number(material.attachment_count ?? 0) > 0 && (
                      <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-violet-600 px-0.5 text-[9px] font-bold text-white">
                        {Number(material.attachment_count) > 99 ? "99+" : Number(material.attachment_count)}
                      </span>
                    )}
                  </Button>
                )}

                <Button
                  variant={sidebarOpen ? "secondary" : "outline"}
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  title={sidebarOpen ? t("closeInspector") : t("openInspector")}
                  onClick={() => {
                    if (sidebarOpen) {
                      closeSidebar();
                    } else {
                      openSidebar("details", {
                        type: "material",
                        id: materialId,
                        data: { ...displayMaterial, __viewerType: viewerType, __path: pathname },
                      });
                    }
                  }}
                >
                  <PanelRight className="h-4 w-4" />
                </Button>
              </div>
            )}
          </div>
          </div>{/* end header overlay */}

          {/*
           * Viewer wrapper — paddingTop is dynamically adjusted based on header visibility.
           * When the header is hidden, it transitions to standard padding (pt-2 / sm:pt-4 / md:pt-6)
           * to match the sides, ensuring the viewer expands to use the empty top space.
           */}
          <div
            className="flex-1 min-h-0 overflow-hidden px-2 pt-2 pb-2 max-sm:pb-20 sm:px-4 sm:pt-4 sm:pb-4 md:px-6 md:pt-6 md:pb-6 transition-all duration-300 ease-in-out"
            style={{ paddingTop: navbarVisible ? headerHeight : undefined }}
          >
          <div
            ref={viewerContainerRef}
            className="relative h-full overflow-hidden rounded-lg border"
          >
            {viewerType === "pdf" && (
              <PdfViewer
                fileKey={fileKey}
                materialId={materialId}
                annotations={threads}
              />
            )}
            {viewerType === "markdown" && (
              <MarkdownViewer
                fileKey={fileKey}
                materialId={materialId}
                material={material}
                annotations={threads}
              />
            )}
            {viewerType === "image" && (
              <ImageViewer
                fileKey={fileKey}
                materialId={materialId}
                fileName={fileName}
              />
            )}
            {viewerType === "svg" && (
              <SvgViewer
                fileKey={fileKey}
                materialId={materialId}
                fileName={fileName}
              />
            )}
            {viewerType === "video" && (
              <VideoPlayer
                fileKey={fileKey}
                materialId={materialId}
                material={material}
              />
            )}
            {viewerType === "audio" && (
              <AudioPlayer fileKey={fileKey} materialId={materialId} />
            )}
            {viewerType === "code" && (
              <CodeViewer
                fileKey={fileKey}
                materialId={materialId}
                fileName={fileName}
              />
            )}
            {viewerType === "csv" && (
              <CsvViewer
                fileKey={fileKey}
                materialId={materialId}
                fileName={fileName}
              />
            )}
            {viewerType === "office" && (
              <OfficeViewer
                fileKey={fileKey}
                materialId={materialId}
                fileName={fileName}
              />
            )}
            {viewerType === "epub" && (
              <EpubViewer fileKey={fileKey} materialId={materialId} />
            )}
            {viewerType === "djvu" && (
              <DjvuViewer fileKey={fileKey} materialId={materialId} />
            )}
            {viewerType === "qcm" && (
              <QCMViewer fileKey={fileKey} materialId={materialId} initialData={stagedQcmDraft as QCMFile ?? undefined} />
            )}
            {viewerType === "generic" && (
              <GenericViewer
                fileName={fileName}
                materialId={materialId}
                fileKey={fileKey}
              />
            )}
            <AnnotationSelectionTooltip
              containerRef={viewerContainerRef}
              onSubmit={handleAnnotationSubmit}
              disabled={isRestricted || guest}
            />
          </div>
          </div>{/* end viewer wrapper */}
        </div>

        <SharedSidebar />
        {isMobile && (
          <ViewerFab
            material={material}
            materialId={materialId}
            materialTitle={title}
            directoryId={directoryId}
            viewerType={viewerType}
            mimeType={mimeType}
            fileName={fileName}
            open={materialActionsOpen}
            onOpenChange={setMaterialActionsOpen}
          />
        )}
      </div>
    </AnnotationsContext.Provider>
  );
}
