"use client";

import { memo, useRef } from "react";
import { useRouter } from "next/navigation";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";
import { Info, MessageSquare } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useUIStore } from "@/lib/stores";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import Link from "next/link";
import { getDirectoryIcon } from "@/lib/directory-icons";
import { getDirectoryColor } from "@/lib/directory-colors";
import { DirectoryPreviewCollage } from "./directory-preview-collage";
import { useDirectoryIconOverrides, useDirectoryColorOverrides } from "@/lib/stores";

// Frosted-glass circular action button — reads on any thumbnail (light, dark or
// colourful) without a scrim band behind it.
const FLOATING_ACTION_BTN =
  "flex items-center justify-center h-8 w-8 rounded-full bg-background/85 text-foreground/80 " +
  "backdrop-blur-md ring-1 ring-border/60 shadow-md hover:bg-background hover:text-foreground " +
  "active:scale-90 transition-all";

interface DirectoryGridCardProps {
  directory: Record<string, unknown>;
  staged?: "edited" | "deleted" | "moved" | "created" | null;
  isExternal?: boolean;
  selectMode?: boolean;
  selected?: boolean;
  onToggleSelect?: (index: number, e?: React.MouseEvent) => void;
  previewPrId?: string;
  navIndex?: number;
  focused?: boolean;
  onNavigate?: () => void;
  /** Current pathname base (without trailing slash), hoisted from parent to avoid per-item usePathname subscription */
  pathBase: string;
}

