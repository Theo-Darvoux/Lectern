import { useCallback, useState, useEffect, useMemo, useRef } from "react";
import { toast } from "sonner";
import { stagedDirectoryIds, useStagingStore } from "@/lib/staging-store";
import type { CreateMaterialOp } from "@/lib/staging-store";
import { MAX_FILE_SIZE_MB, ACCEPTED_FILE_TYPES, guessFileMime } from "@/lib/file-utils";
import { beginUploadGroup, getUploadConfig, uploadLimitMbForMime, type UploadConfig } from "@/lib/upload-client";
import { collectDroppedItems, extractDirPaths, traverseFolder, type ScannedFile } from "@/lib/drop-utils";
import { compareNatural } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";
import { useTranslations } from "next-intl";
import { dirKey, titleFromFilename, useUploadQueue, type QueueItem } from "@/lib/upload-queue";
import { mergeUploadTelemetry, useUploadTelemetry } from "@/lib/upload-telemetry";
import { useDropZoneStore } from "@/lib/drop-zone-store";
import { prepareScannedFiles } from "@/lib/upload-preflight";
import {
    attachUploadFile,
    forgetUpload,
    hasUploadFile,
    pauseUpload,
    removeUpload,
    restartUpload,
    resumeProcessingAfterReload,
    scheduleUpload,
} from "@/lib/upload-runtime";
import { isStaffRole } from "@/lib/guest";

const maxFilesPerBatch_DEFAULT = 50;
const maxFilesPerBatch_PRIVILEGED_DEFAULT = 2_000;

const OFFLINE_UPLOAD_CONFIG: UploadConfig = {
    allowed_extensions: ACCEPTED_FILE_TYPES.split(","),
    allowed_mimetypes: [],
    max_file_size_mb: MAX_FILE_SIZE_MB,
    max_size_mb_by_mime: {},
    recommended_path: "tus",
    direct_threshold_mb: 0,
};

const loadUploadConfig = () => getUploadConfig().catch(() => OFFLINE_UPLOAD_CONFIG);

export function canStageSuccessfulUploads(doneCount: number, inFlightCount: number): boolean {
    return doneCount > 0 && inFlightCount === 0;
}

function isSameFile(item: QueueItem, file: File): boolean {
    return (
        item.fileSize === file.size &&
        (item.fileLastModified === undefined || item.fileLastModified === file.lastModified)
    );
}

export function isInterruptedQueueMatch(item: QueueItem, scanned: ScannedFile): boolean {
    const itemPath = item.targetDirPath ? `${item.targetDirPath}/${item.fileName}` : item.fileName;
    return (
        (item.status === "error" || item.status === "paused") &&
        item.referenceLost === true &&
        itemPath === scanned.relativePath &&
        isSameFile(item, scanned.file)
    );
}

export function fileSize(bytes: number, t: (key: string, values?: Record<string, string | number | Date>) => string): string {
    if (bytes < 1024) return t("units.b", { count: bytes });
    if (bytes < 1024 * 1024) return t("units.kb", { count: (bytes / 1024).toFixed(1) });
    if (bytes < 1024 * 1024 * 1024) return t("units.mb", { count: (bytes / (1024 * 1024)).toFixed(1) });
    return t("units.gb", { count: (bytes / (1024 * 1024 * 1024)).toFixed(2) });
}

function isUnsettled(item: QueueItem): boolean {
    return (item.status === "uploading" || item.status === "pending" || item.status === "paused") && !item.referenceLost;
}

function ancestorPaths(dirPath: string): string[] {
    const parts = dirPath.split("/").filter(Boolean);
    return parts.map((_, depth) => parts.slice(0, depth + 1).join("/"));
}

interface UseUploadEngineProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    directoryId: string | null;
    directoryName?: string;
    parentMaterialId?: string | null;
    initialFiles?: File[] | ScannedFile[];
    initialFolderEntries?: Array<{ entry: FileSystemDirectoryEntry; name: string }>;
}

