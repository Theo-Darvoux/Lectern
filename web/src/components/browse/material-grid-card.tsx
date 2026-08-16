"use client";


import { ContentStatusBadge, normalizeContentStatus } from "@/components/content-status-badge";
import { memo, useState, useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";
import { BrowseLink } from "@/components/browse/browse-link";
import { Paperclip, File } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { EXT_BADGE_COLORS, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { useTranslations } from "next-intl";
import { TYPE_COLORS, TYPE_ICONS, EXT_ICONS } from "@/lib/material-icons";
import { getFileTypeStyle } from "@/components/home/file-type-display";
import { apiFetch } from "@/lib/api-client";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import type { MaterialDetail } from "@/components/home/types";
import { useInView } from "@/hooks/use-in-view";

const MaterialPreview = dynamic(
  () => import("@/components/home/material-preview").then((module) => module.MaterialPreview),
  { ssr: false },
);

// ---------------------------------------------------------------------------
// Ghost preview: for staged "created" materials that have a file_key
// ---------------------------------------------------------------------------

function GhostMaterialPreview({
  fileKey,
  fileName,
  mimeType,
  containerRef,
}: {
  fileKey: string;
  fileName?: string | null;
  mimeType?: string | null;
  containerRef: React.RefObject<Element | null>;
}) {
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const { gradient, iconColorClass, Icon } = getFileTypeStyle(fileName ?? null, mimeType ?? null);
  const inView = useInView(containerRef);

  const isImage =
    (mimeType?.startsWith("image/") ?? false) ||
    /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(fileName ?? "");

  useEffect(() => {
    if (!inView || !fileKey || !isImage) return;
    let cancelled = false;
    // Defer the fetch to idle time so it doesn't compete with the initial
    // grid paint. The card already shows a styled icon placeholder.
    const schedule =
      typeof window !== "undefined" && "requestIdleCallback" in window
        ? (cb: () => void) => (window as unknown as { requestIdleCallback: (cb: () => void) => number }).requestIdleCallback(cb)
        : (cb: () => void) => window.setTimeout(cb, 200);
    schedule(() => {
      if (cancelled) return;
      apiFetch<{ url: string }>(`/upload/preview?file_key=${encodeURIComponent(fileKey)}`)
        .then((res) => {
          if (!cancelled && res && res.url) setImgUrl(res.url);
        })
        .catch(() => {});
    });
    return () => { cancelled = true; };
  }, [inView, fileKey, isImage]);

  return (
    <div
      className={cn(
        "relative w-full h-full flex items-center justify-center overflow-hidden bg-linear-to-br",
        gradient,
      )}
    >
      <Icon
        className={cn(
          "h-10 w-10 z-10",
          iconColorClass,
          imgUrl ? "opacity-0" : "opacity-80",
        )}
      />
      {imgUrl && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={imgUrl}
          alt=""
          className="absolute inset-0 h-full w-full object-cover animate-in fade-in duration-300"
          loading="lazy"
          decoding="async"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MaterialGridCardProps {
  material: Record<string, unknown>;
  staged?: "edited" | "deleted" | "moved" | "created" | null;
  isExternal?: boolean;
  selectMode?: boolean;
  selected?: boolean;
  onToggleSelect?: (index: number, e?: React.MouseEvent) => void;
  previewPrId?: string;
  navIndex?: number;
  focused?: boolean;
  previewOpIndex?: number;
  onNavigate?: () => void;
  onAddAttachment?: (id: string, title: string) => void;
  draftAttachmentCount?: number;
  /** For ghost "created" materials: the staged file key */
  ghostFileKey?: string | null;
  /** For ghost "created" materials: the staged file MIME type */
  ghostFileMimeType?: string | null;
  /** Current pathname base (without trailing slash), hoisted from parent to avoid per-item usePathname subscription */
  pathBase: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function MaterialGridCardImpl({
  material,
  staged,
  isExternal,
  selectMode,
  selected,
  onToggleSelect,
  previewPrId,
  navIndex,
  focused,
  previewOpIndex,
  onNavigate,
  onAddAttachment,
  draftAttachmentCount,
  ghostFileKey,
  ghostFileMimeType,
  pathBase,
}: MaterialGridCardProps) {
  const t = useTranslations("Browse");
  const tTypes = useTranslations("MaterialTypes");
  const cardRef = useRef<HTMLAnchorElement>(null);

  const title = String(material.title ?? "");
  const slug = String(material.slug ?? "");
  const id = String(material.id ?? "");
  const type = String(material.type ?? "other");
  const status = normalizeContentStatus(material.status);
  const attachmentCount = draftAttachmentCount ?? Number(material.attachment_count ?? 0);

  let fileName = "";
  let mimeType = "";
  if (material.current_version_info && typeof material.current_version_info === "object") {
    const vi = material.current_version_info as Record<string, unknown>;
    fileName = vi.file_name ? String(vi.file_name) : "";
    mimeType = vi.file_mime_type ? String(vi.file_mime_type) : "";
  }

  const buildPath = () => {
    if (staged === "edited" && previewPrId && previewOpIndex !== undefined) {
      return `/pull-requests/${previewPrId}/preview/${previewOpIndex}`;
    }
    const matPath = `${pathBase}/${slug}`;
    return previewPrId ? `${matPath}?preview_pr=${previewPrId}` : matPath;
  };

  // Badge
  let badgeColor = TYPE_COLORS[type] ?? TYPE_COLORS.other;
  let badgeLabel = tTypes.has(type as Parameters<typeof tTypes>[0]) ? tTypes(type as Parameters<typeof tTypes>[0]) : type;

  if (type === "document") {
    const fallbackLabel = getFileBadgeLabel(fileName, mimeType);
    if (fallbackLabel && fallbackLabel !== "FILE") badgeLabel = fallbackLabel;
    const ext = getFileExtension(fileName);
    let newColor = badgeColor;
    if (ext && EXT_BADGE_COLORS[ext]) newColor = EXT_BADGE_COLORS[ext];
    else if (mimeType) {
      if (mimeType === "application/pdf") newColor = EXT_BADGE_COLORS["pdf"];
      else if (mimeType.startsWith("image/")) newColor = EXT_BADGE_COLORS["jpg"];
      else if (mimeType.startsWith("video/")) newColor = EXT_BADGE_COLORS["mp4"];
      else if (mimeType.startsWith("audio/")) newColor = EXT_BADGE_COLORS["mp3"];
      else if (mimeType.includes("document") || mimeType.includes("msword")) newColor = EXT_BADGE_COLORS["doc"];
      else if (mimeType.includes("sheet") || mimeType.includes("excel")) newColor = EXT_BADGE_COLORS["xls"];
    }
    if (newColor && newColor !== badgeColor) badgeColor = newColor;
  }

  // Staged styling
  const themeColor =
    staged === "deleted"
      ? "red"
      : staged === "moved"
        ? "amber"
        : isExternal
          ? "blue"
          : "green";

  const borderStyle = isExternal ? "border-solid" : "border-dashed";

  const stagedRing = staged
    ? `ring-2 ${borderStyle === "border-solid" ? "" : "[border-style:dashed]"} ring-${themeColor}-400`
    : "";

  const textColor =
    staged === "deleted"
      ? "line-through text-red-700 dark:text-red-400"
      : staged === "moved"
        ? "text-amber-700 dark:text-amber-400"
        : staged === "created" || staged === "edited"
          ? `text-${themeColor}-700 dark:text-${themeColor}-400`
          : "";

  const prefetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handlePointerEnter = () => {
    if (!slug || staged === "deleted" || onNavigate) return;
    if (staged === "edited" && previewPrId && previewOpIndex !== undefined) return;
    prefetchTimer.current = setTimeout(() => {
      const browsePath = `${pathBase}/${slug}`.replace(/^\/browse\/?/, "").replace(/\/$/, "");
      prefetchBrowsePath(browsePath);
    }, 100);
  };
  const handlePointerLeave = () => {
    if (prefetchTimer.current) {
      clearTimeout(prefetchTimer.current);
      prefetchTimer.current = null;
    }
  };

  const router = useRouter();
  const openLink = useExternalLinkStore((s) => s.openLink);
  const targetUrl = String((material.metadata as Record<string, unknown> | undefined)?.url ?? "").trim();
  const isLink = type === "link" || !!targetUrl;

  const handleCardClick = (e: React.MouseEvent) => {
    if (staged === "deleted") {
      e.preventDefault();
      return;
    }
    if (selectMode && onToggleSelect) {
      e.preventDefault();
      onToggleSelect(navIndex ?? 0, e);
      return;
    }
    if (isLink && targetUrl && !onNavigate) {
      e.preventDefault();
      openLink(targetUrl, (path) => router.push(path));
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      return; // let browser open in new tab natively via <a href>
    }
    if (onNavigate) {
      e.preventDefault();
      onNavigate();
    }
    // else: let Next.js Link handle client-side navigation
  };

  // Whether to use the ghost preview (staged creation with a file_key)
  const useGhostPreview = staged === "created" && !!ghostFileKey;

  return (
    <ItemActionsMenu
      item={{ id, type: "material", data: material, staged, isExternal }}
      onAddAttachment={onAddAttachment ? () => onAddAttachment(id, title) : undefined}
      itemPath={buildPath()}
    >
      <BrowseLink
        ref={cardRef}
        href={buildPath()}
        onClick={handleCardClick}
        onPointerEnter={handlePointerEnter}
        onPointerLeave={handlePointerLeave}
        data-nav-index={navIndex}
        style={{ contentVisibility: "auto", containIntrinsicSize: "0 280px" }}
        className={cn(
          "group relative rounded-xl border bg-card shadow-sm overflow-hidden cursor-pointer",
          "ring-1 ring-border/50 hover:ring-primary/30",
          stagedRing,
          status === "important" && !staged ? "ring-2 ring-red-400/60" : "",
          status === "archived" ? "opacity-75" : "",
          selectMode && selected ? "bg-primary/5 dark:bg-primary/10 ring-primary" : "",
          focused ? "ring-2 ring-primary/40" : "",
        )}
      >
        {/* Preview area */}
        <div className="aspect-[4/3] relative overflow-hidden shrink-0">
          {useGhostPreview ? (
            <GhostMaterialPreview
              fileKey={ghostFileKey!}
              fileName={fileName || undefined}
              mimeType={ghostFileMimeType || mimeType || undefined}
              containerRef={cardRef}
            />
          ) : (
            <MaterialPreview
              material={material as unknown as MaterialDetail}
              className=""
              lazy
            />
          )}

          {/* Staged badge */}
          {staged && (
            <span
              className={cn(
                "absolute top-2 left-2 z-20 inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold",
                staged === "deleted"
                  ? "text-red-600 border-red-300 bg-red-50/80"
                  : staged === "moved"
                    ? "text-amber-600 border-amber-300 bg-amber-50/80"
                    : isExternal
                      ? "text-blue-600 border-blue-300 bg-blue-50/80"
                      : "text-green-600 border-green-300 bg-green-50/80",
              )}
            >
              {staged === "deleted"
                ? t("deleting")
                : staged === "moved"
                  ? t("moving")
                  : staged === "created"
                    ? isExternal
                      ? t("contribution")
                      : t("draft")
                    : t("edited")}
            </span>
          )}

          {/* Select mode checkbox */}
          {selectMode && (
            <div
              className="absolute top-2 right-2 z-20"
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); onToggleSelect?.(navIndex ?? 0, e); }}
            >
              <Checkbox checked={!!selected} onCheckedChange={() => {}} className="h-5 w-5 bg-background/95" />
            </div>
          )}

          {!selectMode && (
            <div
              onClick={(e) => e.stopPropagation()}
              className="absolute bottom-2 right-2 z-10"
            >
              <ItemActionsDropdownTrigger
                className="h-8 w-8 rounded-full bg-background/90 ring-1 ring-border/60 shadow-sm hover:bg-background"
                iconClassName="h-4 w-4 text-foreground"
              />
            </div>
          )}
        </div>

        {/* Card footer */}
        <div className="p-2.5 flex flex-col gap-1 min-w-0">
          <p className={cn("text-sm font-medium leading-snug line-clamp-2", textColor || "text-foreground")}>
            {title}
          </p>
          <div className="flex items-center gap-1.5 flex-wrap">
            <ContentStatusBadge materialId={id} status={status} />
            <span className={cn("inline-block rounded px-1.5 py-0.5 text-[10px] font-medium", badgeColor)}>
              {badgeLabel}
            </span>
            {attachmentCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
                <Paperclip className="h-2.5 w-2.5" />
                {attachmentCount}
              </span>
            )}
          </div>
        </div>
      </BrowseLink>
    </ItemActionsMenu>
  );
}

export const MaterialGridCard = memo(MaterialGridCardImpl);