function DirectoryGridCardImpl({
  directory,
  staged,
  isExternal,
  selectMode,
  selected,
  onToggleSelect,
  previewPrId,
  navIndex,
  focused,
  onNavigate,
  pathBase,
}: DirectoryGridCardProps) {
  const t = useTranslations("Browse");
  const openSidebar = useUIStore((s) => s.openSidebar);
  const router = useRouter();

  const name = String(directory.name ?? "");
  const slug = String(directory.slug ?? "");
  const id = String(directory.id ?? "");
  const childDirCount = Number(directory.child_directory_count ?? 0);
  const childMatCount = Number(directory.child_material_count ?? 0);
  const totalCount = childDirCount + childMatCount;
  const metadata = (directory.metadata ?? {}) as Record<string, unknown>;
  const iconOverrides = useDirectoryIconOverrides((s) => s.overrides);
  const colorOverrides = useDirectoryColorOverrides((s) => s.overrides);
  const rawIconId = metadata.thumbnail_icon ? String(metadata.thumbnail_icon) : null;
  const rawColorId = metadata.thumbnail_color ? String(metadata.thumbnail_color) : null;
  const thumbnailIconId = iconOverrides.has(id) ? (iconOverrides.get(id) ?? null) : rawIconId;
  const thumbnailColorId = colorOverrides.has(id) ? (colorOverrides.get(id) ?? null) : rawColorId;
  const previewMaterialIds = Array.isArray(directory.preview_material_ids)
    ? (directory.preview_material_ids as string[])
    : [];
  const { Icon: ThumbnailIcon } = getDirectoryIcon(thumbnailIconId);
  const { gradient: customGradient, iconClass: customIconClass, folderBodyClass: customFolderBody, folderTabClass: customFolderTab } = getDirectoryColor(thumbnailColorId);
  const showCollage = !thumbnailIconId && previewMaterialIds.length > 0;

  // Folder body and tab use same-hue pairs (tab is darker) so they read as
  // one unified object. Staged states override the directory's own color.
  const folderBodyColor =
    staged === "deleted" ? "bg-red-300 dark:bg-red-700"
    : staged === "moved" ? "bg-amber-300 dark:bg-amber-700"
    : staged === "created" || staged === "edited"
      ? isExternal ? "bg-blue-300 dark:bg-blue-700" : "bg-green-300 dark:bg-green-700"
      : customFolderBody;

  const folderTabColor =
    staged === "deleted" ? "bg-red-500 dark:bg-red-800"
    : staged === "moved" ? "bg-amber-500 dark:bg-amber-800"
    : staged === "created" || staged === "edited"
      ? isExternal ? "bg-blue-500 dark:bg-blue-800" : "bg-green-500 dark:bg-green-800"
      : customFolderTab;

  const buildPath = () => {
    const dirPath = `${pathBase}/${slug}`;
    return previewPrId ? `${dirPath}?preview_pr=${previewPrId}` : dirPath;
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

  const stagedRing = staged
    ? `ring-2 ${borderStyle === "border-solid" ? "" : "[border-style:dashed]"} ring-${themeColor}-400`
    : "";

  const iconColor =
    staged === "deleted"
      ? "text-red-400"
      : staged === "moved"
        ? "text-amber-400"
        : staged === "created" || staged === "edited"
          ? `text-${themeColor}-400`
          : customIconClass;

  const bgGradient =
    staged === "deleted"
      ? "from-red-100 to-rose-200 dark:from-red-950/40 dark:to-rose-900/30"
      : staged === "moved"
        ? "from-amber-100 to-orange-200 dark:from-amber-950/40 dark:to-orange-900/30"
        : staged === "created" || staged === "edited"
          ? isExternal
            ? "from-blue-100 to-indigo-200 dark:from-blue-950/40 dark:to-indigo-900/30"
            : "from-green-100 to-emerald-200 dark:from-green-950/40 dark:to-emerald-900/30"
          : customGradient;

  const textColor =
    staged === "deleted"
      ? "line-through text-red-700 dark:text-red-400"
      : staged === "moved"
        ? "text-amber-700 dark:text-amber-400"
        : staged === "created" || staged === "edited"
          ? `text-${themeColor}-700 dark:text-${themeColor}-400`
          : "";

  const isRestricted = !!staged || !!previewPrId;

  const prefetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handlePointerEnter = () => {
    if (!slug || staged === "deleted" || onNavigate) return;
    prefetchTimer.current = setTimeout(() => {
      const browsePath = `${pathBase}/${slug}`.replace(/^\/browse\/?/, "").replace(/\/$/, "");
      prefetchBrowsePath(browsePath);
      router.prefetch(`${pathBase}/${slug}`);
    }, 100);
  };
  const handlePointerLeave = () => {
    if (prefetchTimer.current) {
      clearTimeout(prefetchTimer.current);
      prefetchTimer.current = null;
    }
  };

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
    if (e.ctrlKey || e.metaKey) {
      return; // let browser open in new tab natively via <a href>
    }
    if (onNavigate) {
      e.preventDefault();
      onNavigate();
    }
    // else: let Next.js Link handle client-side navigation
  };

  const handleDetails = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Drop focus so the buttons don't stay revealed via group-focus-within
    // once the pointer leaves the card.
    (e.currentTarget as HTMLElement).blur();
    openSidebar("details", { type: "directory", id, data: { ...directory, __path: buildPath() } });
  };

  const handleChat = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).blur();
    openSidebar("chat", { type: "directory", id, data: directory });
  };

  return (
    <ItemActionsMenu
      item={{ id, type: "directory", data: directory, staged, isExternal }}
      itemPath={buildPath()}
    >
      <Link
        href={buildPath()}
        onClick={handleCardClick}
        onPointerEnter={handlePointerEnter}
        onPointerLeave={handlePointerLeave}
        data-nav-index={navIndex}
        className={cn(
          "group relative flex flex-col gap-2 cursor-pointer rounded-xl transition-colors",
          stagedRing,
          selectMode && selected ? "bg-primary/5 dark:bg-primary/10 ring-1 ring-primary p-2 -m-2" : "",
          focused ? "ring-2 ring-primary/40 p-2 -m-2" : "",
        )}
      >
        {/* Icon area */}
        <div className="aspect-[4/3] relative flex items-center justify-center overflow-hidden rounded-xl">
          <div className="absolute inset-x-0 bottom-0 top-[16%]">
            <div className="relative w-full h-full transition-all duration-500 ease-out drop-shadow-md group-hover:drop-shadow-xl">
              {/* Tab — darker shade of body, sits above the body's top-left */}
              <div
                className={cn("absolute left-0 w-[34%] rounded-t-[7px]", folderTabColor)}
                style={{ bottom: "100%", height: "16%" }}
              />
              {/* Folder body — fills the card's preview area; the box IS the folder */}
              <div
                className={cn(
                  "absolute inset-0 rounded-b-2xl rounded-tr-2xl overflow-hidden flex items-center justify-center isolate",
                  folderBodyColor,
                )}
              >
                {/* Subtle top-edge highlight to give the folder a lit, 3-D feel */}
                <div className="absolute inset-x-0 top-0 h-[20%] bg-white/20 pointer-events-none rounded-tr-2xl" />
                {/* Bottom-edge shadow stripe for depth */}
                <div className="absolute inset-x-0 bottom-0 h-[8%] bg-black/10 pointer-events-none" />
                
                {showCollage ? (
                  <div className="absolute inset-[6%] rounded-lg overflow-hidden ring-1 ring-black/15 dark:ring-black/40 shadow-md">
                    {/* Thumbnail collage inset inside the folder — gaps kept around it */}
                    <DirectoryPreviewCollage materialIds={previewMaterialIds} />
                  </div>
                ) : (
                  <>
                    {/* Huge watermark */}
                    <ThumbnailIcon className={cn("absolute h-48 w-48 opacity-[0.15] -rotate-12 translate-y-4 translate-x-4 pointer-events-none", iconColor)} />

                    {/* Main icon container with hover scale */}
                    <div className="relative z-10 p-4 bg-white/40 dark:bg-black/20 rounded-2xl shadow-lg backdrop-blur-md ring-1 ring-white/50 dark:ring-white/10 group-hover:scale-110 group-hover:shadow-xl transition-all duration-500 ease-out">
                      <ThumbnailIcon className={cn("h-12 w-12 sm:h-14 sm:w-14 drop-shadow-sm", iconColor)} />
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

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

          {/* Floating action buttons — the kebab is always visible; the rest
              reveal on hover / keyboard focus. No scrim band: each control is a
              self-contained frosted-glass chip. The reveal animates transform +
              opacity only, so it stays on the compositor thread during scroll. */}
          {!selectMode && (
            <div
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
              className="absolute bottom-2 right-2 z-10 flex items-center gap-1.5"
            >
              {/* Secondary actions — hidden until hover / focus */}
              <div className="flex items-center gap-1.5 opacity-0 translate-x-1.5 pointer-events-none transition-all duration-200 ease-out group-hover:opacity-100 group-hover:translate-x-0 group-hover:pointer-events-auto group-focus-within:opacity-100 group-focus-within:translate-x-0 group-focus-within:pointer-events-auto">
                {!isRestricted && (
                  <button
                    onClick={handleChat}
                    className={FLOATING_ACTION_BTN}
                    title={t("chat")}
                    aria-label={t("chatAbout", { title: name })}
                  >
                    <MessageSquare className="h-4 w-4" />
                  </button>
                )}
                <button
                  onClick={handleDetails}
                  className={FLOATING_ACTION_BTN}
                  title={t("details")}
                  aria-label={t("viewDetailsFor", { title: name })}
                >
                  <Info className="h-4 w-4" />
                </button>

              </div>

              {/* Always-visible kebab */}
              <ItemActionsDropdownTrigger
                className="h-8 w-8 rounded-full bg-background/85 backdrop-blur-md ring-1 ring-border/60 shadow-lg hover:bg-background active:scale-90 transition-all"
                iconClassName="h-4 w-4 text-foreground"
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-1 flex flex-col gap-0.5 min-w-0">
          <p className={cn("text-sm font-medium leading-snug line-clamp-2", textColor || "text-foreground")}>
            {name}
          </p>
          <p className={cn("text-[11px]", staged ? `text-${themeColor}-600/70` : "text-muted-foreground")}>
            {t("itemsCount", { count: totalCount })}
          </p>
        </div>
      </Link>
    </ItemActionsMenu>
  );
}

export const DirectoryGridCard = memo(DirectoryGridCardImpl);
