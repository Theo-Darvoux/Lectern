"use client";

import Link from "next/link";
import { Star, ArrowRight, Tag, Folder } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getMaterialBrowsePath } from "./file-type-display";
import { SectionHeader } from "./section-header";
import type { FeaturedItem, DirectoryDetail } from "./types";
import { useTranslations } from "next-intl";
import { MaterialPreview } from "./material-preview";
import { DirectoryPreviewCollage } from "@/components/browse/directory-preview-collage";
import { getDirectoryIcon } from "@/lib/directory-icons";
import { getDirectoryColor } from "@/lib/directory-colors";

interface FeaturedSectionProps {
  items: FeaturedItem[];
}

// ─────────────────────────────────────────────
// Directory thumbnail — collage of contained materials, custom icon, or
// the signature folder gradient. Mirrors the in-directory grid card.
// ─────────────────────────────────────────────
function DirectoryThumbnail({
  directory,
  iconSize,
}: {
  directory: DirectoryDetail;
  iconSize: string;
}) {
  const metadata = (directory.metadata ?? {}) as Record<string, unknown>;
  const iconId = metadata.thumbnail_icon ? String(metadata.thumbnail_icon) : null;
  const colorId = metadata.thumbnail_color ? String(metadata.thumbnail_color) : null;
  const previewIds = directory.preview_material_ids ?? [];
  const showCollage = !iconId && previewIds.length > 0;

  if (showCollage) {
    return (
      <div className="absolute inset-0 bg-muted">
        <DirectoryPreviewCollage materialIds={previewIds} />
      </div>
    );
  }

  // Custom icon/colour set in browse → mirror that theme; otherwise keep the
  // signature amber→orange folder.
  if (iconId || colorId) {
    const { Icon } = getDirectoryIcon(iconId);
    const { gradient, iconClass } = getDirectoryColor(colorId);
    return (
      <div className={cn("absolute inset-0 flex items-center justify-center bg-linear-to-br", gradient)}>
        <Icon className={cn(iconSize, "drop-shadow-sm", iconClass)} />
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex items-center justify-center bg-linear-to-br from-amber-400 to-orange-500">
      <Folder className={cn(iconSize, "opacity-85 drop-shadow-md text-white")} />
    </div>
  );
}

// ─────────────────────────────────────────────
// Single item — full-width hero card
// ─────────────────────────────────────────────
function FeaturedHeroCard({ item }: { item: FeaturedItem }) {
  const t = useTranslations("Home");
  const material = item.material;
  const directory = item.directory;

  const title = item.title ?? (directory ? directory.name : (material?.title || t("untitled")));
  const description = item.description ?? (directory ? directory.description : (material?.description || null));
  
  const browsePath = directory
    ? (directory.full_path ? `/browse/${directory.full_path}` : `/browse`)
    : (material ? getMaterialBrowsePath(material) : "#");

  const tags = directory ? directory.tags : (material?.tags || []);
  const viewText = directory ? t("viewFolder") : t("viewMaterial");

  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
      <div className="flex flex-col sm:flex-row">
        {/* Thumbnail/Gradient panel */}
        <div
          className={cn(
            "relative flex shrink-0 items-center justify-center sm:w-64 sm:rounded-none h-48 sm:h-auto sm:min-h-[220px] overflow-hidden",
          )}
        >
          {material ? (
            <MaterialPreview
              material={material}
              className="absolute inset-0 h-full w-full"
              lazy={false}
            />
          ) : directory ? (
            <DirectoryThumbnail directory={directory} iconSize="h-20 w-20" />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-linear-to-br from-amber-400 to-orange-500">
              <Folder className="h-20 w-20 opacity-85 drop-shadow-md text-white" />
            </div>
          )}

          {/* Featured pill */}
          <span className="absolute left-3 top-3 z-20 inline-flex items-center gap-1 rounded-full border border-white/25 bg-black/55 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur-sm shadow-sm">
            <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
            {t("featured")}
          </span>
        </div>

        {/* Content */}
        <div className="flex flex-1 flex-col justify-between p-5 sm:p-6">
          <div className="space-y-2.5">
            <h2 className="text-xl font-bold leading-snug tracking-tight sm:text-2xl">
              {title}
            </h2>

            {description && (
              <p className="line-clamp-3 text-sm text-muted-foreground sm:line-clamp-4">
                {description}
              </p>
            )}

            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                <Tag className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60 self-center" />
                {tags.slice(0, 6).map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-xs">
                    {tag}
                  </Badge>
                ))}
              </div>
            )}
          </div>

          <div className="mt-5">
            <Button asChild>
              <Link href={browsePath}>
                {viewText}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Multiple items — card in horizontal scroll row
