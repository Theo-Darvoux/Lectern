"use client";

import Link from "next/link";
import { Eye, ThumbsUp, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  formatFileSize,
  getFileBadgeLabel,
  getFileBadgeColor,
} from "@/lib/file-utils";
import { getMaterialBrowsePath } from "./file-type-display";
import { MaterialPreview } from "./material-preview";
import type { MaterialDetail } from "./types";
import { useTranslations } from "next-intl";

interface MaterialCardProps {
  material: MaterialDetail;
  className?: string;
  /** Hide the view-count stat on mobile (used on the home page to reduce clutter). */
  hideViewsOnMobile?: boolean;
}

export function MaterialCard({ material, className, hideViewsOnMobile }: MaterialCardProps) {
  const t = useTranslations("Home");
  const versionInfo = material.current_version_info;
  const fileName = versionInfo?.file_name ?? null;
  const mimeType = versionInfo?.file_mime_type ?? null;
  const fileSize = versionInfo?.file_size ?? null;

  const badgeColor = getFileBadgeColor(fileName ?? "", mimeType ?? undefined);
  const badgeLabel = getFileBadgeLabel(fileName ?? "", mimeType ?? undefined);
  const browsePath = getMaterialBrowsePath(material);

  return (
    <Link
      href={browsePath}
      className={cn(
        "block w-full group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl",
        className,
      )}
    >
      <div className="rounded-xl border bg-card shadow-sm overflow-hidden transition-all duration-300 group-hover:shadow-lg group-hover:-translate-y-1 h-full flex flex-col ring-1 ring-border/50 group-hover:ring-primary/20">
        {/* Preview area — 4:3 aspect ratio */}
        <div className="aspect-4/3 relative overflow-hidden shrink-0">
          <MaterialPreview material={material} />

          {/* File-type badge overlay — Premium Glassmorphism style */}
          <span
            className={cn(
              "absolute top-2 right-2 rounded px-1.5 py-0.5 text-[10px] font-bold leading-none shadow-xs backdrop-blur-md ring-1 ring-white/20",
              badgeColor,
            )}
          >
            {badgeLabel}
          </span>
        </div>

        {/* Card body */}
        <div className="p-3 flex flex-col gap-1.5 flex-1 min-w-0">
          {/* Title */}
          <p className="font-medium text-sm leading-snug line-clamp-2 text-foreground">
            {material.title}
          </p>

          {/* Filename */}
          {fileName && (
            <p
              className="text-[11px] text-muted-foreground truncate"
              title={fileName}
            >
              {fileName}
            </p>
          )}

          {/* File size */}
          {fileSize !== null && (
            <p className="text-[11px] text-muted-foreground/80">
              {formatFileSize(fileSize)}
            </p>
          )}

          {/* Directory path */}
          {material.directory_path && (
            <p
              className="text-[11px] text-muted-foreground/70 truncate flex items-center gap-1"
              title={material.directory_path}
            >
              <FolderOpen className="h-3 w-3 shrink-0" />
              {material.directory_path}
            </p>
          )}

          {/* Stats row — pushed to the bottom */}
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground pt-1 mt-auto">
            <span
              className={cn(
                "items-center gap-1",
                hideViewsOnMobile ? "hidden sm:flex" : "flex",
              )}
              title={t("totalViews")}
            >
              <Eye className="h-3 w-3" />
              {material.total_views.toLocaleString()}
            </span>
            <span
              className={cn(
                "flex items-center gap-1",
                material.is_liked && "text-primary",
              )}
              title={t("likes")}
            >
              <ThumbsUp
                className={cn(
                  "h-3 w-3",
                  material.is_liked && "fill-primary text-primary",
                )}
              />
              {material.like_count.toLocaleString()}
            </span>
          </div>
        </div>
      </div>
    </Link>
  );
}
