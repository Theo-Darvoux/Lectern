"use client";

import { memo } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Folder, Info, MessageSquare, ChevronRight } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useUIStore } from "@/lib/stores";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import Link from "next/link";

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
}: DirectoryGridCardProps) {
  const t = useTranslations("Browse");
  const openSidebar = useUIStore((s) => s.openSidebar);
  const pathname = usePathname();
  const router = useRouter();

  const name = String(directory.name ?? "");
  const slug = String(directory.slug ?? "");
  const id = String(directory.id ?? "");
  const childDirCount = Number(directory.child_directory_count ?? 0);
  const childMatCount = Number(directory.child_material_count ?? 0);
  const totalCount = childDirCount + childMatCount;

  const buildPath = () => {
    const base = pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
    const dirPath = `${base}/${slug}`;
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
          : "text-blue-400";

  const bgGradient =
    staged === "deleted"
      ? "from-red-100 to-rose-200 dark:from-red-950/40 dark:to-rose-900/30"
      : staged === "moved"
        ? "from-amber-100 to-orange-200 dark:from-amber-950/40 dark:to-orange-900/30"
        : staged === "created" || staged === "edited"
          ? isExternal
            ? "from-blue-100 to-indigo-200 dark:from-blue-950/40 dark:to-indigo-900/30"
            : "from-green-100 to-emerald-200 dark:from-green-950/40 dark:to-emerald-900/30"
          : "from-blue-50 to-indigo-100 dark:from-blue-950/30 dark:to-indigo-900/20";

  const textColor =
    staged === "deleted"
      ? "line-through text-red-700 dark:text-red-400"
      : staged === "moved"
        ? "text-amber-700 dark:text-amber-400"
        : staged === "created" || staged === "edited"
          ? `text-${themeColor}-700 dark:text-${themeColor}-400`
          : "";

  const isRestricted = !!staged || !!previewPrId;

  const handleCardClick = (e: React.MouseEvent) => {
    if (selectMode && onToggleSelect) {
      onToggleSelect(navIndex ?? 0, e);
      return;
    }
    if (onNavigate) {
      onNavigate();
    } else {
      router.push(buildPath());
    }
  };

  const handleDetails = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openSidebar("details", { type: "directory", id, data: { ...directory, __path: buildPath() } });
  };

  const handleChat = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openSidebar("chat", { type: "directory", id, data: directory });
  };

  return (
    <ItemActionsMenu
      item={{ id, type: "directory", data: directory, staged, isExternal }}
      itemPath={buildPath()}
    >
      <div
        onClick={handleCardClick}
        data-nav-index={navIndex}
        style={{ contentVisibility: "auto", containIntrinsicSize: "0 240px" }}
        className={cn(
          "group relative rounded-xl border bg-card shadow-sm overflow-hidden cursor-pointer",
          "ring-1 ring-border/50 hover:ring-primary/30",
          stagedRing,
          selectMode && selected ? "bg-primary/5 dark:bg-primary/10 ring-primary" : "",
          focused ? "ring-2 ring-primary/40" : "",
        )}
      >
        {/* Icon area */}
        <div className={cn("aspect-[4/3] relative flex items-center justify-center bg-linear-to-br overflow-hidden", bgGradient)}>
          <Folder className={cn("h-14 w-14", iconColor)} />

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
              onClick={(e) => { e.stopPropagation(); onToggleSelect?.(navIndex ?? 0, e); }}
            >
              <Checkbox checked={!!selected} onCheckedChange={() => {}} className="h-5 w-5 bg-background/95" />
            </div>
          )}

          {/* Hover action overlay — translate is compositor-composited, unlike opacity which can trigger paint. */}
          {!selectMode && (
            <div className="absolute inset-x-0 bottom-0 z-10 flex items-center justify-end gap-0.5 p-1.5 translate-y-full group-hover:translate-y-0 has-[[data-state=open]]:translate-y-0 transition-transform duration-150 bg-black/30">
              {!isRestricted && (
                <button
                  onClick={handleChat}
                  className="rounded-md p-1.5 hover:bg-white/20 active:scale-95 transition-transform"
                  title={t("chat")}
                  aria-label={t("chatAbout", { title: name })}
                >
                  <MessageSquare className="h-3.5 w-3.5 text-white" />
                </button>
              )}
              <button
                onClick={handleDetails}
                className="rounded-md p-1.5 hover:bg-white/20 active:scale-95 transition-transform"
                title={t("details")}
                aria-label={t("viewDetailsFor", { title: name })}
              >
                <Info className="h-3.5 w-3.5 text-white" />
              </button>
              <ItemActionsDropdownTrigger />
              <Link
                href={buildPath()}
                className="rounded-md p-1.5 hover:bg-white/20 active:scale-95 transition-transform"
                title={t("openItem")}
                onClick={(e) => {
                  if (onNavigate) {
                    e.preventDefault();
                    e.stopPropagation();
                    onNavigate();
                  } else {
                    e.stopPropagation();
                  }
                }}
                aria-label={t("openItemFor", { title: name })}
              >
                <ChevronRight className="h-3.5 w-3.5 text-white" />
              </Link>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-2.5 flex flex-col gap-0.5 min-w-0">
          <p className={cn("text-sm font-medium leading-snug line-clamp-2", textColor || "text-foreground")}>
            {name}
          </p>
          <p className={cn("text-[11px]", staged ? `text-${themeColor}-600/70` : "text-muted-foreground")}>
            {t("itemsCount", { count: totalCount })}
          </p>
        </div>
      </div>
    </ItemActionsMenu>
  );
}

export const DirectoryGridCard = memo(DirectoryGridCardImpl);
