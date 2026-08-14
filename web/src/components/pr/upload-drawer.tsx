"use client";

import { useRef, useState } from "react";
import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
    SheetDescription,
    SheetFooter,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AlertCircle, Check, CheckCircle2, ChevronDown, Loader2, PackagePlus, Settings2, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ScannedFile } from "@/lib/drop-utils";
import { TagInput } from "@/components/ui/tag-input";

import { useUploadEngine, fileSize } from "@/hooks/use-upload-engine";
import { DropZoneOverlay } from "./drop-zone-overlay";
import { PendingFolders } from "./zip-progress";
import { UploadQueueItem } from "./upload-queue-item";
import { MimeSelectDialog } from "./mime-select-dialog";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { getUploadFlowState } from "@/lib/upload-flow";

interface UploadDrawerProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    directoryId: string | null;
    directoryName?: string;
    parentMaterialId?: string | null;
    initialFiles?: File[] | ScannedFile[];
    initialFolderEntries?: Array<{ entry: FileSystemDirectoryEntry; name: string }>;
}

export function UploadDrawer({
    open,
    onOpenChange,
    directoryId,
    directoryName,
    parentMaterialId,
    initialFiles,
    initialFolderEntries,
}: UploadDrawerProps) {
    const t = useTranslations("Upload");

    const engine = useUploadEngine({
        open,
        onOpenChange,
        directoryId,
        parentMaterialId,
        initialFiles,
        initialFolderEntries,
    });

    const [foldersExpanded, setFoldersExpanded] = useState(true);

    const fileInputRef = useRef<HTMLInputElement>(null);
    const reAttachFileInputRef = useRef<HTMLInputElement>(null);
    const dropzoneRef = useRef<HTMLDivElement>(null);
    const overallProgress = engine.files.length > 0
        ? Math.round(engine.files.reduce((sum, file) => sum + file.progress, 0) / engine.files.length)
        : 0;
    const flow = getUploadFlowState({
        fileCount: engine.files.length,
        unsettledCount: engine.inFlightFiles.length,
        errorCount: engine.errorFiles.length,
        readyCount: engine.doneFiles.length,
    });
    const steps = [t("steps.choose"), t("steps.process"), t("steps.review")];

    return (
    <>
        <Sheet open={open} onOpenChange={engine.handleClose}>
            <SheetContent
                side="right"
                className="flex w-full flex-col overflow-hidden sm:max-w-lg"
                onInteractOutside={(e) => {
                    if (engine.inFlightFiles.length > 0) {
                        e.preventDefault();
                        // eslint-disable-next-line @typescript-eslint/no-require-imports
                        const { toast } = require("sonner");
                        toast.warning(t("uploadsInProgress"));
                    }
                }}
                onEscapeKeyDown={(e) => {
                    if (engine.inFlightFiles.length > 0) {
                        e.preventDefault();
                        // eslint-disable-next-line @typescript-eslint/no-require-imports
                        const { toast } = require("sonner");
                        toast.warning(t("uploadsInProgress"));
                    }
                }}
            >
                <SheetHeader>
                    <SheetTitle>{parentMaterialId ? t("titleAttachments") : t("titleFiles")}</SheetTitle>
                    <SheetDescription>
                        {parentMaterialId ? (
                            t("descAttachments", { name: directoryName || t("untitled") })
                        ) : (
                            t("descFiles", { name: directoryName || t("untitled") })
                        )}
                    </SheetDescription>
                </SheetHeader>

                <div className="grid grid-cols-3 gap-2 py-4" aria-label={t("steps.label")}>
                    {steps.map((label, index) => {
                        const step = (index + 1) as 1 | 2 | 3;
                        const complete = flow.step > step;
                        const active = flow.step === step;
                        return (
                            <div key={label} className="flex min-w-0 flex-col items-center gap-1.5 text-center">
                                <span
                                    className={cn(
                                        "flex h-7 w-7 items-center justify-center rounded-full border text-xs font-semibold",
                                        complete && "border-primary bg-primary text-primary-foreground",
                                        active && "border-primary bg-primary/10 text-primary ring-2 ring-primary/15",
                                        !complete && !active && "border-border bg-muted text-muted-foreground",
                                    )}
                                    aria-current={active ? "step" : undefined}
                                >
                                    {complete ? <Check className="h-3.5 w-3.5" /> : step}
                                </span>
                                <span className={cn("truncate text-[11px]", active ? "font-medium text-foreground" : "text-muted-foreground")}>
                                    {label}
                                </span>
                            </div>
                        );
                    })}
                </div>

                <DropZoneOverlay
                    isDragging={engine.isDragging}
                    isEmpty={engine.files.length === 0}
                    config={engine.config}
                    fileInputRef={fileInputRef}
                    reAttachFileInputRef={reAttachFileInputRef}
                    dropzoneRef={dropzoneRef}
                    onAddFiles={engine.addFlatFiles}
                    onReAttach={engine.handleReAttach}
                    onDragOver={engine.handleDragOver}
                    onDragLeave={engine.handleDragLeave}
                    onDrop={engine.handleDrop}
                />

                {engine.files.length > 0 && (
                    <details className="group mt-3 rounded-lg border bg-muted/10">
                        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                            <Settings2 className="h-4 w-4 text-muted-foreground" />
                            <span>{t("optionalSettings")}</span>
                            {engine.batchTags.length > 0 && (
                                <span className="ml-auto rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                                    {t("tagCount", { count: engine.batchTags.length })}
                                </span>
                            )}
                            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                        </summary>
                        <div className="space-y-1.5 border-t px-3 py-3">
                            <label className="text-sm font-medium">{t("batchTags")}</label>
                            <TagInput
                                tags={engine.batchTags}
                                onChange={engine.setBatchTags}
                                placeholder={t("batchTagsPlaceholder")}
                            />
                            <p className="text-[10px] text-muted-foreground">{t("batchTagsHint")}</p>
                        </div>
                    </details>
                )}

                <PendingFolders
                    pendingDirPaths={engine.pendingDirPaths}
                    foldersExpanded={foldersExpanded}
                    setFoldersExpanded={setFoldersExpanded}
                    editingPath={engine.editingPath}
                    editValue={engine.editValue}
                    setEditingPath={engine.setEditingPath}
                    setEditValue={engine.setEditValue}
                    commitRename={engine.commitRename}
                />

                {engine.files.length > 0 && (
                    <div className="mt-4 rounded-lg border bg-muted/20 p-3" aria-live="polite">
                        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                            <span className="inline-flex items-center gap-1.5 font-medium">
                                {engine.inFlightCount > 0 ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                                ) : (
                                    <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                                )}
                                {t("readyCount", { ready: engine.doneFiles.length, total: engine.files.length })}
                            </span>
                            {engine.errorFiles.length > 0 && (
                                <button
                                    type="button"
                                    className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    onClick={() => engine.errorFiles.forEach((file) => engine.removeFile(file.clientId))}
                                >
                                    <AlertCircle className="h-3.5 w-3.5" />
                                    {t("failedCount", { count: engine.errorFiles.length })}
                                    <Trash2 className="ml-1 h-3 w-3" />
                                    <span className="sr-only">{t("removeFailed")}</span>
                                </button>
                            )}
                            <span className="ml-auto tabular-nums text-muted-foreground">{overallProgress}%</span>
                        </div>
                        <Progress value={overallProgress} className="h-1.5" />
                    </div>
                )}

                {engine.files.length > 0 && (
                    <ScrollArea className="-mx-6 min-h-0 flex-1 px-6 mt-4">
                        <div className="space-y-2 py-1">
                            {engine.files.map((f) => {
                                const previewUrl = engine.previewUrlsRef.current.get(f.clientId);
                                const hasFileObject = engine.fileObjectsRef.current.has(f.clientId);
                                const eta = engine.etaMap.get(f.clientId);
                                const fileSizeStr = f.serverSize != null ? fileSize(f.serverSize, t) : fileSize(f.fileSize, t);
                                const hasTusHandle = f.status === "uploading" && !!f.tusUrl; 

                                return (
                                    <UploadQueueItem
                                        key={f.clientId}
                                        f={f}
                                        previewUrl={previewUrl}
                                        hasFileObject={hasFileObject}
                                        eta={eta}
                                        fileSizeStr={fileSizeStr}
                                        hasTusHandle={hasTusHandle}
                                        onUpdateTitle={engine.updateTitleField}
                                        onReAttach={(cid) => {
                                            engine.setReAttachingClientId(cid);
                                            reAttachFileInputRef.current?.click();
                                        }}
                                        onPause={engine.pauseUpload}
                                        onResume={engine.resumeUpload}
                                        onRetry={engine.retryFile}
                                        onRemove={engine.removeFile}
                                    />
                                );
                            })}
                        </div>
                    </ScrollArea>
                )}

                <SheetFooter className="-mx-6 mt-auto flex-col gap-2 border-t bg-background px-6 pt-4 sm:flex-col">
                    {engine.files.length > 0 && !engine.canStage && (
                        <p className="text-center text-xs text-muted-foreground">
                            {engine.inFlightFiles.length > 0 ? t("finishBeforeDraft") : t("noReadyFiles")}
                        </p>
                    )}
                    <Button
                        onClick={engine.handleStage}
                        disabled={!engine.canStage}
                        className="w-full gap-2"
                    >
                        <PackagePlus className="h-4 w-4" />
                        {engine.doneFiles.length === engine.files.length
                            ? t("addToDraft", { count: engine.doneFiles.length })
                            : t("addToDraftProgress", { count: engine.doneFiles.length, total: engine.files.length })}
                    </Button>
                </SheetFooter>
            </SheetContent>
        </Sheet>

        {engine.pendingMimeFiles.length > 0 && (
            <MimeSelectDialog
                files={engine.pendingMimeFiles}
                onConfirm={engine.handleMimeConfirm}
                onDismiss={engine.dismissPendingMime}
            />
        )}
    </>
    );
}
