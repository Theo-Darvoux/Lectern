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
    const { content, loading, error, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "text",
    });

    return (
        <ViewerShell
            loading={loading}
            error={error}
            onRetry={reload}
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
