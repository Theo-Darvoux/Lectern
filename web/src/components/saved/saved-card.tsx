"use client";

import { memo, useMemo, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import {
  Calendar,
  Check,
  Copy,
  ExternalLink,
  Eye,
  FileText,
  Loader2,
  MoreVertical,
  StarOff,
  ThumbsUp,
  X,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { enUS, fr } from "date-fns/locale";
import { useLocale } from "next-intl";
import { toast } from "sonner";

import { CollectionPicker } from "@/components/saved/collection-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ContentStatusBadge, normalizeContentStatus } from "@/components/content-status-badge";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { getDirectoryIcon } from "@/lib/directory-icons";
import { getDirectoryColor } from "@/lib/directory-colors";
import { EXT_BADGE_COLORS, getFileBadgeLabel } from "@/lib/file-utils";
import { useSavedTranslations } from "@/lib/saved-i18n";
import { apiFetch } from "@/lib/api-client";
import { removeCollectionItem, type SavedItem } from "@/lib/collections";
import type { MaterialDetail } from "@/components/home/types";
import { cn } from "@/lib/utils";

const MaterialPreview = dynamic(
  () => import("@/components/home/material-preview").then((module) => module.MaterialPreview),
  { ssr: false },
);

const DirectoryPreviewCollage = dynamic(
  () => import("@/components/browse/directory-preview-collage").then((module) => module.DirectoryPreviewCollage),
  { ssr: false },
);

interface SavedCardProps {
  item: SavedItem;
  collectionId: string | null;
  selected: boolean;
  selectMode: boolean;
  onToggleSelect: (item: SavedItem) => void;
  onRemoved: () => void;
  onCollectionsChanged: () => void;
}

export const SavedCard = memo(function SavedCard({
  item,
  collectionId,
  selected,
  selectMode,
  onToggleSelect,
  onRemoved,
  onCollectionsChanged,
}: SavedCardProps) {
  const t = useSavedTranslations();
  const locale = useLocale();
  const router = useRouter();
  const openExternalLink = useExternalLinkStore((s) => s.openLink);
  const [removing, setRemoving] = useState(false);

  const isDirectory = item.target_type === "directory";
  const metadata = (item.metadata ?? {}) as Record<string, unknown>;
  const rawStatus = typeof item.status === "string" ? normalizeContentStatus(item.status) : null;
  const targetUrl = String(metadata?.url ?? "").trim();
  const isExternalLink = item.item_type === "link" || !!targetUrl;

  // Directory visual styling
  const dirIconId = metadata.thumbnail_icon ? String(metadata.thumbnail_icon) : null;
  const dirColorId = metadata.thumbnail_color ? String(metadata.thumbnail_color) : null;
  const { Icon: DirThumbnailIcon } = getDirectoryIcon(dirIconId);
  const dirColor = getDirectoryColor(dirColorId);
  const previewMaterialIds = Array.isArray(metadata?.preview_material_ids)
    ? (metadata.preview_material_ids as string[])
    : [];
  const showCollage = !dirIconId && previewMaterialIds.length > 0;

  // Material data representation for MaterialPreview
  const materialPreviewData = useMemo((): MaterialDetail | null => {
    if (isDirectory) return null;
    const versionInfo = (item.current_version_info as Record<string, unknown> | undefined) ?? null;
    return {
      id: item.target_id,
      directory_id: null,
      directory_path: null,
      title: item.title,
      slug: item.slug || "",
      description: item.description,
      type: item.item_type,
      current_version: Number(versionInfo?.version_number ?? 1),
      parent_material_id: null,
      author_id: null,
      metadata: metadata,
      download_count: item.download_count ?? 0,
      total_views: item.total_views ?? 0,
      views_today: 0,
      like_count: item.like_count ?? 0,
      is_liked: false,
      is_favourited: true,
      attachment_count: 0,
      tags: [],
      created_at: item.added_at,
      updated_at: item.added_at,
      current_version_info: versionInfo
        ? {
            id: String(versionInfo.id ?? ""),
            material_id: item.target_id,
            version_number: Number(versionInfo.version_number ?? 1),
            file_key: (versionInfo.file_key as string) ?? null,
            file_name: (versionInfo.file_name as string) ?? item.title,
            file_size: (versionInfo.file_size as number) ?? null,
            file_mime_type: (versionInfo.file_mime_type as string) ?? null,
            diff_summary: null,
            author_id: null,
            pr_id: null,
            virus_scan_result: "clean",
            created_at: item.added_at,
          }
        : {
            id: "",
            material_id: item.target_id,
            version_number: 1,
            file_key: null,
            file_name: typeof metadata?.file_name === "string" ? metadata.file_name : item.title,
            file_size: null,
            file_mime_type: typeof metadata?.mime_type === "string" ? metadata.mime_type : null,
            diff_summary: null,
            author_id: null,
            pr_id: null,
            virus_scan_result: "clean",
            created_at: item.added_at,
          },
    };
  }, [isDirectory, item, metadata]);

  const badgeLabel = isDirectory
    ? item.item_type === "module"
      ? "Module"
      : "Folder"
    : getFileBadgeLabel(
        (materialPreviewData?.current_version_info?.file_name as string) || item.title,
        typeof metadata?.mime_type === "string" ? metadata.mime_type : undefined,
      ) || item.item_type;

  // Time added
  const addedDate = new Date(item.added_at);
  const relativeTime = isNaN(addedDate.getTime())
    ? ""
    : formatDistanceToNow(addedDate, {
        addSuffix: true,
        locale: locale.startsWith("fr") ? fr : enUS,
      });

  const handleCardClick = (e: React.MouseEvent) => {
    if (selectMode) {
      e.preventDefault();
      onToggleSelect(item);
      return;
    }

    if (isExternalLink && targetUrl) {
      e.preventDefault();
      openExternalLink(targetUrl, (path) => router.push(path));
    }
  };

  const copyUrl = async () => {
    try {
      const fullUrl = `${window.location.origin}${item.href}`;
      await navigator.clipboard.writeText(fullUrl);
      toast.success(t("linkCopied"));
    } catch {
      toast.error("Failed to copy link");
    }
  };

  const remove = async () => {
    if (removing) return;
    setRemoving(true);
    try {
      if (collectionId) {
        await removeCollectionItem(collectionId, item.target_type, item.target_id);
      } else {
        const endpoint =
          item.target_type === "material"
            ? `/materials/${item.target_id}/favourite`
            : `/directories/${item.target_id}/favourite`;
        await apiFetch(endpoint, { method: "POST" });
      }
      onRemoved();
    } catch {
      toast.error(collectionId ? t("errors.removeFromCollection") : t("errors.removeSaved"));
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div
      className={cn(
        "group relative flex flex-col justify-between overflow-hidden rounded-2xl border bg-card text-card-foreground shadow-xs transition-all duration-200 hover:shadow-md hover:border-primary/40",
        selected && "border-primary/70 ring-2 ring-primary/20 bg-primary/5",
      )}
    >
      {/* ── Selection Checkbox Overlay ────────────────────────────────────── */}
      <div
        className={cn(
          "absolute left-3 top-3 z-20 transition-opacity duration-200",
          selectMode || selected
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
        )}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggleSelect(item);
          }}
          className={cn(
            "flex h-6 w-6 items-center justify-center rounded-md border bg-background/90 backdrop-blur-xs shadow-xs transition-colors hover:bg-muted cursor-pointer",
            selected && "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
          )}
          aria-label={t("selectItem")}
        >
          {selected ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <span className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      {/* ── Quick Actions Corner ─────────────────────────────────────────── */}
      <div className="absolute right-2 top-2 z-20 flex items-center gap-1 opacity-0 transition-opacity duration-200 group-hover:opacity-100 focus-within:opacity-100">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="secondary"
              size="icon"
              className="h-7 w-7 rounded-lg bg-background/85 backdrop-blur-xs shadow-xs hover:bg-background"
            >
              <MoreVertical className="h-3.5 w-3.5" />
              <span className="sr-only">{t("actions")}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem asChild>
              <Link href={item.href} target="_blank" rel="noreferrer">
                <ExternalLink className="mr-2 h-4 w-4" />
                {t("openInNewTab")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={copyUrl}>
              <Copy className="mr-2 h-4 w-4" />
              {t("copyLink")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => void remove()}
              disabled={removing}
              className="text-destructive focus:text-destructive"
            >
              {collectionId ? (
                <>
                  <X className="mr-2 h-4 w-4" />
                  {t("removeFromCollection")}
                </>
              ) : (
                <>
                  <StarOff className="mr-2 h-4 w-4" />
                  {t("removeSaved")}
                </>
              )}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* ── Top Visual Thumbnail / Preview Header Banner ─────────────────── */}
      <Link
        href={item.href}
        onClick={handleCardClick}
        className="block relative aspect-[16/9] w-full overflow-hidden border-b bg-muted/40 cursor-pointer focus-visible:outline-hidden"
      >
        {isDirectory ? (
          /* Directory Banner representation */
          <div
            className={cn(
              "relative flex h-full w-full items-center justify-center bg-linear-to-br transition-transform duration-300 group-hover:scale-105",
              dirColor.gradient,
            )}
          >
            {showCollage ? (
              <DirectoryPreviewCollage materialIds={previewMaterialIds} />
            ) : (
              <div className="relative flex flex-col items-center justify-center gap-1.5">
                <DirThumbnailIcon className={cn("h-12 w-12 drop-shadow-xs", dirColor.iconClass)} />
              </div>
            )}
            <div className="absolute bottom-2 left-2 flex items-center gap-1.5 z-10">
              <Badge
                variant="secondary"
                className="bg-background/85 text-[10px] font-semibold uppercase tracking-wider backdrop-blur-xs shadow-xs"
              >
                {badgeLabel}
              </Badge>
              {rawStatus && (
                <ContentStatusBadge status={rawStatus} className="text-[10px] py-0" />
              )}
            </div>
          </div>
        ) : (
          /* Material Live Thumbnail / Preview representation */
          <div className="relative h-full w-full overflow-hidden">
            {materialPreviewData ? (
              <MaterialPreview material={materialPreviewData} lazy />
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-muted">
                <FileText className="h-10 w-10 text-muted-foreground/60" />
              </div>
            )}
            <div className="absolute bottom-2 left-2 flex items-center gap-1.5 z-10 pointer-events-none">
              <Badge
                variant="outline"
                className={cn(
                  "bg-background/90 text-[10px] font-bold uppercase tracking-wider backdrop-blur-xs shadow-xs",
                  EXT_BADGE_COLORS[badgeLabel.toLowerCase()] || "text-foreground",
                )}
              >
                {badgeLabel}
              </Badge>
              {rawStatus && (
                <ContentStatusBadge status={rawStatus} className="text-[10px] py-0" />
              )}
            </div>
          </div>
        )}
      </Link>

      {/* ── Card Content Body ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col p-4">
        <Link
          href={item.href}
          onClick={handleCardClick}
          className="min-w-0 group-hover:text-primary transition-colors focus-visible:outline-hidden"
        >
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight">
            {item.title}
          </h3>
        </Link>

        {item.description ? (
          <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground leading-relaxed">
            {item.description}
          </p>
        ) : (
          <p className="mt-1.5 line-clamp-1 text-xs text-muted-foreground/60 italic">
            {isDirectory ? t("filterDirectories") : t("filterMaterials")}
          </p>
        )}

        {/* ── Metadata & Stats ────────────────────────────────────────── */}
        <div className="mt-auto pt-4 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          {relativeTime ? (
            <span className="flex items-center gap-1 truncate" title={item.added_at}>
              <Calendar className="h-3 w-3 shrink-0 opacity-70" />
              <span className="truncate">{relativeTime}</span>
            </span>
          ) : (
            <span />
          )}

          {!isDirectory && ((item.total_views ?? 0) > 0 || (item.like_count ?? 0) > 0) && (
            <div className="flex items-center gap-2.5 shrink-0 tabular-nums">
              {(item.total_views ?? 0) > 0 && (
                <span className="flex items-center gap-1">
                  <Eye className="h-3 w-3 opacity-70" />
                  {item.total_views?.toLocaleString()}
                </span>
              )}
              {(item.like_count ?? 0) > 0 && (
                <span className="flex items-center gap-1">
                  <ThumbsUp className="h-3 w-3 opacity-70" />
                  {item.like_count?.toLocaleString()}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Card Footer Action Toolbar ───────────────────────────────────── */}
      <div className="flex items-center justify-between border-t bg-muted/10 px-3 py-2">
        <div className="flex-1 min-w-0 pr-2">
          <CollectionPicker
            targetType={item.target_type}
            targetId={item.target_id}
            onChanged={onCollectionsChanged}
            className="h-7 text-xs px-2.5 font-medium"
          />
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
          disabled={removing}
          onClick={() => void remove()}
          title={collectionId ? t("removeFromCollection") : t("removeSaved")}
          aria-label={collectionId ? t("removeFromCollection") : t("removeSaved")}
        >
          {removing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : collectionId ? (
            <X className="h-3.5 w-3.5" />
          ) : (
            <StarOff className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
});
