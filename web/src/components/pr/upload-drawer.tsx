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
import { PackagePlus } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ScannedFile } from "@/lib/drop-utils";
import { TagInput } from "@/components/ui/tag-input";

import { useUploadEngine, fileSize } from "@/hooks/use-upload-engine";
import { DropZoneOverlay } from "./drop-zone-overlay";
import { PendingFolders } from "./zip-progress";
import { UploadQueueItem } from "./upload-queue-item";
import { MimeSelectDialog } from "./mime-select-dialog";

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

                <div className="space-y-1.5 py-4">
                    <label className="text-sm font-medium">{t("batchTags")}</label>
                    <TagInput
                        tags={engine.batchTags}
                        onChange={engine.setBatchTags}
                        placeholder={t("batchTagsPlaceholder")}
                    />
                    <p className="text-[10px] text-muted-foreground">
                        {t("batchTagsHint")}
                    </p>
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

                <SheetFooter className="flex-col gap-2 sm:flex-col mt-4">
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
