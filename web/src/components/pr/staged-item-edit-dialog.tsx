"use client";

import { useState, useRef, useCallback } from "react";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TagInput } from "@/components/ui/tag-input";
import { useStagingStore } from "@/lib/staging-store";
import { sanitizeNameInput } from "@/lib/utils";
import { formatFileSize, MIME_QCM } from "@/lib/file-utils";
import { uploadFile, logicalFileSize } from "@/lib/upload-client";
import type { Operation } from "@/lib/staging-store";
import { useTranslations } from "next-intl";
import { Loader2, CheckCircle2, UploadCloud, X, FileIcon, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { normalizeTargetUrl } from "@/lib/url-utils";
import { localizeUploadError } from "@/lib/upload-errors";

interface StagedItemEditDialogProps {
    /** The draft entry being edited, or null if closed. Remount per entry via `key`. */
    stagedId: string | null;
    onClose: () => void;
}

const NAME_MAX = 128;
const DESCRIPTION_MAX = 1000;

function initialName(op: Operation | null): string {
    if (op?.op === "create_material" || op?.op === "edit_material") return op.title ?? "";
    if (op?.op === "create_directory" || op?.op === "edit_directory") return op.name ?? "";
    return "";
}

function initialUrl(op: Operation | null): string {
    if (op?.op !== "create_material" && op?.op !== "edit_material") return "";
    return String(op.metadata?.url ?? op.metadata?.link ?? "");
}

interface ReplacedFile {
    fileKey: string;
    fileName: string;
    fileSize: number;
    fileMimeType: string;
}

export function StagedItemEditDialog({ stagedId, onClose }: StagedItemEditDialogProps) {
    const t = useTranslations("Staging");
    const tWizard = useTranslations("PRWizard");
    const tUpload = useTranslations("Upload");
    const tCommon = useTranslations("Common");
    const tLink = useTranslations("NewLink");
    const op = useStagingStore((s) => s.operations.find((e) => e.id === stagedId)?.operation ?? null);
    const updateOperation = useStagingStore((s) => s.updateOperation);

    const [title, setTitle] = useState(() => initialName(op));
    const [url, setUrl] = useState(() => initialUrl(op));
    const detailed = op?.op === "delete_material" || op?.op === "delete_directory" || op?.op === "move_item" ? null : op;
    const [description, setDescription] = useState(detailed?.description ?? "");
    const [tags, setTags] = useState<string[]>(detailed?.tags ?? []);

    // File replacement state
    const [replacedFile, setReplacedFile] = useState<ReplacedFile | null>(null);
    const [uploadState, setUploadState] = useState<"idle" | "uploading" | "done" | "error">("idle");
    const [uploadProgress, setUploadProgress] = useState(0);
    const [uploadFileName, setUploadFileName] = useState("");
    const [uploadError, setUploadError] = useState("");
    const abortRef = useRef<AbortController | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const isMaterialOp = op?.op === "create_material" || op?.op === "edit_material";
    const isDirectoryOp = op?.op === "create_directory" || op?.op === "edit_directory";
    const isCreate = op?.op === "create_material" || op?.op === "create_directory";
    const isLink = isMaterialOp && (op.type === "link" || !!op.metadata?.url);
    // An edit only carries what the user changed; empty fields keep the live value.
    const placeholderFor = (label: string) => (isCreate ? label : t("keepCurrent"));

    // Whether this op has a replaceable binary file (not a QCM draft handled by the QCM editor)
    const hasReplaceableFile =
        op?.op === "create_material" &&
        !!op.file_key &&
        op.file_mime_type !== MIME_QCM;

    const currentFileName = op?.op === "create_material" ? (op.file_name ?? "") : "";
    const currentFileSize = op?.op === "create_material" ? (op.file_size ?? 0) : 0;

    const handleFileSelected = useCallback(async (file: File) => {
        abortRef.current?.abort();
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        setUploadState("uploading");
        setUploadProgress(0);
        setUploadFileName(file.name);
        setUploadError("");
        setReplacedFile(null);
        try {
            const result = await uploadFile(file, {
                signal: ctrl.signal,
                onProgress: setUploadProgress,
            });
            setUploadState("done");
            setReplacedFile({
                fileKey: result.file_key,
                fileName: result.correctedName ?? file.name,
                fileSize: logicalFileSize(result),
                fileMimeType: result.mime_type,
            });
        } catch (err) {
            if (ctrl.signal.aborted) return;
            setUploadState("error");
            setUploadError(localizeUploadError(err, tUpload));
        } finally {
            abortRef.current = null;
        }
    }, [tUpload]);

    const resetUpload = useCallback(() => {
        abortRef.current?.abort();
        setReplacedFile(null);
        setUploadState("idle");
        setUploadProgress(0);
        setUploadFileName("");
        setUploadError("");
    }, []);

    const handleSave = () => {
        if (stagedId === null || !op || !detailed) return;

        const trimmedDescription = description.trim();
        const fallbackName = !isCreate ? undefined : isMaterialOp ? t("untitled") : t("newFolder");
        const newOp: Record<string, unknown> = {
            ...op,
            [isMaterialOp ? "title" : "name"]: title.trim() || fallbackName,
        };
        // Leave untouched fields out of an edit: sending them would overwrite the live values.
        if (isCreate) {
            newOp.description = trimmedDescription || null;
            newOp.tags = tags;
        } else {
            if (detailed.description != null || trimmedDescription) newOp.description = trimmedDescription;
            if (detailed.tags != null || tags.length > 0) newOp.tags = tags;
        }

        if (isLink) {
            newOp.metadata = { ...op.metadata, url: normalizeTargetUrl(url.trim()) };
        }
        if (replacedFile) {
            newOp.file_key = replacedFile.fileKey;
            newOp.file_name = replacedFile.fileName;
            newOp.file_size = replacedFile.fileSize;
            newOp.file_mime_type = replacedFile.fileMimeType;
        }

        updateOperation(stagedId, newOp as unknown as Operation);
        onClose();
    };

    const handleClose = () => {
        abortRef.current?.abort();
        onClose();
    };

    const isSaveDisabled = uploadState === "uploading" || (isLink && !url.trim());

    return (
        <Dialog open={detailed !== null} onOpenChange={(open) => !open && handleClose()}>
            <DialogContent className="sm:max-w-[425px] overflow-hidden">
                <DialogHeader>
                    <DialogTitle>{t("editItemTitle")}</DialogTitle>
                    <DialogDescription>
                        {t("editItemDescription")}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4 py-4 min-w-0">
                    <div className="min-w-0 space-y-1.5">
                        <label className="text-sm font-medium">{tWizard("nameTitle")}</label>
                        <Input
                            placeholder={placeholderFor(tWizard("nameTitle"))}
                            value={title}
                            onChange={(e) => setTitle(sanitizeNameInput(e.target.value))}
                            maxLength={NAME_MAX}
                        />
                        <p className={`text-[11px] text-right ${title.length >= NAME_MAX ? "text-destructive font-semibold" : "text-muted-foreground"}`}>
                            {title.length}/{NAME_MAX}
                        </p>
                    </div>

                    {isLink && (
                        <div className="min-w-0 space-y-1.5">
                            <label className="text-sm font-medium">{tLink("targetUrl")}</label>
                            <div className="relative">
                                <Input
                                    placeholder={tLink("urlPlaceholder")}
                                    value={url}
                                    onChange={(e) => setUrl(e.target.value)}
                                    className="font-mono text-xs pl-8"
                                />
                                <ExternalLink className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                            </div>
                        </div>
                    )}

                    <div className="min-w-0 space-y-1.5">
                        <label className="text-sm font-medium">{tWizard("description")}</label>
                        <Textarea
                            placeholder={placeholderFor(tWizard("description"))}
                            className="resize-none"
                            rows={3}
                            maxLength={DESCRIPTION_MAX}
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                        />
                    </div>

                    <div className="min-w-0 space-y-1.5">
                        <label className="text-sm font-medium">{tWizard("tags")}</label>
                        <TagInput
                            placeholder={placeholderFor(tWizard("tagsPlaceholder"))}
                            tags={tags}
                            onChange={(newTags) => setTags(newTags)}
                        />
                    </div>

                    {/* File replacement — only for binary create_material ops */}
                    {hasReplaceableFile && (
                        <div className="min-w-0 space-y-2">
                            <label className="text-sm font-medium">{t("file")}</label>

                            {/* Current file */}
                            {uploadState === "idle" && (
                                <div className="flex min-w-0 items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm">
                                    <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                                    <span className="min-w-0 flex-1 truncate text-muted-foreground">
                                        {currentFileName}
                                        {currentFileSize > 0 && (
                                            <span className="ml-1.5 text-xs">({formatFileSize(currentFileSize)})</span>
                                        )}
                                    </span>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        className="h-7 shrink-0 text-xs"
                                        onClick={() => fileInputRef.current?.click()}
                                    >
                                        <UploadCloud className="h-3 w-3 mr-1" />
                                        {t("changeFile")}
                                    </Button>
                                </div>
                            )}

                            {/* Upload in progress */}
                            {uploadState === "uploading" && (
                                <div className="min-w-0 space-y-1.5 rounded-md border bg-muted/20 px-3 py-2">
                                    <div className="flex min-w-0 items-center gap-2 text-sm">
                                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
                                        <span className="min-w-0 flex-1 truncate">{uploadFileName}</span>
                                        <span className="shrink-0 text-xs text-muted-foreground">{uploadProgress}%</span>
                                        <button
                                            onClick={resetUpload}
                                            className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                                            title={tCommon("cancel")}
                                        >
                                            <X className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                    <div className="h-1 overflow-hidden rounded-full bg-muted">
                                        <div
                                            className="h-full rounded-full bg-primary transition-all"
                                            style={{ width: `${uploadProgress}%` }}
                                        />
                                    </div>
                                </div>
                            )}

                            {/* Upload done */}
                            {uploadState === "done" && replacedFile && (
                                <div className="flex min-w-0 items-center gap-2 rounded-md border border-green-200 bg-green-50/50 px-3 py-2 text-sm dark:border-green-800/40 dark:bg-green-950/20">
                                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
                                    <span className="min-w-0 flex-1 truncate">
                                        {replacedFile.fileName}
                                        <span className="ml-1.5 text-xs text-muted-foreground">({formatFileSize(replacedFile.fileSize)})</span>
                                    </span>
                                    <button
                                        onClick={resetUpload}
                                        className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                                        title={tCommon("remove")}
                                    >
                                        <X className="h-3.5 w-3.5" />
                                    </button>
                                </div>
                            )}

                            {/* Upload error */}
                            {uploadState === "error" && (
                                <div className="flex min-w-0 items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                                    <span className="min-w-0 flex-1 truncate">{uploadError || "Upload failed"}</span>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 shrink-0 text-xs"
                                        onClick={resetUpload}
                                    >
                                        {t("retryUpload")}
                                    </Button>
                                </div>
                            )}

                            <input
                                ref={fileInputRef}
                                type="file"
                                className="hidden"
                                onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    if (f) handleFileSelected(f);
                                    e.target.value = "";
                                }}
                            />
                        </div>
                    )}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={handleClose}>{tCommon("cancel")}</Button>
                    <Button onClick={handleSave} disabled={isSaveDisabled}>{tCommon("save")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