// ─────────────────────────────────────────────
function FeaturedScrollCard({ item }: { item: FeaturedItem }) {
  const t = useTranslations("Home");
  const material = item.material;
  const directory = item.directory;

  const title = item.title ?? (directory ? directory.name : (material?.title || t("untitled")));
  const description = item.description ?? (directory ? directory.description : (material?.description || null));
  
  const browsePath = directory
    ? (directory.full_path ? `/browse/${directory.full_path}` : `/browse`)
    : (material ? getMaterialBrowsePath(material) : "#");

  const tags = directory ? directory.tags : (material?.tags || []);
  const viewText = directory ? t("viewFolder") : t("viewMaterial");

  return (
    <Link
      href={browsePath}
      className="group block w-72 flex-none sm:w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
    >
      <div className="flex h-full flex-col rounded-xl border bg-card shadow-sm overflow-hidden transition-all duration-200 group-hover:shadow-md group-hover:-translate-y-0.5">
        {/* Thumbnail/Gradient banner */}
        <div
          className={cn(
            "relative flex h-40 shrink-0 items-center justify-center overflow-hidden",
          )}
        >
          {material ? (
            <MaterialPreview
              material={material}
              className="absolute inset-0 h-full w-full"
              lazy={false}
            />
          ) : directory ? (
            <DirectoryThumbnail directory={directory} iconSize="h-16 w-16" />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-linear-to-br from-amber-400 to-orange-500">
              <Folder className="h-16 w-16 opacity-85 drop-shadow-sm text-white" />
            </div>
          )}

          {/* Featured pill */}
          <span className="absolute left-3 top-3 z-20 inline-flex items-center gap-1 rounded-full border border-white/25 bg-black/55 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm shadow-sm">
            <Star className="h-2.5 w-2.5 fill-amber-400 text-amber-400" />
            {t("featured")}
          </span>
        </div>

        {/* Content */}
        <div className="flex flex-1 flex-col gap-2 p-4">
          <h3 className="font-semibold leading-snug line-clamp-2 text-sm">
            {title}
          </h3>

          {description && (
            <p className="hidden text-xs text-muted-foreground line-clamp-2 flex-1 sm:block">
              {description}
            </p>
          )}

          {tags.length > 0 && (
            <div className="hidden flex-wrap gap-1 pt-0.5 sm:flex">
              {tags.slice(0, 3).map((tag) => (
                <Badge
                  key={tag}
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0.5"
                >
                  {tag}
                </Badge>
              ))}
              {tags.length > 3 && (
                <span className="text-[10px] text-muted-foreground self-center">
                  +{tags.length - 3}
                </span>
              )}
            </div>
          )}

          <div className="mt-auto hidden pt-2 sm:block">
            <span className="inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:underline">
              {viewText}
              <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
            </span>
          </div>
        </div>
      </div>
    </Link>
  );
}

// ─────────────────────────────────────────────
// Public export
// ─────────────────────────────────────────────
export function FeaturedSection({ items }: FeaturedSectionProps) {
  const t = useTranslations("Home");
  if (items.length === 0) return null;

  return (
    <section aria-label={t("featuredMaterials")}>
      <SectionHeader
        title={t("featured")}
        subtitle={t("highlightedMaterials")}
      />

      <div className="mt-4">
        {items.length === 1 ? (
          <FeaturedHeroCard item={items[0]} />
        ) : (
          <div>
            <div className="flex gap-4 overflow-x-auto pb-3 scrollbar-none [scrollbar-width:none] [-ms-overflow-style:none] sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 sm:overflow-x-visible sm:pb-0">
              {items.map((item) => (
                <FeaturedScrollCard key={item.id} item={item} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
