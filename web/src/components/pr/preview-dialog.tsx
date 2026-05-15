"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
    FileText,
    Image as ImageIcon,
    Video as VideoIcon,
    Music,
    Code2,
    Eye,
    Loader2,
    Download,
    ExternalLink,
    ListChecks,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getFileExtension } from "@/lib/file-utils";
import { useTranslations } from "next-intl";

const QCMViewerPreview = dynamic(
    () => import("@/components/viewers/qcm-viewer").then((m) => m.QCMViewer),
    {
        ssr: false,
        loading: () => (
            <div className="flex h-full items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        ),
    },
);

// Loaded client-only: pdfjs calls Promise.withResolvers() at module-eval time,
// which doesn't exist in the Node.js version used by Next.js SSR.
const PdfPreview = dynamic(
    () => import("./pdf-preview").then((m) => m.PdfPreview),
    {
        ssr: false,
        loading: () => (
            <div className="flex h-full items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        ),
    },
);

/* ── Viewer type resolution ────────────────────────────────────────────────── */

const MIME_TO_VIEWER: Record<string, string> = {
    "application/vnd.wikint.qcm+json": "qcm",
    "application/pdf": "pdf",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/svg+xml": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "video/ogg": "video",
    "audio/mpeg": "audio",
    "audio/wav": "audio",
    "audio/ogg": "audio",
    "audio/flac": "audio",
    "audio/aac": "audio",
    "audio/mp3": "audio",
    "text/csv": "csv",
    "application/csv": "csv",
};

const EXT_TO_VIEWER: Record<string, string> = {
    qcm: "qcm",
    pdf: "pdf",
    md: "markdown",
    markdown: "markdown",
    png: "image",
    jpg: "image",
    jpeg: "image",
    gif: "image",
    webp: "image",
    svg: "image",
    mp4: "video",
    webm: "video",
    mov: "video",
    mp3: "audio",
    wav: "audio",
    flac: "audio",
    m4a: "audio",
    aac: "audio",
    csv: "csv",
};

const TEXT_EXTS = new Set([
    "txt", "log", "ini", "cfg", "conf", "tex", "latex",
    "js", "ts", "jsx", "tsx", "py", "java", "c", "cpp", "h", "hpp",
    "rs", "go", "rb", "php", "cs", "swift", "kt", "scala",
    "html", "css", "scss", "json", "yaml", "yml", "toml", "xml",
    "sql", "sh", "bash", "zsh", "lua", "r",
]);

function getViewerType(mimeType: string, fileName: string): string {
    if (MIME_TO_VIEWER[mimeType]) return MIME_TO_VIEWER[mimeType];
    if (mimeType.startsWith("image/")) return "image";
    if (mimeType.startsWith("video/")) return "video";
    if (mimeType.startsWith("audio/")) return "audio";
    if (mimeType.startsWith("text/")) return "code";
    const ext = getFileExtension(fileName);
    if (EXT_TO_VIEWER[ext]) return EXT_TO_VIEWER[ext];
    if (TEXT_EXTS.has(ext)) return "code";
    return "generic";
}

/* ── Text preview (markdown / code / csv) ──────────────────────────────────── */

function TextPreview({ url, type }: { url: string; type: "markdown" | "code" | "csv" }) {
    const t = useTranslations("Preview");
    const [content, setContent] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    useEffect(() => {
        let cancelled = false;
        fetch(url)
            .then((r) => r.text())
            .then((text) => { if (!cancelled) setContent(text); })
            .catch(() => { if (!cancelled) setError(true); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [url]);

    if (loading) return (
        <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
    );
    if (error) return (
        <div className="flex h-full items-center justify-center text-sm text-destructive">
            {t("failedToLoad")}
        </div>
    );

    if (type === "markdown") {
        return (
            <div className="prose prose-sm dark:prose-invert max-w-none h-full overflow-y-auto px-8 py-6">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
        );
    }

    return (
        <pre className="h-full overflow-auto bg-muted/20 p-4 text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
            {content}
        </pre>
    );
}

/* ── Generic fallback ──────────────────────────────────────────────────────── */

function GenericFallback({ url, fileName, mimeType }: { url: string; fileName: string; mimeType: string }) {
    const t = useTranslations("Preview");
    return (
        <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
            <FileText className="h-14 w-14 text-muted-foreground/40" />
            <div>
                <p className="text-sm font-medium">{fileName}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                    {mimeType || t("unknownType")} {t("previewUnavailable")}
                </p>
            </div>
            <div className="flex gap-2">
                <Button asChild variant="outline" size="sm">
                    <a href={url} download={fileName}>
                        <Download className="mr-1.5 h-3.5 w-3.5" />
                        {t("download")}
                    </a>
                </Button>
                <Button asChild variant="outline" size="sm">
                    <a href={url} target="_blank" rel="noreferrer">
                        <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                        {t("open")}
                    </a>
                </Button>
            </div>
        </div>
    );
}

/* ── Main dialog ───────────────────────────────────────────────────────────── */

const VIEWER_ICONS: Record<string, React.ElementType> = {
    pdf: FileText,
    image: ImageIcon,
    video: VideoIcon,
    audio: Music,
    markdown: Code2,
    code: Code2,
    csv: Code2,
    qcm: ListChecks,
    generic: Eye,
};

const VIEWER_ICON_COLORS: Record<string, string> = {
    pdf: "text-red-500",
    image: "text-blue-500",
    video: "text-purple-500",
    audio: "text-pink-500",
    markdown: "text-green-600",
    code: "text-amber-500",
    csv: "text-teal-500",
    qcm: "text-violet-500",
    generic: "text-muted-foreground",
};

export function PreviewDialog({
    url,
    mimeType = "",
    fileName,
    onClose,
}: {
    url: string;
    mimeType?: string;
    fileName?: string;
    onClose: () => void;
}) {
    const t = useTranslations("Preview");
    const displayFileName = fileName || t("titleDefault");
    const viewerType = getViewerType(mimeType, displayFileName);
    const Icon = VIEWER_ICONS[viewerType] ?? Eye;
    const iconColor = VIEWER_ICON_COLORS[viewerType] ?? "";

    const isLarge = viewerType === "pdf" || viewerType === "code" || viewerType === "markdown" || viewerType === "csv" || viewerType === "qcm";

    return (
        <Dialog open onOpenChange={(open) => !open && onClose()}>
            <DialogContent
                className={`${isLarge ? "max-w-5xl h-[90vh]" : "max-w-3xl"} w-full p-0 overflow-hidden flex flex-col`}
            >
                <DialogHeader className="shrink-0 px-4 pt-4 pb-2">
                    <DialogTitle className="flex items-center gap-2 text-sm font-medium">
                        <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
                        <span className="truncate">{displayFileName}</span>
                    </DialogTitle>
                </DialogHeader>

                <div className={`flex-1 min-h-0 ${!isLarge ? "px-4 pb-4" : "overflow-hidden"}`}>
                    {viewerType === "pdf" && <PdfPreview key={url} url={url} />}

                    {viewerType === "image" && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                            src={url}
                            alt={displayFileName}
                            className="max-h-[70vh] w-full rounded-lg object-contain bg-muted/10"
                        />
                    )}

                    {viewerType === "video" && (
                        <video
                            src={url}
                            controls
                            className="w-full max-h-[70vh] rounded-lg bg-black"
                        />
                    )}

                    {viewerType === "audio" && (
                        <div className="flex items-center justify-center py-12">
                            <audio src={url} controls className="w-full" />
                        </div>
                    )}

                    {(viewerType === "markdown" || viewerType === "code" || viewerType === "csv") && (
                        <TextPreview key={url} url={url} type={viewerType} />
                    )}

                    {viewerType === "qcm" && (
                        <QCMViewerPreview directUrl={url} />
                    )}

                    {viewerType === "generic" && (
                        <GenericFallback url={url} fileName={displayFileName} mimeType={mimeType} />
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
