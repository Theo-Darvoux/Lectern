import React from "react";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import {
    CheckCircle2,
    ShieldX,
    AlertCircle,
    Loader2,
    FileText,
    ImageIcon,
    Folder,
    PackagePlus,
    Pause,
    Play,
    RotateCcw,
    X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import type { QueueItem } from "@/lib/upload-queue";

interface UploadQueueItemProps {
    f: QueueItem;
    previewUrl?: string;
    hasFileObject: boolean;
    eta?: { bps: number; etaSec: number };
    fileSizeStr: string;
    hasTusHandle: boolean;
    onUpdateTitle: (clientId: string, title: string) => void;
    onReAttach: (clientId: string) => void;
    onPause: (clientId: string) => void;
    onResume: (clientId: string) => void;
    onRetry: (clientId: string) => void;
    onRemove: (clientId: string) => void;
}

export function UploadQueueItem({
    f,
    previewUrl,
    hasFileObject,
    eta,
    fileSizeStr,
    hasTusHandle,
    onUpdateTitle,
    onReAttach,
    onPause,
    onResume,
    onRetry,
    onRemove,
}: UploadQueueItemProps) {
    const t = useTranslations("Upload");

    return (
        <div
            className={cn(
                "group flex items-start gap-3 rounded-lg border p-3",
                f.status === "virus" &&
                    "border-destructive bg-destructive/5 dark:bg-destructive/10 animate-[virus-pulse-border_2s_ease-in-out_3]",
            )}
        >
            <div className="flex flex-col items-center gap-1.5 shrink-0 mt-0.5">
                <div className="h-4 w-4">
                    {f.status === "done" && <CheckCircle2 className="h-4 w-4 text-green-500" />}
                    {f.status === "virus" && (
                        <ShieldX className="h-4 w-4 text-destructive animate-[virus-shake_0.6s_ease-in-out_3]" />
                    )}
                    {f.status === "error" && <AlertCircle className="h-4 w-4 text-destructive" />}
                    {(f.status === "uploading" || f.status === "pending") && (
                        <Loader2 className="h-4 w-4 animate-spin text-primary" />
                    )}
                </div>

                <div className="h-9 w-9 overflow-hidden rounded border bg-muted/50 flex items-center justify-center">
                    {previewUrl ? (
                        f.fileMimeType === "application/pdf" ? (
                            <div className="flex flex-col items-center gap-0.5">
                                <FileText className="h-4 w-4 text-red-500" />
                                <span className="text-[8px] font-bold uppercase text-red-500">PDF</span>
                            </div>
                        ) : (
                            <img
                                src={previewUrl}
                                alt={t("previewAlt")}
                                className="h-full w-full object-cover"
                            />
                        )
                    ) : (
                        <div className="flex flex-col items-center gap-0.5">
                            {f.fileMimeType.startsWith("image/") ? (
                                <ImageIcon className="h-4 w-4 text-muted-foreground/60" />
                            ) : (
                                <FileText className="h-4 w-4 text-muted-foreground/60" />
                            )}
                            <span className="text-[7px] font-medium uppercase text-muted-foreground/60">
                                {f.fileName.split(".").pop()?.slice(0, 3)}
                            </span>
                        </div>
                    )}
                </div>
            </div>

            <div className="min-w-0 flex-1 space-y-1.5">
                <Input
                    value={f.title}
                    onChange={(e) => onUpdateTitle(f.clientId, e.target.value)}
                    className="h-7 text-sm font-medium"
                    placeholder={t("titlePlaceholder")}
                />
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="shrink-0">{fileSizeStr}</span>
                    {f.wasCompressed && (
                        <span className="shrink-0 rounded bg-blue-100 px-1 py-0.5 text-[9px] font-medium text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                            {t("compressed")}
                        </span>
                    )}
                </div>
                {!f.isFromBatchZip && !hasFileObject && (f.status === "pending" || f.status === "uploading" || f.status === "paused") && (
                    <p className="text-[10px] text-destructive font-medium">
                        {t("fileReferenceLost")}
                    </p>
                )}
                {f.targetDirPath && (
                    <div className="flex items-center gap-1 text-[10px] text-green-600 dark:text-green-400">
                        <Folder className="h-2.5 w-2.5 shrink-0" />
                        <span className="truncate">{f.targetDirPath}</span>
                    </div>
                )}
                {(f.status === "uploading" || f.status === "paused") && (
                    <div className="flex flex-col gap-1.5">
                        <div className="flex flex-col gap-0.5">
                            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                                <span>{t("uploading")}</span>
                                {f.status !== "paused" && (
                                    <span>
                                        {Math.min(Math.round(f.progress * 100 / 80), 100)}%
                                        {eta && f.progress < 80 && (
                                            <span className="ml-1">
                                                · {(eta.bps / (1024 * 1024)).toFixed(1)} {t("mbPerSec")} · ~{eta.etaSec}
                                                {t("secondsShort")}
                                            </span>
                                        )}
                                    </span>
                                )}
                            </div>
                            <Progress
                                value={f.progress < 80 ? Math.min(Math.round(f.progress * 100 / 80), 100) : 100}
                                className="h-1.5"
                            />
                        </div>

                        {f.status === "uploading" && f.progress >= 80 && (
                            <div className="flex flex-col gap-0.5">
                                <div className="flex items-center gap-1 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                                    <Loader2 className="h-2.5 w-2.5 animate-spin shrink-0" />
                                    <span className="truncate">{f.processingStatus || t("processing")}</span>
                                    {f.stageIndex != null && f.stageTotal != null && (
                                        <span className="ml-auto shrink-0 font-normal text-muted-foreground">
                                            {f.stageIndex + 1}/{f.stageTotal}
                                        </span>
                                    )}
                                </div>
                                <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-amber-100 dark:bg-amber-950/40">
                                    <div
                                        className="h-full bg-amber-500 dark:bg-amber-400 transition-all duration-500 animate-pulse"
                                        style={{ width: `${Math.min((f.progress - 80) * 5, 100)}%` }}
                                    />
                                </div>
                            </div>
                        )}

                        {f.status === "paused" && (
                            <p className="text-[10px] text-muted-foreground">{t("paused")}</p>
                        )}
                    </div>
                )}
                {f.status === "virus" && (
                    <div className="rounded-md bg-destructive/10 px-2 py-1.5">
                        <p className="text-xs font-semibold text-destructive">
                            {t("threatDetected")}
                        </p>
                        <p className="mt-0.5 text-[10px] text-destructive/80">
                            {t("threatDescription")}
                        </p>
                    </div>
                )}
                {f.status === "error" && f.error && (
                    <p className="text-xs text-destructive">
                        {f.error}
                    </p>
                )}
            </div>

            <div className="flex shrink-0 items-center gap-1">
                {!f.isFromBatchZip && !hasFileObject && (f.status === "pending" || f.status === "uploading" || f.status === "paused" || f.status === "error") && (
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-primary"
                        onClick={() => onReAttach(f.clientId)}
                        title={t("reAttach")}
                    >
                        <PackagePlus className="h-3.5 w-3.5" />
                    </Button>
                )}

                {f.status === "uploading" && hasTusHandle && (
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => onPause(f.clientId)}
                        title={t("pause")}
                    >
                        <Pause className="h-3.5 w-3.5" />
                    </Button>
                )}
                {f.status === "paused" && hasFileObject && (
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => onResume(f.clientId)}
                        title={t("resume")}
                    >
                        <Play className="h-3.5 w-3.5" />
                    </Button>
                )}
                {(f.status === "error" || f.status === "virus") && hasFileObject && (
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => onRetry(f.clientId)}
                        title={t("retry")}
                    >
                        <RotateCcw className="h-3.5 w-3.5" />
                    </Button>
                )}
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => onRemove(f.clientId)}
                    title={t("remove")}
                >
                    <X className="h-3.5 w-3.5" />
                </Button>
            </div>
        </div>
    );
}
