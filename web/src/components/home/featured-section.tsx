"use client";

import Link from "next/link";
import { Star, ArrowRight, Tag } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getFileTypeStyle, getMaterialBrowsePath } from "./file-type-display";
import { SectionHeader } from "./section-header";
import type { FeaturedItem } from "./types";
import { useTranslations } from "next-intl";

interface FeaturedSectionProps {
  items: FeaturedItem[];
}

// ─────────────────────────────────────────────
// Single item — full-width hero card
// ─────────────────────────────────────────────
function FeaturedHeroCard({ item }: { item: FeaturedItem }) {
  const t = useTranslations("Home");
  const material = item.material;
  const versionInfo = material.current_version_info;
  const fileName = versionInfo?.file_name ?? null;
  const mimeType = versionInfo?.file_mime_type ?? null;
  const { gradient, iconColorClass, Icon } = getFileTypeStyle(
    fileName,
    mimeType,
  );

  const title = item.title ?? material.title;
  const description = item.description ?? material.description;
  const browsePath = getMaterialBrowsePath(material);

  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
      <div className="flex flex-col sm:flex-row">
        {/* Gradient panel */}
        <div
          className={cn(
            "relative flex shrink-0 items-center justify-center bg-linear-to-br sm:w-64 sm:rounded-none",
            gradient,
            "h-48 sm:h-auto",
          )}
        >
          <Icon
            className={cn(
              "h-20 w-20 opacity-85 drop-shadow-md",
              iconColorClass,
            )}
          />

          {/* Featured pill */}
          <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full border border-white/30 bg-white/20 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur-sm">
            <Star className="h-3 w-3 fill-white" />
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

            {material.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                <Tag className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60 self-center" />
                {material.tags.slice(0, 6).map((tag) => (
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
                {t("viewMaterial")}
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
  const versionInfo = material.current_version_info;
  const fileName = versionInfo?.file_name ?? null;
  const mimeType = versionInfo?.file_mime_type ?? null;
  const { gradient, iconColorClass, Icon } = getFileTypeStyle(
    fileName,
    mimeType,
  );

  const title = item.title ?? material.title;
  const description = item.description ?? material.description;
  const browsePath = getMaterialBrowsePath(material);

  return (
    <Link
      href={browsePath}
      className="group block w-75 flex-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
    >
      <div className="flex h-full flex-col rounded-xl border bg-card shadow-sm overflow-hidden transition-all duration-200 group-hover:shadow-md group-hover:-translate-y-0.5">
        {/* Gradient banner */}
        <div
          className={cn(
            "relative flex h-40 shrink-0 items-center justify-center bg-linear-to-br",
            gradient,
          )}
        >
          <Icon
            className={cn(
              "h-16 w-16 opacity-85 drop-shadow-sm",
              iconColorClass,
            )}
          />

          {/* Featured pill */}
          <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full border border-white/30 bg-white/20 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm">
            <Star className="h-2.5 w-2.5 fill-white" />
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

          {material.tags.length > 0 && (
            <div className="hidden flex-wrap gap-1 pt-0.5 sm:flex">
              {material.tags.slice(0, 3).map((tag) => (
                <Badge
                  key={tag}
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0.5"
                >
                  {tag}
                </Badge>
              ))}
              {material.tags.length > 3 && (
                <span className="text-[10px] text-muted-foreground self-center">
                  +{material.tags.length - 3}
                </span>
              )}
            </div>
          )}

          <div className="mt-auto hidden pt-2 sm:block">
            <span className="inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:underline">
              {t("viewMaterial")}
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
            <div className="flex gap-4 overflow-x-auto pb-3 scrollbar-none [scrollbar-width:none] [-ms-overflow-style:none]">
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
