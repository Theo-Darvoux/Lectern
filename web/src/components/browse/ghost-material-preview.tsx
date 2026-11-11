"use client";

import { useQuery } from "@tanstack/react-query";
import { getFileTypeStyle } from "@/components/home/file-type-display";
import { useInView } from "@/hooks/use-in-view";
import { uploadPreviewQuery } from "@/lib/upload-preview";
import { cn } from "@/lib/utils";

const IMAGE_NAME = /\.(jpg|jpeg|png|gif|webp|svg)$/i;

/** Preview of a staged material that exists only as an uploaded file. */
export function GhostMaterialPreview({
    fileKey,
    fileName,
    mimeType,
    containerRef,
    iconClassName,
}: {
    fileKey: string;
    fileName?: string | null;
    mimeType?: string | null;
    containerRef: React.RefObject<Element | null>;
    iconClassName: string;
}) {
    const { gradient, iconColorClass, Icon } = getFileTypeStyle(fileName ?? null, mimeType ?? null);
    const inView = useInView(containerRef);
    const isImage = (mimeType?.startsWith("image/") ?? false) || IMAGE_NAME.test(fileName ?? "");

    // Optional enhancement: the styled icon already stands in, so failures stay silent.
    const { data: imgUrl } = useQuery({
        ...uploadPreviewQuery(fileKey),
        enabled: inView && isImage && !!fileKey,
    });

    return (
        <div
            className={cn(
                "relative w-full h-full flex items-center justify-center overflow-hidden bg-linear-to-br",
                gradient,
            )}
        >
            <Icon className={cn(iconClassName, "z-10", iconColorClass, imgUrl ? "opacity-0" : "opacity-80")} />
            {imgUrl && (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                    src={imgUrl}
                    alt={fileName ?? ""}
                    className="absolute inset-0 h-full w-full object-cover animate-in fade-in duration-300"
                    loading="lazy"
                    decoding="async"
                />
            )}
        </div>
    );
}
