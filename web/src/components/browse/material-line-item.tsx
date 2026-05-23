"use client";

import { memo, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";
import {
    Eye,
    File,
    FileText,
    Info,
    ListChecks,
    MessageSquare,
    Paperclip,
    ThumbsUp,
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useUIStore } from "@/lib/stores";
import { EXT_BADGE_COLORS, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { EXT_ICONS, TYPE_COLORS, TYPE_ICONS } from "@/lib/material-icons";
import { useTranslations } from "next-intl";


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
    onAddAttachment?: (id: string, title: string) => void;
    /** Cached attachment count for drafts */
    draftAttachmentCount?: number;
    /** Current pathname base (without trailing slash), hoisted from parent to avoid per-item usePathname subscription */
    pathBase: string;
    /** Hoisted from parent to avoid per-item useIsMobile subscription */
    isMobile: boolean;
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
    pathBase,
    isMobile,
}: MaterialLineItemProps) {
    const t = useTranslations("Browse");
    const tTypes = useTranslations("MaterialTypes");
    const openSidebar = useUIStore((s) => s.openSidebar);
    const router = useRouter();

    const title = String(material.title ?? "");
    const slug = String(material.slug ?? "");
    const id = String(material.id ?? "");
    const type = String(material.type ?? "other");
    const attachmentCount = draftAttachmentCount ?? Number(material.attachment_count ?? 0);
    const likeCount = Number(material.like_count ?? 0);
    const isLiked = Boolean(material.is_liked);

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

    let badgeColor = TYPE_COLORS[type] ?? TYPE_COLORS.other;

    let badgeLabel = tTypes.has(type as any) ? tTypes(type as any) : type;
    let Icon = TYPE_ICONS[type] ?? File;

    if (type === "document") {
        const fallbackLabel = getFileBadgeLabel(fileName, mimeType);
        if (fallbackLabel && fallbackLabel !== "FILE") {
            badgeLabel = fallbackLabel;
        }

        const ext = getFileExtension(fileName);
        if (ext && EXT_ICONS[ext]) {
            Icon = EXT_ICONS[ext];
        } else if (mimeType === "application/vnd.wikint.qcm+json") {
            Icon = ListChecks;
        } else if (mimeType && mimeType.includes("pdf")) {
            Icon = FileText;
        }

        // Try to get a meaningful color
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
            // Prefetch the Next.js RSC payload so navigation doesn't block on
            // deserializing the server component tree (250–440 ms frame gaps).
            router.prefetch(`${pathBase}/${slug}`);
        }, 100);
    };
    const handlePointerLeave = () => {
        if (prefetchTimer.current) clearTimeout(prefetchTimer.current);
    };

    const handleDetails = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        openSidebar("details", { type: "material", id, data: { ...material, __path: buildPath() } });
    };

    const handleChat = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        openSidebar("chat", { type: "material", id, data: material });
    };

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

    const iconColorClass = staged
        ? `text-${themeColor}-500`
        : badgeColor.split(" ").find(c => c.startsWith("text-")) || "text-muted-foreground";

    const textColor =
        staged === "deleted"
            ? "line-through text-red-700 dark:text-red-400"
            : staged === "moved"
                ? "text-amber-700 dark:text-amber-400"
                : (staged === "created" || staged === "edited")
                    ? `text-${themeColor}-700 dark:text-${themeColor}-400`
                    : "";

        const isRestricted = !!staged || !!previewPrId;

        return (
        <ItemActionsMenu
            item={{ id, type: "material", data: material, staged, isExternal }}
            onAddAttachment={onAddAttachment ? () => onAddAttachment(id, title) : undefined}
            itemPath={buildPath()}
        >
            <div
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
                        onCheckedChange={() => {}}
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggleSelect?.(navIndex ?? 0, e);
                        }}
                        className="shrink-0"
                    />
                )}
                <Icon className={`h-6 w-6 shrink-0 ${iconColorClass}`} />

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
                    <div className="flex items-center gap-1.5 mt-0.5">
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
                        {!isRestricted ? (
                            <>
                                <button
                                    onClick={handleChat}
                                    className="rounded-md p-2 hover:bg-muted active:scale-95 transition-transform"
                                    title={t("chat")}
                                    aria-label={t("chatAbout", { title })}
                                >
                                    <MessageSquare className={`${isMobile ? "h-5 w-5" : "h-4 w-4"} text-muted-foreground`} />
                                </button>
                            </>
                        ) : null}
                        <button
                            onClick={handleDetails}
                            className="rounded-md p-2 hover:bg-muted active:scale-95 transition-transform"
                            title={t("details")}
                            aria-label={t("viewDetailsFor", { title })}
                        >
                            <Info className={`${isMobile ? "h-5 w-5" : "h-4 w-4"} text-muted-foreground`} />
                        </button>
                    {staged === "created" && onAddAttachment && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                onAddAttachment(id, title);
                            }}
                            className="rounded-md p-2 hover:bg-violet-50 text-violet-600 dark:hover:bg-violet-950/40 dark:text-violet-400 active:scale-95 transition-transform"
                            title={t("addAttachment")}
                        >
                            <Paperclip className={`${isMobile ? "h-5 w-5" : "h-4 w-4"}`} />
                        </button>
                    )}
                    <ItemActionsDropdownTrigger />
                    <Link
                        href={buildPath()}
                        className="rounded-md p-2 hover:bg-muted active:scale-95 transition-transform"
                        title={isMobile ? t("view") || "View" : t("preview") || "Preview"}
                        onClick={(e) => {
                            if (onNavigate) {
                                e.preventDefault();
                                e.stopPropagation();
                                onNavigate();
                            } else {
                                e.stopPropagation();
                            }
                        }}
                        aria-label={t("viewOrPreviewFor", { title, action: isMobile ? (t("view") || "View") : (t("preview") || "Preview") })}
                    >
                        <Eye className={`${isMobile ? "h-5 w-5" : "h-4 w-4"} text-muted-foreground`} />
                    </Link>
                </div>
            </div>
        </ItemActionsMenu>
    );
}

export const MaterialLineItem = memo(MaterialLineItemImpl);
