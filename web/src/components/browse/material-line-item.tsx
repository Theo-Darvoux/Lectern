"use client";

import { memo, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";
import { BrowseLink } from "@/components/browse/browse-link";
import {
    Paperclip,
    ThumbsUp,
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useLikeOverrides } from "@/lib/stores";
import { EXT_BADGE_COLORS, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { TYPE_COLORS } from "@/lib/material-icons";
import { getFileTypeStyle } from "@/components/home/file-type-display";
import { isExternalUrl } from "@/lib/url-utils";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { ContentStatusBadge, normalizeContentStatus } from "@/components/content-status-badge";
import { apiFetch } from "@/lib/api-client";
import { useInView } from "@/hooks/use-in-view";
import { cn } from "@/lib/utils";
import type { MaterialDetail } from "@/components/home/types";

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
          "h-6 w-6 z-10",
          iconColorClass,
          imgUrl ? "opacity-0" : "opacity-80",
        )}
      />
      {imgUrl && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={imgUrl}
          alt={fileName ?? "Staged file preview"}
          className="absolute inset-0 h-full w-full object-cover animate-in fade-in duration-300"
          loading="lazy"
          decoding="async"
        />
      )}
    </div>
  );
}

interface MaterialLineItemProps {
    material: Record<string, unknown>;
    staged?: "edited" | "deleted" | "moved" | "created" | null;
    isExternal?: boolean;
    selectMode?: boolean;
    selected?: boolean;
    onToggleSelect?: (index: number, e?: React.MouseEvent) => void;
    /** When set, appended as ?preview_pr= to preserve preview mode across navigation */
    previewPrId?: string;
    navIndex?: number;
    focused?: boolean;
    /** The index of the operation in the PR payload, if this is an external preview edit */
    previewOpIndex?: number;
    /** Special override for clicking on ghost materials (creations) */
    onNavigate?: () => void;
    /** Request attachment upload for this material (draft only) */
    onAddAttachment?: (materialId: string, materialTitle: string) => void;
    /** Cached attachment count for drafts */
    draftAttachmentCount?: number;
    /** For ghost "created" materials: the staged file key */
    ghostFileKey?: string | null;
    /** For ghost "created" materials: the staged file MIME type */
    ghostFileMimeType?: string | null;
    /** Current pathname base (without trailing slash), hoisted from parent to avoid per-item usePathname subscription */
    pathBase: string;
    /** Hoisted from parent to avoid per-item useIsMobile subscription */
    isMobile?: boolean;
}

