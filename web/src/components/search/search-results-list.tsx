"use client";

import * as React from "react";
import {
  ArrowRight,
  BookOpen,
  ExternalLink,
  Eye,
  File,
  FileCode,
  FileText,
  FolderOpen,
  GraduationCap,
  HelpCircle,
  Lightbulb,
  Link2,
  MessageSquare,
  Star,
  ThumbsUp,
  Video,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { ContentStatusBadge } from "@/components/content-status-badge";
import { SearchResultThumbnail } from "@/components/search/search-modal";
import { DirectoryGridCard } from "@/components/browse/directory-grid-card";
import { MaterialGridCard } from "@/components/browse/material-grid-card";
import type { SearchResult } from "@/components/search/use-search";
import { Badge } from "@/components/ui/badge";
import { getFileBadgeLabel } from "@/lib/file-utils";
import { cn } from "@/lib/utils";

export function getMaterialTypeIcon(type: string): React.ReactNode {
  switch (type) {
    case "document":
      return <FileText className="size-3.5" />;
    case "polycopie":
      return <BookOpen className="size-3.5" />;
    case "annal":
      return <GraduationCap className="size-3.5" />;
    case "cheatsheet":
      return <FileCode className="size-3.5" />;
    case "tip":
      return <Lightbulb className="size-3.5" />;
    case "review":
      return <Star className="size-3.5" />;
    case "discussion":
      return <MessageSquare className="size-3.5" />;
    case "video":
      return <Video className="size-3.5" />;
    case "qcm":
      return <HelpCircle className="size-3.5" />;
    case "link":
      return <Link2 className="size-3.5" />;
    default:
      return <File className="size-3.5" />;
  }
}

export function getStatusIndicatorClass(status: string): string {
  switch (status) {
    case "important":
      return "bg-purple-500";
    case "current":
      return "bg-emerald-500";
    case "deprecated":
      return "bg-amber-500";
    case "archived":
      return "bg-zinc-400";
    default:
      return "bg-primary";
  }
}

export function getBrowsePathInfo(result: SearchResult): { pathBase: string; slug: string } {
  const fullPath =
    result.browse_path ||
    (result.search_type === "directory"
      ? `/directories/${result.id}`
      : `/materials/${result.id}`);

  const lastSlash = fullPath.lastIndexOf("/");
  if (lastSlash > 0) {
    return {
      pathBase: fullPath.slice(0, lastSlash),
      slug: result.slug || fullPath.slice(lastSlash + 1),
    };
  }
  return {
    pathBase: "",
    slug: fullPath.replace(/^\//, ""),
  };
}

export function SearchListCard({
  result,
  onSelect,
}: {
  result: SearchResult;
  onSelect: (result: SearchResult) => void;
}) {
  const t = useTranslations("Search");
  const isDirectory = result.search_type === "directory";
  const title = result.title || result.name || result.file_name || t("untitled");
  const fileBadge = isDirectory
    ? null
    : getFileBadgeLabel(result.file_name || title, result.file_mime_type);
  const location = result.ancestor_path?.trim() || t("libraryRoot");
  const isLink = result.type === "link" || Boolean(result.url);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(result)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(result);
        }
      }}
      className="group relative flex flex-col sm:flex-row items-stretch sm:items-start gap-3 sm:gap-3.5 rounded-xl border border-border/70 bg-card p-3.5 sm:p-4 transition-all duration-200 hover:border-primary/40 hover:bg-accent/40 hover:shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
    >
      {/* Top Header on Mobile / Left column on Desktop */}
      <div className="flex items-center sm:items-start gap-3 shrink-0">
        <SearchResultThumbnail result={result} />
        {/* Mobile-only header info */}
        <div className="sm:hidden min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-foreground text-sm line-clamp-2 break-words group-hover:text-primary transition-colors">
              {title}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1.5 flex-wrap">
            <Badge variant="outline" className="h-4.5 px-1.5 text-[10px] font-medium shrink-0">
              {isDirectory ? t("folder") : fileBadge || t("material")}
            </Badge>
            {result.status && result.status !== "current" && (
              <ContentStatusBadge status={result.status} interactive={false} />
            )}
          </div>
        </div>
      </div>

      {/* Main Content Info */}
      <div className="min-w-0 flex-1 space-y-1.5 w-full">
        {/* Desktop-only title & badges */}
        <div className="hidden sm:flex items-center gap-2 flex-wrap">
          <span className="font-semibold text-foreground text-sm sm:text-base group-hover:text-primary transition-colors line-clamp-1 break-all">
            {title}
          </span>
          <Badge variant="outline" className="h-4.5 px-1.5 text-[10px] font-medium shrink-0">
            {isDirectory ? t("folder") : fileBadge || t("material")}
          </Badge>
          {result.status && result.status !== "current" && (
            <ContentStatusBadge status={result.status} interactive={false} />
          )}
        </div>

        {/* Location Breadcrumb */}
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <FolderOpen className="size-3.5 shrink-0 opacity-70" />
          <span className="truncate">{location}</span>
        </div>

        {/* Description */}
        {result.description && (
          <p className="text-xs text-muted-foreground/90 line-clamp-2 leading-relaxed break-words">
            {result.description}
          </p>
        )}

        {/* Context Match Snippet */}
        {result.match_context && result.matched_field && (
          <div className="inline-flex max-w-full items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground border border-border/50 overflow-hidden">
            <span className="font-medium text-foreground/80 shrink-0">
              {t(`matchedFields.${result.matched_field}`)}:
            </span>
            <span className="truncate font-mono text-[11px] text-foreground/90">
              {result.match_context}
            </span>
          </div>
        )}

        {/* Tags List */}
        {result.tags && result.tags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            {result.tags.slice(0, 4).map((tag) => (
              <span
                key={tag}
                className="rounded-md bg-secondary/80 px-1.5 py-0.5 text-[10px] text-secondary-foreground font-medium truncate max-w-[120px]"
              >
                #{tag}
              </span>
            ))}
            {result.tags.length > 4 && (
              <span className="text-[10px] text-muted-foreground shrink-0">
                +{result.tags.length - 4}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Engagement Stats & Actions: Responsive Footer on Mobile / Right Column on Desktop */}
      <div className="flex items-center justify-between sm:justify-end gap-3 w-full sm:w-auto self-stretch sm:self-center shrink-0 text-muted-foreground pt-2 sm:pt-0 border-t border-border/40 sm:border-0 mt-1 sm:mt-0">
        <div className="flex items-center gap-3">
          {result.total_views !== undefined && result.total_views > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px]">
              <Eye className="size-3.5" />
              {result.total_views}
            </span>
          )}
          {result.like_count !== undefined && result.like_count > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px]">
              <ThumbsUp className="size-3.5" />
              {result.like_count}
            </span>
          )}
        </div>
        <div className="rounded-full p-1 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5 group-hover:text-primary shrink-0">
          {isLink ? <ExternalLink className="size-4" /> : <ArrowRight className="size-4" />}
        </div>
      </div>
    </div>
  );
}

