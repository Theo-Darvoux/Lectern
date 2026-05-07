import React from "react";
import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";
import { MAX_FILE_SIZE_MB, ACCEPTED_FILE_TYPES } from "@/lib/file-utils";
import { useTranslations } from "next-intl";

interface DropZoneOverlayProps {
    isDragging: boolean;
    isEmpty: boolean;
    config: { allowed_extensions: string[]; max_file_size_mb: number } | null;
    fileInputRef: React.RefObject<HTMLInputElement | null>;
    reAttachFileInputRef: React.RefObject<HTMLInputElement | null>;
    dropzoneRef: React.RefObject<HTMLDivElement | null>;
    onAddFiles: (files: FileList) => void;
    onReAttach: (e: React.ChangeEvent<HTMLInputElement>) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
}

export function DropZoneOverlay({
    isDragging,
    isEmpty,
    config,
    fileInputRef,
    reAttachFileInputRef,
    dropzoneRef,
    onAddFiles,
    onReAttach,
    onDragOver,
    onDragLeave,
    onDrop,
}: DropZoneOverlayProps) {
    const t = useTranslations("Upload");

    return (
        <div
            ref={dropzoneRef}
            role="region"
            aria-label={t("dropzoneActive")}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    fileInputRef.current?.click();
                }
            }}
            tabIndex={0}
            className={cn(
                "cursor-pointer rounded-lg border-2 border-dashed transition-colors",
                isDragging
                    ? "border-primary bg-primary/5"
                    : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30",
                isEmpty
                    ? "flex flex-col items-center justify-center gap-2 p-8"
                    : "flex items-center gap-3 px-4 py-2.5",
            )}
        >
            <UploadCloud
                className={cn(
                    "pointer-events-none",
                    isDragging ? "text-primary" : "text-muted-foreground",
                    isEmpty ? "h-8 w-8" : "h-4 w-4 shrink-0",
                )}
            />
            {isEmpty ? (
                <div className="pointer-events-none flex flex-col items-center gap-2 text-center">
                    <p className="text-sm text-muted-foreground">
                        {isDragging ? t("dropzoneActive") : t("dropzoneDefault")}
                    </p>
                    <p className="text-xs text-muted-foreground/70">
                        {t("dropzoneHint", { limit: config?.max_file_size_mb || MAX_FILE_SIZE_MB })}
                    </p>
                </div>
            ) : (
                <p className="pointer-events-none text-xs text-muted-foreground">
                    {isDragging ? t("dropMoreActive") : t("dropMoreDefault")}
                </p>
            )}
            <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={config?.allowed_extensions.join(",") || ACCEPTED_FILE_TYPES}
                className="hidden"
                onChange={(e) => {
                    if (e.target.files) onAddFiles(e.target.files);
                    e.target.value = "";
                }}
            />
            <input
                ref={reAttachFileInputRef}
                type="file"
                className="hidden"
                onChange={onReAttach}
            />
        </div>
    );
}
