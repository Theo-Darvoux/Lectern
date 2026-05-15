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
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { sanitizeNameInput } from "@/lib/utils";
import { formatFileSize } from "@/lib/file-utils";
import { uploadFile, logicalFileSize } from "@/lib/upload-client";
import type { Operation } from "@/lib/staging-store";
import { useTranslations } from "next-intl";
import { Loader2, CheckCircle2, UploadCloud, X, FileIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface StagedItemEditDialogProps {
    /** The index in the staging operations array, or null if closed */
    index: number | null;
    onClose: () => void;
}

interface ReplacedFile {
    fileKey: string;
    fileName: string;
    fileSize: number;
    fileMimeType: string;
}

export function StagedItemEditDialog({ index, onClose }: StagedItemEditDialogProps) {
    const t = useTranslations("Staging");
    const tWizard = useTranslations("PRWizard");
    const tCommon = useTranslations("Common");
    const operations = useStagingStore((s) => s.operations);
    const updateOperation = useStagingStore((s) => s.updateOperation);

    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [tags, setTags] = useState<string[]>([]);

    // File replacement state
    const [replacedFile, setReplacedFile] = useState<ReplacedFile | null>(null);
    const [uploadState, setUploadState] = useState<"idle" | "uploading" | "done" | "error">("idle");
    const [uploadProgress, setUploadProgress] = useState(0);
    const [uploadFileName, setUploadFileName] = useState("");
    const [uploadError, setUploadError] = useState("");
    const abortRef = useRef<AbortController | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Safety check against out of bounds index
    const staged = index !== null ? operations[index] : null;
    const op = staged ? unwrapOp(staged) : null;

    const [prevOp, setPrevOp] = useState<Operation | null>(null);
    if (op !== prevOp) {
        setPrevOp(op);
        // Reset upload state whenever we switch to a different op
        abortRef.current?.abort();
        setReplacedFile(null);
        setUploadState("idle");
        setUploadProgress(0);
        setUploadFileName("");
        setUploadError("");
        if (op) {
            if (op.op === "create_material") {
                setTitle(op.title);
                setDescription(op.description || "");
                setTags(op.tags || []);
            } else if (op.op === "create_directory") {
                setTitle(op.name);
                setDescription(op.description || "");
                setTags(op.tags || []);
            } else if (op.op === "edit_material") {
                setTitle(op.title || "");
                setDescription(op.description || "");
                setTags(op.tags || []);
            } else if (op.op === "edit_directory") {
                setTitle(op.name || "");
                setDescription(op.description || "");
                setTags(op.tags || []);
            } else {
                setTitle("");
                setDescription("");
                setTags([]);
            }
        }
    }

    // Whether this op has a replaceable binary file (not a QCM draft handled by the QCM editor)
    const hasReplaceableFile =
        op?.op === "create_material" &&
        !!op.file_key &&
        op.file_mime_type !== "application/vnd.wikint.qcm+json";

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
                forcePipeline: true,
            });
            setUploadState("done");
            setReplacedFile({
                fileKey: result.file_key,
                fileName: result.correctedName ?? file.name,
                fileSize: logicalFileSize(result),
                fileMimeType: result.mime_type,
            });
        } catch (err) {
            if ((err as Error)?.message === "Upload cancelled") return;
            setUploadState("error");
            setUploadError((err as Error)?.message ?? "Upload failed");
        } finally {
            abortRef.current = null;
        }
    }, []);

    const resetUpload = useCallback(() => {
        abortRef.current?.abort();
        setReplacedFile(null);
        setUploadState("idle");
        setUploadProgress(0);
        setUploadFileName("");
        setUploadError("");
    }, []);

    const NAME_MAX = 128;

    const handleSave = () => {
        if (index === null || !op) return;

        const newOp = { ...op } as Record<string, unknown>;

        if (op.op === "create_material" || op.op === "edit_material") {
            newOp.title = title.trim() || undefined;
            newOp.description = description.trim() || null;
            newOp.tags = tags;
            if (op.op === "create_material" && !newOp.title) {
                newOp.title = t("untitled");
            }
            // Apply file replacement if a new file was uploaded
            if (replacedFile) {
                newOp.file_key = replacedFile.fileKey;
                newOp.file_name = replacedFile.fileName;
                newOp.file_size = replacedFile.fileSize;
                newOp.file_mime_type = replacedFile.fileMimeType;
            }
        } else if (op.op === "create_directory" || op.op === "edit_directory") {
            newOp.name = title.trim() || undefined;
            newOp.description = description.trim() || null;
            newOp.tags = tags;
            if (op.op === "create_directory" && !newOp.name) {
                newOp.name = t("newFolder");
            }
        }

        updateOperation(index, newOp as unknown as Operation);
        onClose();
    };

    const handleClose = () => {
        abortRef.current?.abort();
        onClose();
    };

    // If modal tries to open but we are tracking a non-editable op type, close it
    const isEditable = op?.op === "create_material" || op?.op === "edit_material" || op?.op === "create_directory" || op?.op === "edit_directory";
    const isSaveDisabled = uploadState === "uploading";

    return (
        <Dialog open={index !== null && isEditable} onOpenChange={(open) => !open && handleClose()}>
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
                            placeholder={tWizard("nameTitle")}
                            value={title}
                            onChange={(e) => setTitle(sanitizeNameInput(e.target.value))}
                            maxLength={NAME_MAX}
                        />
                        <p className={`text-[11px] text-right ${title.length >= NAME_MAX ? "text-destructive font-semibold" : "text-muted-foreground"}`}>
                            {title.length}/{NAME_MAX}
                        </p>
                    </div>

                    <div className="min-w-0 space-y-1.5">
                        <label className="text-sm font-medium">{tWizard("description")}</label>
                        <Textarea
                            placeholder={tWizard("description")}
                            className="resize-none"
                            rows={3}
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                        />
                    </div>

                    <div className="min-w-0 space-y-1.5">
                        <label className="text-sm font-medium">{tWizard("tags")}</label>
                        <TagInput
                            placeholder={tWizard("tagsPlaceholder")}
                            tags={tags}
                            onChange={(newTags) => setTags(newTags)}
                        />
                    </div>

                    {/* File replacement — only for binary create_material ops */}
                    {hasReplaceableFile && (
                        <div className="min-w-0 space-y-2">
                            <label className="text-sm font-medium">Fichier</label>

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
                                        Changer
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
                                        Réessayer
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
                    <Button variant="outline" onClick={handleClose} disabled={isSaveDisabled}>{tCommon("cancel")}</Button>
                    <Button onClick={handleSave} disabled={isSaveDisabled}>{tCommon("save")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
