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
import { isExternalUrl } from "@/lib/url-utils";
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

interface SavedListRowProps {
  item: SavedItem;
  collectionId: string | null;
  selected: boolean;
  selectMode: boolean;
  onToggleSelect: (item: SavedItem) => void;
  onRemoved: () => void;
  onCollectionsChanged: () => void;
}

export const SavedListRow = memo(function SavedListRow({
  item,
  collectionId,
  selected,
  selectMode,
  onToggleSelect,
  onRemoved,
  onCollectionsChanged,
}: SavedListRowProps) {
  const t = useSavedTranslations();
  const locale = useLocale();
  const router = useRouter();
  const openExternalLink = useExternalLinkStore((s) => s.openLink);
  const [removing, setRemoving] = useState(false);

  const isDirectory = item.target_type === "directory";
  const metadata = (item.metadata ?? {}) as Record<string, unknown>;
  const rawStatus = typeof item.status === "string" ? normalizeContentStatus(item.status) : null;
  const targetUrl = String(metadata?.url ?? "").trim();
  const isLink = item.item_type === "link" || !!targetUrl;
  const isInternalLink = isLink && !isExternalUrl(targetUrl);

  // Directory styling
  const dirIconId = metadata.thumbnail_icon ? String(metadata.thumbnail_icon) : null;
  const dirColorId = metadata.thumbnail_color ? String(metadata.thumbnail_color) : null;
  const { Icon: DirThumbnailIcon } = getDirectoryIcon(dirIconId);
  const dirColor = getDirectoryColor(dirColorId);

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
    : isLink
      ? isInternalLink
        ? "Internal Link"
        : "External Link"
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

  const handleRowLinkClick = (e: React.MouseEvent) => {
    if (selectMode) {
      e.preventDefault();
      onToggleSelect(item);
      return;
    }

    if (isLink && targetUrl) {
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
        "group relative flex items-center gap-3 rounded-xl border bg-card p-3 transition-all duration-150 hover:bg-muted/30 hover:border-primary/30",
        selected && "border-primary/70 ring-2 ring-primary/20 bg-primary/5",
      )}
    >
      {/* ── Selection Checkbox ────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center">
        <button
          type="button"
          onClick={() => onToggleSelect(item)}
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-md border transition-colors hover:bg-muted cursor-pointer",
            selected
              ? "border-primary bg-primary text-primary-foreground hover:bg-primary/90"
              : "border-border/80 bg-background/50",
          )}
          aria-label={t("selectItem")}
        >
          {selected ? (
            <Check className="h-3 w-3" />
          ) : (
            <span className="h-3 w-3" />
          )}
        </button>
      </div>

      {/* ── Visual Thumbnail / Icon ───────────────────────────────────────── */}
      <Link
        href={item.href}
        onClick={handleRowLinkClick}
        className="relative flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border bg-muted/50 transition-transform group-hover:scale-105"
      >
        {isDirectory ? (
          <div
            className={cn(
              "flex h-full w-full items-center justify-center bg-linear-to-br",
              dirColor.gradient,
            )}
          >
            <DirThumbnailIcon className={cn("h-5 w-5", dirColor.iconClass)} />
          </div>
        ) : materialPreviewData ? (
          <MaterialPreview material={materialPreviewData} lazy className="h-full w-full" />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-card">
            <FileText className="h-5 w-5 text-muted-foreground/70" />
          </div>
        )}
      </Link>

      {/* ── Title, Subtitle, Badges ───────────────────────────────────────── */}
      <Link
        href={item.href}
        onClick={handleRowLinkClick}
        className="min-w-0 flex-1 flex flex-col justify-center focus-visible:outline-hidden"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <p className="truncate text-sm font-semibold group-hover:text-primary transition-colors">
            {item.title}
          </p>
          <Badge
            variant="secondary"
            className={cn(
              "shrink-0 text-[10px] font-semibold uppercase tracking-wider py-0 px-1.5",
              !isDirectory && EXT_BADGE_COLORS[badgeLabel.toLowerCase()],
            )}
          >
            {badgeLabel}
          </Badge>
          {rawStatus && (
            <ContentStatusBadge status={rawStatus} className="text-[10px] py-0" />
          )}
        </div>
        {item.description ? (
          <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
            {item.description}
          </p>
        ) : relativeTime ? (
          <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground/75">
            <Calendar className="h-3 w-3 inline shrink-0" />
            <span>{relativeTime}</span>
          </p>
        ) : null}
      </Link>

      {/* ── Metrics (Views, Likes) ────────────────────────────────────────── */}
      {!isDirectory && ((item.total_views ?? 0) > 0 || (item.like_count ?? 0) > 0) && (
        <div className="hidden sm:flex items-center gap-3 shrink-0 tabular-nums text-xs text-muted-foreground px-2">
          {(item.total_views ?? 0) > 0 && (
            <span className="flex items-center gap-1" title="Views">
              <Eye className="h-3.5 w-3.5 opacity-70" />
              {item.total_views?.toLocaleString()}
            </span>
          )}
          {(item.like_count ?? 0) > 0 && (
            <span className="flex items-center gap-1" title="Likes">
              <ThumbsUp className="h-3.5 w-3.5 opacity-70" />
              {item.like_count?.toLocaleString()}
            </span>
          )}
        </div>
      )}

      {/* ── Collection Picker ────────────────────────────────────────────── */}
      <div className="shrink-0 w-36 sm:w-44">
        <CollectionPicker
          targetType={item.target_type}
          targetId={item.target_id}
          onChanged={onCollectionsChanged}
          className="h-8 text-xs font-medium"
        />
      </div>

      {/* ── Row Context Menu & Quick Remove ──────────────────────────────── */}
      <div className="flex items-center gap-1 shrink-0">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
              <MoreVertical className="h-4 w-4" />
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

        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
          disabled={removing}
          onClick={() => void remove()}
          title={collectionId ? t("removeFromCollection") : t("removeSaved")}
          aria-label={collectionId ? t("removeFromCollection") : t("removeSaved")}
        >
          {removing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : collectionId ? (
            <X className="h-4 w-4" />
          ) : (
            <StarOff className="h-4 w-4" />
          )}
        </Button>
      </div>
    </div>
  );
});
