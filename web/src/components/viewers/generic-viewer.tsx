"use client";

import { FileText } from "lucide-react";
import { useDownload } from "@/hooks/use-download";
import { ViewerShell } from "./viewer-shell";
import { useTranslations } from "next-intl";
import { useConfigStore } from "@/lib/stores";

interface GenericViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

/**
 * Fallback viewer for file types that don't have a specialized renderer.
 * Offers a download button and displays the file name.
 */
export function GenericViewer({ materialId, fileName, fileKey }: GenericViewerProps) {
    const t = useTranslations("Preview");
    const config = useConfigStore((state) => state.config);
    const { downloadMaterial, isDownloading } = useDownload();

    return (
        <ViewerShell loading={false} error={null}>
            <div className="flex h-full flex-col items-center justify-center p-12 text-center">
                <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-muted shadow-sm">
                    <FileText className="h-10 w-10 text-muted-foreground" />
                </div>
                
                <h3 className="mb-2 text-xl font-semibold text-foreground">{fileName}</h3>
                <p className="mb-8 max-w-md text-sm text-muted-foreground">
                    {t("notSupported", { siteName: config?.site_name || "" })}
                </p>

                <button
                    onClick={() => downloadMaterial(materialId)}
                    disabled={isDownloading}
                    className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-semibold bg-primary text-primary-foreground hover:bg-primary/90 h-11 px-8 py-2 disabled:opacity-70 transition-all active:scale-95 shadow-lg shadow-primary/20"
                >
                    {isDownloading ? (
                        <div className="h-4 w-4 animate-spin rounded-full border-2 border-background border-t-transparent" />
                    ) : null}
                    {t("downloadFile")}
                </button>
            </div>
        </ViewerShell>
    );
}
