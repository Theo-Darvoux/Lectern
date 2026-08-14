"use client";

import { memo, useRef } from "react";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";
import { BrowseLink } from "@/components/browse/browse-link";
import { Folder, ThumbsUp } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useLikeOverrides } from "@/lib/stores";
import { useTranslations } from "next-intl";

interface DirectoryLineItemProps {
    directory: Record<string, unknown>;
    staged?: "edited" | "deleted" | "moved" | "created" | null;
    isExternal?: boolean;
    selectMode?: boolean;
    selected?: boolean;
    onToggleSelect?: (index: number, e?: React.MouseEvent) => void;
    /** When set, appended as ?preview_pr= to preserve preview mode across navigation */
    previewPrId?: string;
    navIndex?: number;
    focused?: boolean;
    /** Special override for clicking on ghost directories (creations) */
    onNavigate?: () => void;
    /** Current pathname base (without trailing slash), hoisted from parent to avoid per-item usePathname subscription */
    pathBase: string;
    /** Hoisted from parent to avoid per-item useIsMobile subscription */
    isMobile: boolean;
}

function DirectoryLineItemImpl({
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
    isMobile,
}: DirectoryLineItemProps) {
    const t = useTranslations("Browse");

    const name = String(directory.name ?? "");
    const slug = String(directory.slug ?? "");
    const id = String(directory.id ?? "");
    const childDirCount = Number(directory.child_directory_count ?? 0);
    const childMatCount = Number(directory.child_material_count ?? 0);
    const totalCount = childDirCount + childMatCount;
    const likeOverride = useLikeOverrides((s) => s.directoryOverrides[id]);
    const likeCount = likeOverride !== undefined ? likeOverride.likeCount : Number(directory.like_count ?? 0);
    const isLiked = likeOverride !== undefined ? likeOverride.isLiked : Boolean(directory.is_liked);

    const buildPath = () => {
        const dirPath = `${pathBase}/${slug}`;
        return previewPrId ? `${dirPath}?preview_pr=${previewPrId}` : dirPath;
    };

    const prefetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const handlePointerEnter = () => {
        if (!slug || staged === "deleted" || onNavigate) return;
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

    const iconColor =
        staged === "deleted"
            ? "text-red-500"
            : staged === "moved"
                ? "text-amber-500"
                : staged === "created"
                    ? `text-${themeColor}-500`
                    : "text-blue-500";

    const textColor =
        staged === "deleted"
            ? "line-through text-red-700 dark:text-red-400"
            : staged === "moved"
                ? "text-amber-700 dark:text-amber-400"
                : (staged === "created" || staged === "edited")
                    ? `text-${themeColor}-700 dark:text-${themeColor}-400`
                    : "";

        return (
        <ItemActionsMenu 
            item={{ id, type: "directory", data: directory, staged, isExternal }}
            itemPath={buildPath()}
        >
            <BrowseLink
                href={buildPath()}
                onClick={handleCardClick}
                onPointerEnter={handlePointerEnter}
                onPointerLeave={handlePointerLeave}
                data-nav-index={navIndex}
                style={{ contentVisibility: "auto", containIntrinsicSize: "0 68px" }}
                className={`flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50 cursor-pointer ${stagedBorder} ${selectMode && selected ? "bg-primary/5 dark:bg-primary/10" : ""} ${focused ? "bg-muted ring-2 ring-inset ring-primary/40" : ""}`}
            >
                {selectMode && (
                    <Checkbox
                        checked={!!selected}
                        onCheckedChange={() => {}} // Handled by onClick below
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onToggleSelect?.(navIndex ?? 0, e);
                        }}
                        className="shrink-0"
                    />
                )}
                <Folder className={`h-6 w-6 shrink-0 ${iconColor}`} />

                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <span className={`block truncate font-medium ${textColor}`}>
                            {name}
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
                    </div>
                    <span className={`text-sm ${staged ? `text-${themeColor}-600/70` : "text-muted-foreground"}`}>
                        {t("itemsCount", { count: totalCount })}
                    </span>
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

export const DirectoryLineItem = memo(DirectoryLineItemImpl);
