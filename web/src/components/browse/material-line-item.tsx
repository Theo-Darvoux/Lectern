"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
    FileText, MessageSquare, Info, Eye, ThumbsUp,
    FileImage, FileVideo, FileAudio, FileArchive, FileSpreadsheet, FileCode, File, Lightbulb, ClipboardCheck, Video,
    Paperclip
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ItemActionsMenu, ItemActionsDropdownTrigger } from "./item-actions-menu";
import { useIsMobile } from "@/hooks/use-media-query";
import { useUIStore } from "@/lib/stores";
import { EXT_BADGE_COLORS, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { useTranslations } from "next-intl";

const TYPE_COLORS: Record<string, string> = {
    polycopie: "bg-blue-100 text-blue-800",
    annal: "bg-orange-100 text-orange-800",
    cheatsheet: "bg-green-100 text-green-800",
    tip: "bg-yellow-100 text-yellow-800",
    review: "bg-purple-100 text-purple-800",
    discussion: "bg-pink-100 text-pink-800",
    video: "bg-red-100 text-red-800",
    other: "bg-gray-100 text-gray-800",
};


export const TYPE_ICONS: Record<string, React.ElementType> = {
    polycopie: FileText,
    annal: FileText,
    cheatsheet: FileText,
    tip: Lightbulb,
    review: ClipboardCheck,
    discussion: MessageSquare,
    video: Video,
    other: File,
    document: FileText,
};

export const EXT_ICONS: Record<string, React.ElementType> = {
    pdf: FileText,
    doc: FileText,
    docx: FileText,
    txt: FileText,
    xls: FileSpreadsheet,
    xlsx: FileSpreadsheet,
    csv: FileSpreadsheet,
    png: FileImage,
    jpg: FileImage,
    jpeg: FileImage,
    gif: FileImage,
    webp: FileImage,
    svg: FileImage,
    mp4: FileVideo,
    avi: FileVideo,
    mkv: FileVideo,
    webm: FileVideo,
    mp3: FileAudio,
    wav: FileAudio,
    ogg: FileAudio,
    zip: FileArchive,
    rar: FileArchive,
    "7z": FileArchive,
    tar: FileArchive,
    gz: FileArchive,
    js: FileCode,
    ts: FileCode,
    py: FileCode,
    html: FileCode,
    css: FileCode,
    json: FileCode,
};


interface MaterialLineItemProps {
    material: Record<string, unknown>;
    staged?: "edited" | "deleted" | "moved" | "created" | null;
    isExternal?: boolean;
    selectMode?: boolean;
    selected?: boolean;
    onToggleSelect?: (e?: React.MouseEvent) => void;
    /** When set, appended as ?preview_pr= to preserve preview mode across navigation */
    previewPrId?: string;
    navIndex?: number;
    focused?: boolean;
    /** The index of the operation in the PR payload, if this is an external preview edit */
    previewOpIndex?: number;
    /** Special override for clicking on ghost materials (creations) */
    onNavigate?: () => void;
    /** Request attachment upload for this material (draft only) */
    onAddAttachment?: () => void;
    /** Cached attachment count for drafts */
    draftAttachmentCount?: number;
}

export function MaterialLineItem({
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
}: MaterialLineItemProps) {
    const t = useTranslations("Browse");
    const tTypes = useTranslations("MaterialTypes");
    const isMobile = useIsMobile();
    const { openSidebar } = useUIStore();
    const pathname = usePathname();
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
        const base = pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
        const matPath = `${base}/${slug}`;
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
            onToggleSelect(e);
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
            onAddAttachment={onAddAttachment}
            itemPath={buildPath()}
        >
            <div
                onClick={handleCardClick}
                data-nav-index={navIndex}
                className={`flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50 cursor-pointer ${stagedBorder} ${selectMode && selected ? "bg-primary/5 dark:bg-primary/10" : ""} ${focused ? "bg-muted ring-2 ring-inset ring-primary/40" : ""}`}
            >
                {selectMode && (
                    <Checkbox
                        checked={!!selected}
                        onCheckedChange={() => {}}
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggleSelect?.(e);
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
                                onAddAttachment();
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