export function useUploadEngine({
    open,
    onOpenChange,
    directoryId,
    directoryName,
    parentMaterialId,
    initialFiles,
    initialFolderEntries,
}: UseUploadEngineProps) {
    const t = useTranslations("Upload");
    const { user } = useAuth();
    const isPrivileged = isStaffRole(user?.role);

    const addOperations = useStagingStore((s) => s.addOperations);
    const nextTempId = useStagingStore((s) => s.nextTempId);
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
    const draftOperations = useStagingStore((s) => s.operations);

    const durableFiles = useUploadQueue((s) => s.items);
    const dirTempIds = useUploadQueue((s) => s.dirTempIds);
    const { addItems, updateItem, removeItem, setDirTempIds, renameDirectory } = useUploadQueue.getState();

    const telemetryById = useUploadTelemetry((state) => state.byId);
    const files = useMemo(
        () => mergeUploadTelemetry(durableFiles, telemetryById),
        [durableFiles, telemetryById],
    );

    const doneFiles = useMemo(() => files.filter((i) => i.status === "done"), [files]);
    const errorFiles = useMemo(() => files.filter((i) => i.status === "error" || i.status === "virus"), [files]);
    const inFlightFiles = useMemo(() => files.filter(isUnsettled), [files]);
    const inFlightCount = inFlightFiles.length;

    /** Folders that adding the current rows would create, by path. */
    const pendingDirPaths = useMemo(() => {
        const staged = stagedDirectoryIds(draftOperations);
        const paths = new Map<string, string>();
        for (const item of durableFiles) {
            for (const path of ancestorPaths(item.targetDirPath)) {
                const key = dirKey(item, path);
                if (!staged.has(dirTempIds[key])) paths.set(path, dirTempIds[key] ?? key);
            }
        }
        return paths;
    }, [durableFiles, dirTempIds, draftOperations]);

    const [pendingMimeFiles, setPendingMimeFiles] = useState<ScannedFile[]>([]);
    const [editingPath, setEditingPath] = useState<string | null>(null);
    const [editValue, setEditValue] = useState("");
    const [batchTags, setBatchTags] = useState<string[]>([]);
    const [keepOriginalImages, setKeepOriginalImages] = useState(false);
    const [reAttachingClientId, setReAttachingClientId] = useState<string | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const initialFilesProcessedRef = useRef(false);
    const [config, setConfig] = useState<UploadConfig | null>(null);

    const maxFilesPerBatch = isPrivileged
        ? config?.batch_max_files_privileged ?? maxFilesPerBatch_PRIVILEGED_DEFAULT
        : Math.min(config?.batch_max_files ?? maxFilesPerBatch_DEFAULT, maxFilesPerBatch_DEFAULT);

    useEffect(() => {
        void loadUploadConfig().then(setConfig);
        resumeProcessingAfterReload(t);
    }, [t]);

    const handleReAttach = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        // Selecting the same file again must fire another change event.
        e.target.value = "";
        const item = useUploadQueue.getState().items.find((i) => i.clientId === reAttachingClientId);
        setReAttachingClientId(null);
        if (!file || !item) return;

        if (file.name !== item.fileName || !isSameFile(item, file)) {
            toast.error(t("reAttachMismatch"));
            return;
        }

        attachUploadFile(item.clientId, file);
        restartUpload(item.clientId, t);
    }, [t, reAttachingClientId]);

    const commitRename = useCallback(
        (oldPath: string) => {
            const newLeaf = editValue.trim().replace(/\//g, "");
            setEditingPath(null);
            setEditValue("");
            if (!newLeaf || newLeaf === oldPath.split("/").pop()) return;
            renameDirectory(oldPath, [...oldPath.split("/").slice(0, -1), newLeaf].join("/"));
        },
        [editValue, renameDirectory],
    );

    const processScannedFiles = useCallback(
        async (scanned: ScannedFile[]): Promise<number> => {
            if (scanned.length === 0) return 0;
            const uploadConfig = await loadUploadConfig();

            // Resolve MIME types from extension for files the browser mis-labels as octet-stream
            const normalized = scanned.map(s => {
                const mime = guessFileMime(s.file);
                if (mime !== s.file.type) {
                    return { ...s, file: new File([s.file], s.file.name, { type: mime, lastModified: s.file.lastModified }) };
                }
                return s;
            });

            let valid = normalized.filter((s) => {
                const limitMb = uploadLimitMbForMime(uploadConfig, s.file.type, MAX_FILE_SIZE_MB);
                if (s.file.size <= limitMb * 1024 * 1024) return true;
                toast.error(t("fileExceedsLimit", { name: s.file.name, limit: limitMb }));
                return false;
            });

            // Mobile pickers and messaging apps often deliver files with a stripped or
            // decorated name and a generic MIME type. Sniff magic bytes to recover the
            // real type, and restore the canonical extension so valid files are not
            // rejected here or by server-side filename validation.
            const prepared = await prepareScannedFiles(valid, uploadConfig.allowed_extensions);
            prepared.unreadable.forEach((path) => {
                toast.error(`${path}: ${t("failedToReadDropped")}`);
            });

            const toProcess: ScannedFile[] = [];
            const needsMime: ScannedFile[] = [];
            for (const s of prepared.files) {
                const f = s.file;
                const ext = `.${f.name.split(".").pop()?.toLowerCase()}`;
                const isAllowedExt = uploadConfig.allowed_extensions.includes(ext);
                const isAllowedMime = f.type ? uploadConfig.allowed_mimetypes.includes(f.type) : false;
                const isOctetStream = !f.type || f.type === "application/octet-stream";

                if (isOctetStream && isAllowedExt) {
                    needsMime.push(s);
                } else if (!isAllowedExt && !isAllowedMime && !f.type.startsWith("text/")) {
                    toast.error(t("fileTypeNotSupported", { name: f.name, type: f.type || ext }));
                } else {
                    toProcess.push(s);
                }
            }
            if (needsMime.length > 0) setPendingMimeFiles((prev) => [...prev, ...needsMime]);
            valid = toProcess;

            const queueItems = useUploadQueue.getState().items;
            const recovered: Array<{ item: QueueItem; file: File }> = [];
            const newFiles: ScannedFile[] = [];
            for (const scannedFile of valid) {
                const interrupted = queueItems.find(
                    (item) =>
                        isInterruptedQueueMatch(item, scannedFile) &&
                        !hasUploadFile(item.clientId) &&
                        !recovered.some((entry) => entry.item.clientId === item.clientId),
                );
                if (interrupted) recovered.push({ item: interrupted, file: scannedFile.file });
                else newFiles.push(scannedFile);
            }

            const remaining = maxFilesPerBatch - queueItems.filter((item) => !item.folderName).length;
            if (newFiles.length > 0 && remaining <= 0) {
                toast.error(t("maxFilesPerBatch", { count: maxFilesPerBatch }));
                newFiles.length = 0;
            } else if (newFiles.length > remaining) {
                toast.warning(t("onlyAddingCapped", { count: remaining, limit: maxFilesPerBatch }));
                newFiles.length = remaining;
            }

            // One admission covers the whole selection, so choosing many files at
            // once is not throttled like many separate uploads. Without it the
            // files still upload, subject to the ordinary rate limit.
            const admittedCount = newFiles.length + recovered.length;
            const group = admittedCount > 1 ? await beginUploadGroup(admittedCount).catch(() => null) : null;

            const destination = { directoryId, directoryName, parentMaterialId };
            const newDirIds: Record<string, string> = {};
            const knownDirIds = useUploadQueue.getState().dirTempIds;
            for (const path of extractDirPaths(newFiles)) {
                const key = dirKey(destination, path);
                if (!knownDirIds[key]) newDirIds[key] = nextTempId("dir");
            }
            setDirTempIds(newDirIds);

            const newItems: QueueItem[] = newFiles.map(({ file, relativePath }) => {
                const clientId = crypto.randomUUID();
                attachUploadFile(clientId, file);
                return {
                    clientId,
                    uploadId: crypto.randomUUID(),
                    uploadGroupId: group?.group_id,
                    fileName: file.name,
                    fileSize: file.size,
                    fileLastModified: file.lastModified,
                    fileMimeType: file.type || "application/octet-stream",
                    title: titleFromFilename(file.name),
                    status: "pending",
                    progress: 0,
                    processingStatus: "",
                    targetDirPath: relativePath.split("/").slice(0, -1).join("/"),
                    skipCompression: keepOriginalImages,
                    ...destination,
                };
            });
            addItems(newItems);

            for (const { item, file } of recovered) {
                attachUploadFile(item.clientId, file);
                restartUpload(item.clientId, t, group?.group_id);
            }
            for (const item of newItems) scheduleUpload(item.clientId, t);
            return newItems.length + needsMime.length + recovered.length;
        },
        [addItems, setDirTempIds, nextTempId, maxFilesPerBatch, keepOriginalImages, directoryId, directoryName, parentMaterialId, t],
    );

    const handleMimeConfirm = useCallback(
        (selections: Array<{ scanned: ScannedFile; mime: string }>) => {
            setPendingMimeFiles([]);
            void processScannedFiles(selections.map(({ scanned, mime }) => ({
                ...scanned,
                file: new File([scanned.file], scanned.file.name, { type: mime, lastModified: scanned.file.lastModified }),
            })));
        },
        [processScannedFiles],
    );

    const dismissPendingMime = useCallback(() => setPendingMimeFiles([]), []);

    const processFolder = useCallback(
        async (entry: FileSystemDirectoryEntry, folderName: string) => {
            const placeholder: QueueItem = {
                clientId: crypto.randomUUID(),
                uploadId: crypto.randomUUID(),
                fileName: folderName,
                fileSize: 0,
                fileMimeType: "inode/directory",
                title: folderName,
                status: "uploading",
                progress: 0,
                processingStatus: "",
                targetDirPath: "",
                folderName,
            };
            const placeholderId = placeholder.clientId;
            const stillWanted = () => useUploadQueue.getState().items.some((i) => i.clientId === placeholderId);
            addItems([placeholder]);

            try {
                const scanResult = await traverseFolder(entry);
                if (!stillWanted()) return;

                if (scanResult.skipped.length > 0) {
                    toast.warning(`${folderName}: ${t("filesSkipped", {
                        count: scanResult.skipped.length,
                        errors: scanResult.skipped.slice(0, 2).join(", ") + (scanResult.skipped.length > 2 ? "…" : ""),
                    })}`);
                }
                if (scanResult.files.length === 0) {
                    updateItem(placeholderId, { status: "error", error: t("folderEmpty") });
                    return;
                }

                const queued = await processScannedFiles(scanResult.files);
                if (queued > 0) removeItem(placeholderId);
                else updateItem(placeholderId, { status: "error", error: t("noValidFiles") });
            } catch {
                if (stillWanted()) updateItem(placeholderId, { status: "error", error: t("folderUploadFailed") });
            }
        },
        [addItems, updateItem, removeItem, processScannedFiles, t],
    );

    const addFlatFiles = useCallback(
        (newFiles: FileList | File[] | ScannedFile[]) => {
            const filesArray = (Array.isArray(newFiles) ? newFiles : Array.from(newFiles)) as (File | ScannedFile)[];
            void processScannedFiles(filesArray.map((f) =>
                "relativePath" in f ? f : { file: f, relativePath: f.name },
            ));
        },
        [processScannedFiles],
    );

    useEffect(() => {
        const hasFiles = (initialFiles?.length ?? 0) > 0;
        const hasFolders = (initialFolderEntries?.length ?? 0) > 0;
        if (open && (hasFiles || hasFolders) && !initialFilesProcessedRef.current) {
            initialFilesProcessedRef.current = true;
            if (hasFiles) queueMicrotask(() => addFlatFiles(initialFiles!));
            if (hasFolders) {
                void (async () => {
                    for (const { entry, name } of initialFolderEntries!) {
                        await processFolder(entry, name);
                    }
                })();
            }
        }
        if (!open) {
            initialFilesProcessedRef.current = false;
        }
    }, [open, initialFiles, initialFolderEntries, addFlatFiles, processFolder]);

    const dismissOverlay = useDropZoneStore((s) => s.dismissOverlay);

    useEffect(() => {
        if (!open) return;
        const handlePaste = (e: ClipboardEvent) => {
            if (!e.clipboardData) return;
            const imageFiles = Array.from(e.clipboardData.items)
                .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
                .map((item) => item.getAsFile())
                .filter((file): file is File => file !== null);
            if (imageFiles.length > 0) addFlatFiles(imageFiles);
        };
        document.addEventListener("paste", handlePaste);
        return () => document.removeEventListener("paste", handlePaste);
    }, [open, addFlatFiles]);

    const processDropItems = useCallback(
        async (items: DataTransferItemList) => {
            let dropped: Awaited<ReturnType<typeof collectDroppedItems>>;
            try {
                dropped = await collectDroppedItems(items);
            } catch {
                toast.error(t("failedToReadDropped"));
                return;
            }
            if (dropped.inaccessible.length > 0) {
                toast.warning(t("foldersNotAccessible", { count: dropped.inaccessible.length }));
            }
            if (dropped.files.length > 0) void processScannedFiles(dropped.files);
            for (const { entry, name } of dropped.folders) {
                await processFolder(entry, name);
            }
        },
        [processScannedFiles, processFolder, t],
    );

    useEffect(() => {
        if (!open) return;

        const onDragOver = (e: DragEvent) => {
            if (!e.dataTransfer?.types.includes("Files")) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        };

        const onDrop = (e: DragEvent) => {
            e.preventDefault();
            e.stopPropagation();
            setIsDragging(false);
            dismissOverlay?.();
            if (!e.dataTransfer?.items.length) return;
            void processDropItems(e.dataTransfer.items);
        };

        document.addEventListener("dragover", onDragOver, true);
        document.addEventListener("drop", onDrop, true);

        return () => {
            document.removeEventListener("dragover", onDragOver, true);
            document.removeEventListener("drop", onDrop, true);
        };
    }, [open, processDropItems, dismissOverlay]);

    const retryFile = useCallback((clientId: string) => restartUpload(clientId, t), [t]);

    const resumeUpload = useCallback((clientId: string) => scheduleUpload(clientId, t), [t]);

    const updateTitleField = useCallback(
        (clientId: string, title: string) => updateItem(clientId, { title }),
        [updateItem],
    );

    const canStage = canStageSuccessfulUploads(doneFiles.length, inFlightCount);

    const handleStage = () => {
        if (!canStage) return;

        // A folder counts as staged only while the draft still creates it.
        const staged = stagedDirectoryIds(useStagingStore.getState().operations);
        const tempIds = { ...useUploadQueue.getState().dirTempIds };
        const dirIdFor = (item: QueueItem, path: string) => {
            const key = dirKey(item, path);
            tempIds[key] ??= nextTempId("dir");
            return tempIds[key];
        };
        const rootOf = (item: QueueItem) => (item.directoryId ?? directoryId) || null;

        // One folder per destination-scoped path, parents first.
        const dirsToCreate = new Map<string, { item: QueueItem; path: string }>();
        for (const item of doneFiles) {
            for (const path of ancestorPaths(item.targetDirPath)) {
                const key = dirKey(item, path);
                if (!staged.has(tempIds[key])) dirsToCreate.set(key, { item, path });
            }
        }
        const dirOps = [...dirsToCreate.values()]
            .sort((a, b) => a.path.split("/").length - b.path.split("/").length || compareNatural(a.path, b.path))
            .map(({ item, path }) => {
                const parts = path.split("/");
                const parentPath = parts.slice(0, -1).join("/");
                return {
                    op: "create_directory" as const,
                    temp_id: dirIdFor(item, path),
                    parent_id: parentPath ? dirIdFor(item, parentPath) : rootOf(item),
                    name: parts[parts.length - 1],
                    type: "folder" as const,
                    tags: batchTags.length > 0 ? batchTags : undefined,
                };
            });

        const matOps: CreateMaterialOp[] = doneFiles.map((f) => {
            const materialParent = f.parentMaterialId === undefined ? parentMaterialId : f.parentMaterialId;
            return {
                op: "create_material" as const,
                temp_id: nextTempId("mat"),
                directory_id: (f.targetDirPath ? dirIdFor(f, f.targetDirPath) : rootOf(f))!,
                title: f.title || titleFromFilename(f.correctedName ?? f.fileName),
                type: "document" as const,
                file_key: f.fileKey!,
                file_name: f.correctedName ?? f.fileName,
                file_size: f.serverSize ?? f.fileSize,
                file_mime_type: f.mimeType || f.fileMimeType || "application/octet-stream",
                ...(materialParent ? { parent_material_id: materialParent } : {}),
                tags: batchTags.length > 0 ? batchTags : undefined,
            };
        });

        addOperations([...dirOps, ...matOps]);
        setDirTempIds(tempIds);
        doneFiles.forEach((f) => forgetUpload(f.clientId));

        toast.success(t("addedToDraft", { count: dirOps.length + matOps.length }));

        if (errorFiles.length === 0) {
            onOpenChange(false);
            setReviewOpen(true);
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
    };

    const handleClose = (nextOpen: boolean) => {
        if (!nextOpen && inFlightCount > 0) toast.info(t("continuesInBackground"));
        onOpenChange(nextOpen);
    };

    return {
        files,
        doneFiles,
        errorFiles,
        inFlightCount,
        inFlightFiles,
        canStage,

        pendingDirPaths,
        pendingMimeFiles,
        editingPath,
        editValue,
        batchTags,
        keepOriginalImages,
        isDragging,
        reAttachingClientId,
        config,

        setEditingPath,
        setEditValue,
        setBatchTags,
        setKeepOriginalImages,
        setReAttachingClientId,

        handleReAttach,
        commitRename,
        addFlatFiles,
        processDropItems,
        retryFile,
        removeFile: removeUpload,
        updateTitleField,
        pauseUpload,
        resumeUpload,
        handleStage,
        handleMimeConfirm,
        dismissPendingMime,

        handleDragOver,
        handleDragLeave,
        handleClose,
    };
}