function MaterialLineItemImpl({
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
    isMobile,
}: MaterialLineItemProps) {
    const t = useTranslations("Browse");
    const tTypes = useTranslations("MaterialTypes");
    const itemRef = useRef<HTMLAnchorElement>(null);

    const title = String(material.title ?? "");
    const slug = String(material.slug ?? "");
    const id = String(material.id ?? "");
    const type = String(material.type ?? "other");
    const targetUrl = String((material.metadata as Record<string, unknown> | undefined)?.url ?? "").trim();
    const isLink = type === "link" || !!targetUrl;
    const isInternalLink = isLink && !isExternalUrl(targetUrl);
    const status = normalizeContentStatus(material.status);
    const attachmentCount = draftAttachmentCount ?? Number(material.attachment_count ?? 0);
    const likeOverride = useLikeOverrides((s) => s.materialOverrides[id]);
    const likeCount = likeOverride !== undefined ? likeOverride.likeCount : Number(material.like_count ?? 0);
    const isLiked = likeOverride !== undefined ? likeOverride.isLiked : Boolean(material.is_liked);

    // Extract file name from current version info if available
    let fileName = "";
    let mimeType = "";
    if (material.current_version_info && typeof material.current_version_info === "object") {
        const vi = material.current_version_info as Record<string, unknown>;
        fileName = vi.file_name ? String(vi.file_name) : "";
        mimeType = vi.file_mime_type ? String(vi.file_mime_type) : "";
    }

    const buildPath = () => {
        // If this is an external edit preview, link directly to the PR preview page
        if (staged === "edited" && previewPrId && previewOpIndex !== undefined) {
            return `/pull-requests/${previewPrId}/preview/${previewOpIndex}`;
        }
        const matPath = `${pathBase}/${slug}`;
        return previewPrId ? `${matPath}?preview_pr=${previewPrId}` : matPath;
    };

    const effectiveType = isInternalLink ? "internal_link" : type;
    let badgeColor = TYPE_COLORS[effectiveType] ?? TYPE_COLORS[type] ?? TYPE_COLORS.other;

    let badgeLabel = tTypes.has(effectiveType as any)
        ? tTypes(effectiveType as any)
        : tTypes.has(type as any)
            ? tTypes(type as any)
            : type;

    if (type === "document") {
        const fallbackLabel = getFileBadgeLabel(fileName, mimeType);
        if (fallbackLabel && fallbackLabel !== "FILE") {
            badgeLabel = fallbackLabel;
        }

        const ext = getFileExtension(fileName);
        let newColor = badgeColor;
        if (ext && EXT_BADGE_COLORS[ext]) {
            newColor = EXT_BADGE_COLORS[ext];
        } else if (mimeType) {
            if (mimeType === "application/pdf") newColor = EXT_BADGE_COLORS["pdf"];
            else if (mimeType.startsWith("image/")) newColor = EXT_BADGE_COLORS["jpg"];
            else if (mimeType.startsWith("video/")) newColor = EXT_BADGE_COLORS["mp4"];
            else if (mimeType.startsWith("audio/")) newColor = EXT_BADGE_COLORS["mp3"];
            else if (mimeType.includes("document") || mimeType.includes("msword")) newColor = EXT_BADGE_COLORS["doc"];
            else if (mimeType.includes("sheet") || mimeType.includes("excel")) newColor = EXT_BADGE_COLORS["xls"];
        }
        if (newColor && newColor !== badgeColor) {
            badgeColor = newColor;
        }
    }

    const prefetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const handlePointerEnter = () => {
        if (!slug || staged === "deleted" || onNavigate) return;
        // Don't prefetch PR preview pages — they aren't browse routes
        if (staged === "edited" && previewPrId && previewOpIndex !== undefined) return;
        prefetchTimer.current = setTimeout(() => {
            // Prefetch the browse API response.
            const builtPath = buildPath();
            const browsePath = builtPath.replace(/^\/browse\/?/, "").split("?")[0].replace(/\/$/, "");
            prefetchBrowsePath(browsePath);
        }, 100);
    };
    const handlePointerLeave = () => {
        if (prefetchTimer.current) clearTimeout(prefetchTimer.current);
    };

    const router = useRouter();
    const openLink = useExternalLinkStore((s) => s.openLink);

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

    const themeColor =
        staged === "deleted"
            ? "red"
            : staged === "moved"
                ? "amber"
                : isExternal
                    ? "blue"
                    : "green";

    const borderStyle = isExternal ? "border-solid" : "border-dashed";

    const stagedBorder = staged
        ? `border-l-2 ${borderStyle} border-l-${themeColor}-400 bg-${themeColor}-50/50 dark:bg-${themeColor}-950/20`
        : "";

    const textColor =
        staged === "deleted"
            ? "line-through text-red-700 dark:text-red-400"
            : staged === "moved"
                ? "text-amber-700 dark:text-amber-400"
                : (staged === "created" || staged === "edited")
                    ? `text-${themeColor}-700 dark:text-${themeColor}-400`
                    : "";

    const useGhostPreview = staged === "created" && !!ghostFileKey;

    return (
        <ItemActionsMenu
            item={{ id, type: "material", data: material, staged, isExternal }}
            onAddAttachment={onAddAttachment ? () => onAddAttachment(id, title) : undefined}
            itemPath={buildPath()}
        >
            <BrowseLink
                ref={itemRef}
                href={buildPath()}
                onClick={handleCardClick}
                onPointerEnter={handlePointerEnter}
                onPointerLeave={handlePointerLeave}
                data-nav-index={navIndex}
                style={{ contentVisibility: "auto", containIntrinsicSize: "0 72px" }}
                className={`flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50 cursor-pointer ${stagedBorder} ${selectMode && selected ? "bg-primary/5 dark:bg-primary/10" : ""} ${focused ? "bg-muted ring-2 ring-inset ring-primary/40" : ""}`}
            >
                {selectMode && (
                    <Checkbox
                        checked={!!selected}
                        onCheckedChange={() => {}}
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onToggleSelect?.(navIndex ?? 0, e);
                        }}
                        className="shrink-0"
                    />
                )}

                {/* ── Visual Thumbnail / Preview ── */}
                <div className="relative flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border bg-muted/50 transition-transform group-hover:scale-105">
                    {useGhostPreview ? (
                        <GhostMaterialPreview
                            fileKey={ghostFileKey!}
                            fileName={fileName || undefined}
                            mimeType={ghostFileMimeType || mimeType || undefined}
                            containerRef={itemRef}
                        />
                    ) : (
                        <MaterialPreview
                            material={material as unknown as MaterialDetail}
                            lazy
                            className="h-full w-full"
                        />
                    )}
                </div>

                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <span className={`block truncate font-medium ${textColor}`}>
                            {title}
                        </span>
                        {staged && (
                            <span
                                className={`inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                                    staged === "deleted"
                                        ? "text-red-600 border-red-300"
                                        : staged === "moved"
                                            ? "text-amber-600 border-amber-300"
                                            : isExternal
                                                ? "text-blue-600 border-blue-300"
                                                : "text-green-600 border-green-300"
                                }`}
                            >
                                {staged === "deleted"
                                    ? t("deleting") || "Deleting"
                                    : staged === "moved"
                                        ? t("moving") || "Moving"
                                        : staged === "created"
                                            ? isExternal
                                                ? t("contribution") || "Contribution"
                                                : t("draft") || "Draft"
                                            : t("edited") || "Edited"}
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                        <ContentStatusBadge materialId={id} status={status} />
                        <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${badgeColor}`}>
                            {badgeLabel}
                        </span>
                        {attachmentCount > 0 && (
                            <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
                                <Paperclip className="h-3 w-3" />
                                {attachmentCount}
                            </span>
                        )}
                    </div>
                </div>

                {!isMobile && likeCount > 0 && (
                    <div className="flex flex-col items-end justify-center px-2 text-[11px] leading-tight text-muted-foreground opacity-80">
                        <span className="flex items-center gap-1" title={t("likes")}>
                            {likeCount}
                            <ThumbsUp className={`h-3 w-3 ${isLiked ? "fill-primary text-primary" : ""}`} />
                        </span>
                    </div>
                )}
                    <div className="flex shrink-0 items-center gap-1">
                    <ItemActionsDropdownTrigger />

                </div>
            </BrowseLink>
        </ItemActionsMenu>
    );
}

export const MaterialLineItem = memo(MaterialLineItemImpl);
