"use client";

import { useTranslations } from "next-intl";
import { useMaterialFile } from "@/hooks/use-material-file";
import { NotebookRenderer } from "./notebook-renderer";
import { ViewerShell } from "./viewer-shell";

interface NotebookViewerProps {
    fileKey: string;
    materialId: string;
}

export function NotebookViewer({ materialId, fileKey }: NotebookViewerProps) {
    const t = useTranslations("Viewers.notebook");
    const tViewers = useTranslations("Viewers");
    const { content, loading, error, truncated, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "text",
    });

    return (
        <ViewerShell
            loading={loading}
            // A cut notebook is not valid JSON: offer the download instead.
            error={error ?? (truncated ? tViewers("tooLargeToPreview") : null)}
            onRetry={truncated ? undefined : reload}
            downloadMaterialId={materialId}
            toolbarLeft={(
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
                    {t("title")}
                </span>
            )}
        >
            {!loading && !error && <NotebookRenderer content={content} />}
        </ViewerShell>
    );
}
