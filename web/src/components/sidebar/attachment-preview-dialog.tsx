"use client";

import dynamic from "next/dynamic";
import { Download, X } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getViewerType, getFileBadgeColor, getFileBadgeLabel } from "@/lib/file-utils";
import { useDownload } from "@/hooks/use-download";
import { useTranslations } from "next-intl";

const PdfViewer = dynamic(
    () => import("@/components/viewers/pdf-viewer").then((m) => m.PdfViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const ImageViewer = dynamic(
    () => import("@/components/viewers/image-viewer").then((m) => m.ImageViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const VideoPlayer = dynamic(
    () => import("@/components/viewers/video-player").then((m) => m.VideoPlayer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const AudioPlayer = dynamic(
    () => import("@/components/viewers/audio-player").then((m) => m.AudioPlayer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const MarkdownViewer = dynamic(
    () => import("@/components/viewers/markdown-viewer").then((m) => m.MarkdownViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const CodeViewer = dynamic(
    () => import("@/components/viewers/code-viewer").then((m) => m.CodeViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const NotebookViewer = dynamic(
    () => import("@/components/viewers/notebook-viewer").then((m) => m.NotebookViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const CsvViewer = dynamic(
    () => import("@/components/viewers/csv-viewer").then((m) => m.CsvViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const OfficeViewer = dynamic(
    () => import("@/components/viewers/office-viewer").then((m) => m.OfficeViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const EpubViewer = dynamic(
    () => import("@/components/viewers/epub-viewer").then((m) => m.EpubViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const DjvuViewer = dynamic(
    () => import("@/components/viewers/djvu-viewer").then((m) => m.DjvuViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const SvgViewer = dynamic(
    () => import("@/components/viewers/svg-viewer").then((m) => m.SvgViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);
const GenericViewer = dynamic(
    () => import("@/components/viewers/generic-viewer").then((m) => m.GenericViewer),
    { loading: () => <Skeleton className="h-full w-full rounded-none" />, ssr: false },
);

export interface AttachmentPreviewDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    materialId: string;
    title: string;
    fileKey: string;
    fileName: string;
    mimeType: string;
    material?: Record<string, unknown>;
}

export function AttachmentPreviewDialog({
    open,
    onOpenChange,
    materialId,
    title,
    fileKey,
    fileName,
    mimeType,
    material,
}: AttachmentPreviewDialogProps) {
    const t = useTranslations("Sidebar");
    const tCommon = useTranslations("Common");
    const { downloadMaterial, isDownloading } = useDownload();
    const viewerType = getViewerType(mimeType, fileName);

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="max-w-5xl sm:max-w-5xl w-[90vw] h-[90vh] flex flex-col p-0 gap-0 overflow-hidden"
                showCloseButton={false}
            >
                <div className="flex items-center gap-2 px-4 py-3 border-b shrink-0">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                        <span
                            className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${getFileBadgeColor(fileName, mimeType)}`}
                        >
                            {getFileBadgeLabel(fileName, mimeType)}
                        </span>
                        <span className="font-medium text-sm truncate">{title}</span>
                    </div>
                    <Button
                        size="icon"
                        variant="ghost"
                        className="shrink-0 h-7 w-7"
                        onClick={() => downloadMaterial(materialId)}
                        disabled={isDownloading}
                        title={t("download")}
                    >
                        <Download className="h-4 w-4" />
                    </Button>
                    <Button
                        size="icon"
                        variant="ghost"
                        className="shrink-0 h-7 w-7"
                        onClick={() => onOpenChange(false)}
                        title={tCommon("close")}
                    >
                        <X className="h-4 w-4" />
                    </Button>
                </div>

                <div className="flex-1 min-h-0 overflow-hidden rounded-b-lg">
                    {viewerType === "pdf" && (
                        <PdfViewer fileKey={fileKey} materialId={materialId} annotations={[]} />
                    )}
                    {viewerType === "image" && (
                        <ImageViewer fileKey={fileKey} materialId={materialId} fileName={fileName} />
                    )}
                    {viewerType === "svg" && (
                        <SvgViewer fileKey={fileKey} materialId={materialId} fileName={fileName} />
                    )}
                    {viewerType === "video" && (
                        <VideoPlayer fileKey={fileKey} materialId={materialId} material={material ?? {}} />
                    )}
                    {viewerType === "audio" && (
                        <AudioPlayer fileKey={fileKey} materialId={materialId} />
                    )}
                    {viewerType === "markdown" && (
                        <MarkdownViewer fileKey={fileKey} materialId={materialId} material={material ?? {}} annotations={[]} />
                    )}
                    {viewerType === "code" && (
                        <CodeViewer fileKey={fileKey} materialId={materialId} fileName={fileName} />
                    )}
                    {viewerType === "notebook" && (
                        <NotebookViewer fileKey={fileKey} materialId={materialId} />
                    )}
                    {viewerType === "csv" && (
                        <CsvViewer fileKey={fileKey} materialId={materialId} fileName={fileName} />
                    )}
                    {viewerType === "office" && (
                        <OfficeViewer fileKey={fileKey} materialId={materialId} fileName={fileName} />
                    )}
                    {viewerType === "epub" && (
                        <EpubViewer fileKey={fileKey} materialId={materialId} />
                    )}
                    {viewerType === "djvu" && (
                        <DjvuViewer fileKey={fileKey} materialId={materialId} />
                    )}
                    {(viewerType === "generic" || viewerType === "qcm") && (
                        <GenericViewer fileName={fileName} materialId={materialId} fileKey={fileKey} />
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
