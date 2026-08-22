"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getMaterialThumbnail } from "@/lib/material-preview-source";
import { getFileTypeStyle } from "@/components/home/file-type-display";
import { getDirectoryIcon } from "@/lib/directory-icons";
import { getDirectoryColor } from "@/lib/directory-colors";
import { getFileBadgeLabel, getFileBadgeColor, getFileExtension } from "@/lib/file-utils";
import { isExternalUrl } from "@/lib/url-utils";
import { cn } from "@/lib/utils";
import { Folder, ExternalLink, Link2, File, Image as ImageIcon } from "lucide-react";

interface PROperationThumbnailProps {
    className?: string;
    size?: "sm" | "md" | "lg";
    fileName?: string | null;
    mimeType?: string | null;
    materialType?: string | null;
    materialId?: string | null;
    stagedFileKey?: string | null;
    targetUrl?: string | null;
    isDirectory?: boolean;
    directoryIcon?: string | null;
    directoryColor?: string | null;
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif"]);

export function PROperationThumbnail({
    className,
    size = "sm",
    fileName,
    mimeType,
    materialType,
    materialId,
    stagedFileKey,
    targetUrl,
    isDirectory,
    directoryIcon,
    directoryColor,
}: PROperationThumbnailProps) {
    const [imgUrl, setImgUrl] = useState<string | null>(null);
    const [imgError, setImgError] = useState(false);

    const ext = fileName ? getFileExtension(fileName) : "";
    const isImage = IMAGE_EXTENSIONS.has(ext) || (mimeType?.startsWith("image/") ?? false);
    const isLink = materialType === "link" || materialType === "internal_link" || Boolean(targetUrl);
    const isInternalLink = isLink && targetUrl && !isExternalUrl(targetUrl);

    useEffect(() => {
        let cancelled = false;
        setImgUrl(null);
        setImgError(false);

        if (isDirectory || isLink) return;

        // 1. If staged image file key
        if (stagedFileKey && isImage) {
            apiFetch<{ url: string }>(`/upload/preview?file_key=${encodeURIComponent(stagedFileKey)}`)
                .then((res) => {
                    if (!cancelled && res?.url) setImgUrl(res.url);
                })
                .catch(() => {
                    if (!cancelled) setImgError(true);
                });
            return () => { cancelled = true; };
        }

        // 2. If existing material with ID
        if (materialId && !materialId.startsWith("$")) {
            getMaterialThumbnail(materialId)
                .then((thumb) => {
                    if (!cancelled && thumb?.url) {
                        setImgUrl(thumb.url);
                    }
                })
                .catch(() => {
                    // Ignore thumbnail fetch errors; fallback icon will show
                });
            return () => { cancelled = true; };
        }
    }, [stagedFileKey, materialId, isImage, isDirectory, isLink]);

    // Directory Rendering
    if (isDirectory) {
        const { Icon: DirIcon } = getDirectoryIcon(directoryIcon ?? null);
        const { gradient, iconClass } = getDirectoryColor(directoryColor ?? null);

        const containerClasses = {
            sm: "h-9 w-9 rounded-md text-xs",
            md: "h-14 w-14 rounded-lg text-sm",
            lg: "h-24 w-28 rounded-xl text-base",
        }[size];

        const iconSizes = {
            sm: "h-4 w-4",
            md: "h-7 w-7",
            lg: "h-11 w-11",
        }[size];

        return (
            <div
                className={cn(
                    "flex shrink-0 items-center justify-center border bg-linear-to-br shadow-xs relative overflow-hidden",
                    gradient,
                    containerClasses,
                    className,
                )}
            >
                <DirIcon className={cn(iconSizes, iconClass, "transition-transform drop-shadow-xs")} />
            </div>
        );
    }

    // Link Rendering
    if (isLink) {
        const containerClasses = {
            sm: "h-9 w-9 rounded-md",
            md: "h-14 w-14 rounded-lg",
            lg: "h-24 w-28 rounded-xl",
        }[size];

        const iconSizes = {
            sm: "h-4 w-4",
            md: "h-7 w-7",
            lg: "h-11 w-11",
        }[size];

        return (
            <div
                className={cn(
                    "flex shrink-0 items-center justify-center border shadow-xs bg-linear-to-br from-sky-500 to-blue-600 text-white dark:from-sky-600 dark:to-blue-800",
                    containerClasses,
                    className,
                )}
            >
                {isInternalLink ? <Link2 className={iconSizes} /> : <ExternalLink className={iconSizes} />}
            </div>
        );
    }

    // Material / File Rendering
    const fileStyle = getFileTypeStyle(fileName ?? null, mimeType ?? null, materialType);
    const FileIcon = fileStyle.Icon || File;

    const containerClasses = {
        sm: "h-9 w-9 rounded-md",
        md: "h-14 w-14 rounded-lg",
        lg: "h-24 w-28 rounded-xl",
    }[size];

    const iconSizes = {
        sm: "h-4 w-4",
        md: "h-6 w-6",
        lg: "h-10 w-10",
    }[size];

    return (
        <div
            className={cn(
                "relative flex shrink-0 items-center justify-center border overflow-hidden shadow-xs bg-linear-to-br",
                fileStyle.gradient,
                containerClasses,
                className,
            )}
        >
            {imgUrl && !imgError ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                    src={imgUrl}
                    alt={fileName ?? "Thumbnail"}
                    className="absolute inset-0 h-full w-full object-cover animate-in fade-in duration-200"
                    onError={() => setImgError(true)}
                    loading="lazy"
                />
            ) : (
                <FileIcon className={cn(iconSizes, fileStyle.iconColorClass, "drop-shadow-xs opacity-90")} />
            )}

            {size === "lg" && fileName && (
                <span
                    className={cn(
                        "absolute bottom-1 right-1 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase shadow-xs",
                        getFileBadgeColor(fileName),
                    )}
                >
                    {getFileBadgeLabel(fileName, mimeType ?? undefined)}
                </span>
            )}
        </div>
    );
}