export function SearchGridItem({ result }: { result: SearchResult }) {
  const { pathBase, slug } = getBrowsePathInfo(result);

  if (result.search_type === "directory") {
    const dirData = {
      ...result,
      id: result.id,
      name: result.name || result.title || "",
      slug,
      status: result.status,
      metadata: result.metadata || {},
      child_directory_count: (result.metadata?.child_directory_count as number) ?? 0,
      child_material_count: (result.metadata?.child_material_count as number) ?? 0,
    };
    return <DirectoryGridCard directory={dirData} pathBase={pathBase} />;
  }

  const matData = {
    ...result,
    id: result.id,
    title: result.title || result.name || result.file_name || "",
    slug,
    type: result.type || "document",
    status: result.status,
    description: result.description || null,
    tags: result.tags || [],
    metadata: {
      url: result.url || (result.metadata?.url as string | undefined),
      link: (result.metadata?.link as string | undefined),
      ...(result.metadata || {}),
    },
    current_version_info: {
      id: "",
      material_id: result.id,
      version_number: 1,
      file_key: null,
      file_name: result.file_name || result.title || null,
      file_size: null,
      file_mime_type: result.file_mime_type || null,
      diff_summary: null,
      author_id: null,
      pr_id: null,
      virus_scan_result: "clean",
      created_at: "",
    },
  };
  return <MaterialGridCard material={matData} pathBase={pathBase} />;
}
