"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowLeft, Loader2, AlertCircle, FileText, Image as ImageIcon, Video as VideoIcon, Music, Code2, Eye, Download, ExternalLink, ListChecks } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api-client";
import { getFileBadgeColor, getFileBadgeLabel } from "@/lib/file-utils";
import { MarkdownRenderer } from "@/components/viewers/markdown-renderer";
import { NotebookRenderer } from "@/components/viewers/notebook-renderer";
import { getContributionPreviewViewerType } from "./preview-viewer";
import { useTranslations } from "next-intl";

const PdfPreview = dynamic(
    () => import("@/components/pr/pdf-preview").then((m) => m.PdfPreview),
    {
        ssr: false,
        loading: () => (
            <div className="flex h-full items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        ),
    },
);

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

function TextPreview({ url, type }: { url: string; type: "markdown" | "code" | "csv" | "notebook" }) {
    const t = useTranslations("Preview");
    const [content, setContent] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    useEffect(() => {
        let cancelled = false;
        fetch(url)
            .then((r) => r.text())
            .then((t) => { if (!cancelled) setContent(t); })
            .catch(() => { if (!cancelled) setError(true); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [url]);

    if (loading) return <div className="flex h-full items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
    if (error) return <div className="flex h-full items-center justify-center text-sm text-destructive">{t("failedToLoad")}</div>;

    if (type === "markdown") {
        return (
            <div className="flex-1 overflow-auto prose prose-sm max-w-none p-8 dark:prose-invert
                prose-img:rounded-lg prose-img:shadow-sm
                prose-a:text-primary prose-a:no-underline hover:prose-a:underline
                prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-pre:text-foreground
                prose-code:before:content-none prose-code:after:content-none prose-code:text-foreground
                prose-table:border-collapse
                prose-th:border prose-th:border-border prose-th:px-3 prose-th:py-2
                prose-td:border prose-td:border-border prose-td:px-3 prose-td:py-2
                [&_mark]:bg-yellow-200 [&_mark]:text-yellow-900 dark:[&_mark]:bg-yellow-500/20 dark:[&_mark]:text-yellow-200">
                <MarkdownRenderer content={content} />
            </div>
        );
    }
    if (type === "notebook") {
        return (
            <div className="h-full overflow-y-auto bg-zinc-200 dark:bg-zinc-800/50">
                <NotebookRenderer content={content} />
            </div>
        );
    }
    return (
        <pre className="h-full overflow-auto bg-muted/10 p-6 text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
            {content}
        </pre>
    );
}

function GenericFallback({ url, fileName, mimeType }: { url: string; fileName: string; mimeType: string }) {
    const t = useTranslations("Preview");
    return (
        <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <FileText className="h-16 w-16 text-muted-foreground/30" />
            <div>
                <p className="font-medium">{fileName}</p>
                <p className="text-sm text-muted-foreground mt-0.5">{mimeType || t("unknownType")} {t("previewUnavailable")}</p>
            </div>
            <div className="flex gap-2">
                <Button asChild variant="outline">
                    <a href={url} download={fileName}>
                        <Download className="mr-1.5 h-4 w-4" />
                        {t("download")}
                    </a>
                </Button>
                <Button asChild variant="outline">
                    <a href={url} target="_blank" rel="noreferrer">
                        <ExternalLink className="mr-1.5 h-4 w-4" />
                        {t("openInNewTab")}
                    </a>
                </Button>
            </div>
        </div>
    );
}

const VIEWER_ICONS: Record<string, React.ElementType> = {
    pdf: FileText, image: ImageIcon, video: VideoIcon, audio: Music,
    markdown: Code2, code: Code2, csv: Code2, notebook: Code2, qcm: ListChecks, generic: Eye,
};
const VIEWER_ICON_COLORS: Record<string, string> = {
    pdf: "text-red-500", image: "text-blue-500", video: "text-purple-500",
    audio: "text-pink-500", markdown: "text-green-600", code: "text-amber-500",
    csv: "text-teal-500", notebook: "text-orange-500", qcm: "text-violet-500", generic: "text-muted-foreground",
};

export function PRPreviewPageContent() {
    const t = useTranslations("Preview");
    const pathname = usePathname();
    // pathname: /pull-requests/{id}/preview/{opIndex}/
    const previewMatch = pathname.match(/^\/pull-requests\/([^/]+)\/preview\/([^/]+)/);
    const prId = previewMatch ? previewMatch[1] : "";
    const opIndex = previewMatch ? Number(previewMatch[2]) : 0;
    const router = useRouter();

    const [presignedUrl, setPresignedUrl] = useState<string | null>(null);
    const [fileName, setFileName] = useState<string>("");
    const [mimeType, setMimeType] = useState<string>("");
    const [prTitle, setPrTitle] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;

        async function load() {
            try {
                const [pr, preview] = await Promise.all([
                    apiFetch<{ title: string; payload: Record<string, unknown>[] }>(`/pull-requests/${prId}`),
                    apiFetch<{ url: string; file_name?: string; file_mime_type?: string }>(`/pull-requests/${prId}/preview?opIndex=${opIndex}`),
                ]);

                if (cancelled) return;

                const op = pr.payload?.[opIndex] ?? {};
                if (preview) {
                    setFileName(String(preview.file_name ?? op.file_name ?? "File"));
                    setMimeType(String(preview.file_mime_type ?? op.file_mime_type ?? ""));
                    setPresignedUrl(preview.url);
                }
                setPrTitle(pr.title);
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : t("failedToLoadPreview"));
            } finally {
                if (!cancelled) setLoading(false);
            }
        }

        load();
        return () => { cancelled = true; };
    }, [prId, opIndex]);

    const viewerType = presignedUrl ? getContributionPreviewViewerType(mimeType, fileName) : "generic";
    const Icon = VIEWER_ICONS[viewerType] ?? Eye;
    const iconColor = VIEWER_ICON_COLORS[viewerType] ?? "";

    return (
        <div className="flex h-[calc(100vh-3.5rem)] flex-col">
            <div className="flex shrink-0 items-center gap-3 border-b bg-background px-4 py-2.5">
                <Button
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => router.back()}
                    title={t("backToContribution")}
                >
                    <ArrowLeft className="h-4 w-4" />
                </Button>

                <div className="flex min-w-0 flex-1 items-center gap-2.5">
                    <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
                    <span className="truncate text-sm font-medium">{fileName || t("titleDefault")}</span>
                    {fileName && (
                        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${getFileBadgeColor(fileName)}`}>
                            {getFileBadgeLabel(fileName, mimeType)}
                        </span>
                    )}
                </div>

                <div className="flex shrink-0 items-center gap-2">
                    {prTitle && (
                        <Badge variant="secondary" className="hidden sm:flex gap-1 text-xs font-normal">
                            {t("contribution")} ·
                            <Link href={`/pull-requests/${prId}`} className="hover:underline truncate max-w-[160px]">
                                {prTitle}
                            </Link>
                        </Badge>
                    )}
                    <Badge variant="outline" className="text-xs text-amber-600 border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400">
                        {t("pending")}
                    </Badge>
                </div>
            </div>

            <div className="relative flex-1 min-h-0 overflow-hidden">
                {loading && (
                    <div className="flex h-full items-center justify-center">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                )}

                {!loading && error && (
                    <div className="flex h-full flex-col items-center justify-center gap-3 text-center p-8">
                        <AlertCircle className="h-10 w-10 text-destructive/50" />
                        <div>
                            <p className="font-medium text-destructive">{t("previewUnavailableTitle")}</p>
                            <p className="text-sm text-muted-foreground mt-1">{error}</p>
                        </div>
                        <Button variant="outline" onClick={() => router.back()}>
                            {t("backToContribution")}
                        </Button>
                    </div>
                )}

                {!loading && !error && presignedUrl && (
                    <>
                        {viewerType === "pdf" && (
                            <PdfPreview key={presignedUrl} url={presignedUrl} />
                        )}
                        {(viewerType === "image" || viewerType === "svg") && (
                            <div className="flex h-full items-center justify-center bg-muted/10 p-4">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                    src={presignedUrl}
                                    alt={fileName}
                                    className="max-h-full max-w-full rounded-lg object-contain"
                                />
                            </div>
                        )}
                        {viewerType === "video" && (
                            <div className="flex h-full items-center justify-center bg-black">
                                <video src={presignedUrl} controls className="max-h-full max-w-full" />
                            </div>
                        )}
                        {viewerType === "audio" && (
                            <div className="flex h-full items-center justify-center p-8">
                                <audio src={presignedUrl} controls className="w-full max-w-xl" />
                            </div>
                        )}
                        {(viewerType === "markdown" || viewerType === "code" || viewerType === "csv" || viewerType === "notebook") && (
                            <TextPreview key={presignedUrl} url={presignedUrl} type={viewerType} />
                        )}
                        {viewerType === "qcm" && (
                            <QCMViewerPreview directUrl={presignedUrl} />
                        )}
                        {viewerType === "generic" && (
                            <GenericFallback url={presignedUrl} fileName={fileName} mimeType={mimeType} />
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
