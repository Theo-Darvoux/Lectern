import { useCallback, useRef, useState, useEffect, useMemo } from "react";
import { toast } from "sonner";
import { useStagingStore } from "@/lib/staging-store";
import type { CreateMaterialOp } from "@/lib/staging-store";
import { MAX_FILE_SIZE_MB, ACCEPTED_FILE_TYPES, guessFileMime, sniffFileType, MIME_TO_EXT } from "@/lib/file-utils";
import { uploadFile, getUploadConfig, logicalFileSize, trackExistingUpload, uploadBatchZip, type UploadConfig, type TusUploadHandle } from "@/lib/upload-client";
import { ApiError } from "@/lib/api-client";
import { collectDroppedItems, extractDirPaths, traverseFolder, zipScannedFiles, type ScannedFile } from "@/lib/drop-utils";
import { compareNatural } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";
import { useTranslations } from "next-intl";
import { useUploadQueue, type QueueItem } from "@/lib/upload-queue";
import { useDropZoneStore } from "@/lib/drop-zone-store";

const MAX_CONCURRENT_UPLOADS = 4;
const maxFilesPerBatch_DEFAULT = 50;
const PRIVILEGED_ROLES = new Set(["moderator", "bureau", "vieux"]);

export function fileSize(bytes: number, t: (key: string, values?: Record<string, string | number | Date>) => string): string {
    if (bytes < 1024) return t("units.b", { count: bytes });
    if (bytes < 1024 * 1024) return t("units.kb", { count: (bytes / 1024).toFixed(1) });
    if (bytes < 1024 * 1024 * 1024) return t("units.mb", { count: (bytes / (1024 * 1024)).toFixed(1) });
    return t("units.gb", { count: (bytes / (1024 * 1024 * 1024)).toFixed(2) });
}

function titleFromFilename(name: string): string {
    return name
        .replace(/\.[^.]+$/, "")
        .replace(/[-_]+/g, " ")
        .replace(/(^|\s)(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase())
        .trim();
}

type DirPathMap = Map<string, string>;

interface SpeedEntry {
    lastBytes: number;
    lastTime: number;
    smoothedBps: number;
    measurements: number;
}

interface UseUploadEngineProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    directoryId: string | null;
    parentMaterialId?: string | null;
    initialFiles?: File[] | ScannedFile[];
    initialFolderEntries?: Array<{ entry: FileSystemDirectoryEntry; name: string }>;
}

export function useUploadEngine({
    open,
    onOpenChange,
    directoryId,
    parentMaterialId,
    initialFiles,
    initialFolderEntries,
}: UseUploadEngineProps) {
    const t = useTranslations("Upload");
    const { user } = useAuth();
    const isPrivileged = PRIVILEGED_ROLES.has(user?.role ?? "");
    const maxFilesPerBatch = isPrivileged ? Infinity : maxFilesPerBatch_DEFAULT;

    const addOperations = useStagingStore((s) => s.addOperations);
    const nextTempId = useStagingStore((s) => s.nextTempId);
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);

    const {
        items: files,
        addItems,
        updateItem,
        removeItem,
        clearAll,
        setActiveCount,
    } = useUploadQueue();

    const doneFiles = useMemo(() => files.filter((i) => i.status === "done"), [files]);
    const errorFiles = useMemo(() => files.filter((i) => i.status === "error" || i.status === "virus"), [files]);
    const inFlightCount = useMemo(
        () => files.filter((i) => i.status === "uploading" || i.status === "pending").length,
        [files],
    );

    const fileObjectsRef = useRef<Map<string, File>>(new Map());
    const quarantineKeysRef = useRef<Map<string, string>>(new Map());
    const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
    const tusHandlesRef = useRef<Map<string, TusUploadHandle>>(new Map());
    const previewUrlsRef = useRef<Map<string, string>>(new Map());
    const speedRef = useRef<Map<string, SpeedEntry>>(new Map());
    const [etaMap, setEtaMap] = useState<Map<string, { bps: number; etaSec: number }>>(new Map());

    const uploadQueueRef = useRef<string[]>([]);

    const [pendingDirPaths, setPendingDirPaths] = useState<DirPathMap>(new Map());
    const [pendingMimeFiles, setPendingMimeFiles] = useState<ScannedFile[]>([]);
    const [editingPath, setEditingPath] = useState<string | null>(null);
    const [editValue, setEditValue] = useState("");
    const [batchTags, setBatchTags] = useState<string[]>([]);
    const [reAttachingClientId, setReAttachingClientId] = useState<string | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const initialFilesProcessedRef = useRef(false);
    const [config, setConfig] = useState<UploadConfig | null>(null);

    useEffect(() => {
        getUploadConfig().then(setConfig).catch(() => {
            setConfig({
                allowed_extensions: ACCEPTED_FILE_TYPES.split(","),
                allowed_mimetypes: [],
                max_file_size_mb: MAX_FILE_SIZE_MB,
            });
        });
    }, []);

    useEffect(() => {
        files.forEach((item) => {
            if (item.isFromBatchZip) return;
            if ((item.status === "pending" || item.status === "uploading" || item.status === "paused") && !fileObjectsRef.current.has(item.clientId)) {
                updateItem(item.clientId, {
                    status: item.tusUrl ? "paused" : "error",
                    error: t("errorReferenceLost"),
                });
            }
        });
         
    }, []);

    const handleReAttach = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !reAttachingClientId) {
            setReAttachingClientId(null);
            return;
        }

        const item = files.find(i => i.clientId === reAttachingClientId);
        if (!item) {
            setReAttachingClientId(null);
            return;
        }

        if (file.name !== item.fileName || file.size !== item.fileSize) {
            toast.error(t("chooseDifferent"));
            setReAttachingClientId(null);
            return;
        }

        if (item.contentSha256) {
            updateItem(item.clientId, { processingStatus: t("verifyingFile") });
            const { sha256File } = await import("@/lib/crypto-utils");
            try {
                const newHash = await sha256File(file);
                if (newHash !== item.contentSha256) {
                    toast.error(t("hashMismatch"));
                    updateItem(item.clientId, { processingStatus: "", error: t("hashMismatch") });
                    setReAttachingClientId(null);
                    return;
                }
            } catch {
                toast.error(t("errorProcessing"));
                setReAttachingClientId(null);
                return;
            }
        }

        fileObjectsRef.current.set(item.clientId, file);
        if (file.type.startsWith("image/") || file.type === "application/pdf") {
            previewUrlsRef.current.set(item.clientId, URL.createObjectURL(file));
        }

        updateItem(item.clientId, { error: undefined, processingStatus: "" });
        setReAttachingClientId(null);
        toast.success(t("completed"));
    }, [files, updateItem, t, reAttachingClientId]);

    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (inFlightCount > 0) {
                e.preventDefault();
                e.returnValue = "";
            }
        };
        window.addEventListener("beforeunload", handleBeforeUnload);
        return () => window.removeEventListener("beforeunload", handleBeforeUnload);
    }, [inFlightCount]);

    const start = useCallback((clientId: string) => {
        const drainQueue = () => {
            const currentActive = useUploadQueue.getState().activeCount;
            if (currentActive < MAX_CONCURRENT_UPLOADS && uploadQueueRef.current.length > 0) {
                const nextId = uploadQueueRef.current.shift()!;
                const nextItem = useUploadQueue.getState().items.find(i => i.clientId === nextId);
                if (nextItem && (nextItem.status === "pending" || nextItem.status === "paused")) {
                    setActiveCount(currentActive + 1);
                    runUpload(nextId);
                }
            }
        };

        const runUpload = async (cid: string) => {
            const item = useUploadQueue.getState().items.find(i => i.clientId === cid);

            const quarantineKey = quarantineKeysRef.current.get(cid);
            if (quarantineKey) {
                updateItem(cid, { status: "uploading", progress: 80, error: undefined });
                const controller = new AbortController();
                abortControllersRef.current.set(cid, controller);

                try {
                    const result = await trackExistingUpload(quarantineKey, {
                        onProgress: (pct) => updateItem(cid, { progress: pct }),
                        onStatusUpdate: (msg, si, st) => updateItem(cid, { processingStatus: msg, stageIndex: si, stageTotal: st }),
                        signal: controller.signal,
                    });

                    const currentItem = useUploadQueue.getState().items.find(i => i.clientId === cid);
                    updateItem(cid, {
                        status: "done",
                        progress: 100,
                        fileKey: result.file_key,
                        correctedName: result.correctedName,
                        serverSize: logicalFileSize(result),
                        mimeType: result.mime_type,
                        title: currentItem?.title === titleFromFilename(currentItem?.fileName ?? "") ? titleFromFilename(result.correctedName) : currentItem?.title,
                    });
                } catch (err) {
                    const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : t("errorProcessing"));
                    if (msg !== "Upload cancelled") {
                        const isVirus = msg.includes("ERR_MALWARE_DETECTED");
                        updateItem(cid, { status: isVirus ? "virus" : "error", error: msg });
                    }
                } finally {
                    quarantineKeysRef.current.delete(cid);
                    abortControllersRef.current.delete(cid);
                    setActiveCount(Math.max(0, useUploadQueue.getState().activeCount - 1));
                    drainQueue();
                }
                return;
            }

            const file = fileObjectsRef.current.get(cid);

            if (!item || !file) {
                setActiveCount(Math.max(0, useUploadQueue.getState().activeCount - 1));
                drainQueue();
                return;
            }

            updateItem(cid, { status: "uploading", progress: item.progress || 0, error: undefined });
            const controller = new AbortController();
            abortControllersRef.current.set(cid, controller);

            try {
                const result = await uploadFile(file, {
                    onProgress: (pct) => updateItem(cid, { progress: pct }),
                    onStatusUpdate: (msg, stageIndex, stageTotal) => updateItem(cid, { processingStatus: msg, stageIndex, stageTotal }),
                    onHashComputed: (hash) => updateItem(cid, { contentSha256: hash }),
                    onBytesProgress: (uploaded, total) => {
                        const now = Date.now();
                        const prev = speedRef.current.get(cid) ?? {
                            lastBytes: 0,
                            lastTime: now,
                            smoothedBps: 0,
                            measurements: 0,
                        };
                        const dt = (now - prev.lastTime) / 1000;
                        const db = uploaded - prev.lastBytes;
                        const instant = dt > 0 ? db / dt : 0;
                        const smoothed =
                            prev.smoothedBps === 0 ? instant : 0.7 * prev.smoothedBps + 0.3 * instant;
                        const measurements = prev.measurements + 1;
                        speedRef.current.set(cid, {
                            lastBytes: uploaded,
                            lastTime: now,
                            smoothedBps: smoothed,
                            measurements,
                        });
                        
                        if (measurements >= 3) {
                            const etaSec = smoothed > 0 ? Math.round((total - uploaded) / smoothed) : 0;
                            setEtaMap((m) => new Map(m).set(cid, { bps: smoothed, etaSec }));
                        }
                    },
                    onTusReady: (handle) => {
                        tusHandlesRef.current.set(cid, handle);
                    },
                    onTusUrlAvailable: (url) => {
                        updateItem(cid, { tusUrl: url });
                    },
                    signal: controller.signal,
                    uploadId: item.uploadId,
                    tusUrl: item.tusUrl,
                    t: t,
                });

                const currentItem = useUploadQueue.getState().items.find(i => i.clientId === cid);

                updateItem(cid, {
                    status: "done",
                    progress: 100,
                    fileKey: result.file_key,
                    correctedName: result.correctedName,
                    serverSize: logicalFileSize(result),
                    mimeType: result.mime_type,
                    wasCompressed: result.wasCompressed,
                    title: currentItem?.title === titleFromFilename(file.name) ? titleFromFilename(result.correctedName) : currentItem?.title,
                });
            } catch (err) {
                const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : t("errorProcessing"));
                if (msg !== "Upload cancelled") {
                    const isVirus = msg.includes("ERR_MALWARE_DETECTED");
                    updateItem(cid, {
                        status: isVirus ? "virus" : "error",
                        error: msg,
                    });
                }
            } finally {
                tusHandlesRef.current.delete(cid);
                speedRef.current.delete(cid);
                abortControllersRef.current.delete(cid);
                setEtaMap((m) => {
                    const next = new Map(m);
                    next.delete(cid);
                    return next;
                });
                setActiveCount(Math.max(0, useUploadQueue.getState().activeCount - 1));
                drainQueue();
            }
        };

        uploadQueueRef.current.push(clientId);
        drainQueue();
    }, [updateItem, setActiveCount, t]);

    const commitRename = useCallback(
        (oldPath: string) => {
            const newLeaf = editValue.trim().replace(/\//g, "");
            setEditingPath(null);
            setEditValue("");
            if (!newLeaf || newLeaf === oldPath.split("/").pop()) return;

            const newPath = [...oldPath.split("/").slice(0, -1), newLeaf].join("/");

            setPendingDirPaths((prev) => {
                const next = new Map<string, string>();
                for (const [key, val] of prev) {
                    if (key === oldPath) next.set(newPath, val);
                    else if (key.startsWith(oldPath + "/")) next.set(newPath + key.slice(oldPath.length), val);
                    else next.set(key, val);
                }
                return next;
            });

            for (const item of useUploadQueue.getState().items) {
                if (!item.targetDirPath) continue;
                if (item.targetDirPath === oldPath)
                    updateItem(item.clientId, { targetDirPath: newPath });
                else if (item.targetDirPath.startsWith(oldPath + "/"))
                    updateItem(item.clientId, { targetDirPath: newPath + item.targetDirPath.slice(oldPath.length) });
            }
        },
        [editValue, updateItem],
    );

    const processScannedFiles = useCallback(
        async (scanned: ScannedFile[]) => {
            if (scanned.length === 0) return;

            const currentMaxSize = (config?.max_file_size_mb || MAX_FILE_SIZE_MB) * 1024 * 1024;

            const oversized = scanned.filter((s) => s.file.size > currentMaxSize);
            oversized.forEach((s) =>
                toast.error(t("fileExceedsLimit", { name: s.file.name, limit: config?.max_file_size_mb || MAX_FILE_SIZE_MB })),
            );

            let valid = scanned.filter((s) => s.file.size <= currentMaxSize);

            // Resolve MIME types from extension for files the browser mis-labels as octet-stream
            valid = valid.map(s => {
                const mime = guessFileMime(s.file);
                if (mime !== s.file.type) {
                    return { ...s, file: new File([s.file], s.file.name, { type: mime, lastModified: s.file.lastModified }) };
                }
                return s;
            });

            // Mobile pickers and messaging apps often deliver files with a stripped or
            // decorated name and a generic MIME type. Sniff magic bytes to recover the
            // real type, and restore the canonical extension so valid files are not
            // rejected here or by server-side filename validation.
            valid = await Promise.all(valid.map(async (s) => {
                let f = s.file;
                const ext = `.${f.name.split(".").pop()?.toLowerCase()}`;
                const extKnown = config
                    ? config.allowed_extensions.includes(ext)
                    : ACCEPTED_FILE_TYPES.split(",").includes(ext);
                if (extKnown) return s;
                let mime = f.type;
                if (!mime || mime === "application/octet-stream") {
                    const sniffed = await sniffFileType(f);
                    if (sniffed) mime = sniffed.mime;
                }
                const canonicalExt = MIME_TO_EXT[mime];
                const name = canonicalExt ? `${f.name}.${canonicalExt}` : f.name;
                if (mime === f.type && name === f.name) return s;
                f = new File([f], name, { type: mime, lastModified: f.lastModified });
                return { ...s, file: f };
            }));

            if (config) {
                const toProcess: ScannedFile[] = [];
                const needsMime: ScannedFile[] = [];

                for (const s of valid) {
                    const f = s.file;
                    const ext = `.${f.name.split(".").pop()?.toLowerCase()}`;
                    const isAllowedExt = config.allowed_extensions.includes(ext);
                    const isAllowedMime = f.type ? config.allowed_mimetypes.includes(f.type) : false;
                    const isTextMime = f.type.startsWith("text/");
                    const isOctetStream = !f.type || f.type === "application/octet-stream";

                    if (isOctetStream && isAllowedExt) {
                        // Extension is allowed but MIME still unknown — ask user
                        needsMime.push(s);
                    } else if (!isAllowedExt && !isAllowedMime && !isTextMime) {
                        toast.error(t("fileTypeNotSupported", { name: f.name, type: f.type || ext }));
                    } else {
                        toProcess.push(s);
                    }
                }

                if (needsMime.length > 0) {
                    setPendingMimeFiles(prev => [...prev, ...needsMime]);
                }
                valid = toProcess;
            }

            if (valid.length === 0) return;

            const remaining = maxFilesPerBatch - useUploadQueue.getState().items.length;
            if (remaining <= 0) {
                toast.error(t("maxFilesPerBatch", { count: maxFilesPerBatch }));
                return;
            }
            if (valid.length > remaining) {
                toast.warning(t("onlyAddingCapped", { count: remaining, limit: maxFilesPerBatch }));
                valid = valid.slice(0, remaining);
            }

            const dirPaths = extractDirPaths(valid);
            if (dirPaths.length > 0) {
                const newDirMap: DirPathMap = new Map();
                for (const path of dirPaths) newDirMap.set(path, nextTempId("dir"));
                setPendingDirPaths((prev) => new Map([...prev, ...newDirMap]));
            }

            const newItems: QueueItem[] = valid.map(({ file, relativePath }) => {
                const parts = relativePath.split("/");
                const dirPart = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
                const clientId = crypto.randomUUID();
                fileObjectsRef.current.set(clientId, file);

                if (file.type.startsWith("image/") || file.type === "application/pdf") {
                    previewUrlsRef.current.set(clientId, URL.createObjectURL(file));
                }

                return {
                    clientId,
                    uploadId: crypto.randomUUID(),
                    fileName: file.name,
                    fileSize: file.size,
                    fileMimeType: file.type || "application/octet-stream",
                    title: titleFromFilename(file.name),
                    status: "pending",
                    progress: 0,
                    processingStatus: "",
                    targetDirPath: dirPart,
                };
            });

            addItems(newItems);
            for (const item of newItems) start(item.clientId);
        },
        [start, nextTempId, addItems, config, maxFilesPerBatch, t],
    );

    const handleMimeConfirm = useCallback(
        (selections: Array<{ scanned: ScannedFile; mime: string }>) => {
            setPendingMimeFiles([]);
            const resolved: ScannedFile[] = selections.map(({ scanned, mime }) => ({
                ...scanned,
                file: new File([scanned.file], scanned.file.name, { type: mime, lastModified: scanned.file.lastModified }),
            }));
            processScannedFiles(resolved);
        },
        [processScannedFiles],
    );

    const dismissPendingMime = useCallback(() => setPendingMimeFiles([]), []);

    const processFolderViaZip = useCallback(
        async (entry: FileSystemDirectoryEntry, folderName: string) => {
            const placeholderId = crypto.randomUUID();

            addItems([{
                clientId: placeholderId,
                uploadId: crypto.randomUUID(),
                fileName: `${folderName}.zip`,
                fileSize: 0,
                fileMimeType: "application/zip",
                title: folderName,
                status: "pending",
                progress: 0,
                processingStatus: t("scanningFolder"),
                targetDirPath: "",
                folderName,
                isFromBatchZip: true,
            }]);

            const controller = new AbortController();
            abortControllersRef.current.set(placeholderId, controller);

            try {
                updateItem(placeholderId, { status: "uploading", progress: 2, processingStatus: t("scanningFolder") });
                const scanned = await traverseFolder(entry);

                if (scanned.length === 0) {
                    updateItem(placeholderId, { status: "error", error: t("folderEmpty") });
                    return;
                }

                updateItem(placeholderId, { progress: 5, processingStatus: t("zipping") });
                const zipBlob = await zipScannedFiles(scanned, (ratio) => {
                    updateItem(placeholderId, { progress: 5 + Math.round(ratio * 25) });
                });

                if (controller.signal.aborted) return;

                updateItem(placeholderId, { processingStatus: t("uploadsInProgress") });
                const response = await uploadBatchZip(zipBlob, {
                    onProgress: (pct) => updateItem(placeholderId, { progress: 30 + Math.round(pct * 0.5) }),
                    signal: controller.signal,
                });

                abortControllersRef.current.delete(placeholderId);
                removeItem(placeholderId);

                if (response.files.length === 0) {
                    const msg = response.errors.length > 0 ? response.errors[0] : t("noValidFiles");
                    toast.warning(`${folderName}: ${msg}`);
                    return;
                }

                const newDirMap: DirPathMap = new Map();
                for (const f of response.files) {
                    const parts = f.relative_path.split("/");
                    for (let depth = 1; depth < parts.length; depth++) {
                        const dirPath = parts.slice(0, depth).join("/");
                        if (!newDirMap.has(dirPath)) newDirMap.set(dirPath, nextTempId("dir"));
                    }
                }
                if (newDirMap.size > 0) {
                    setPendingDirPaths((prev) => new Map([...prev, ...newDirMap]));
                }

                const newItems: QueueItem[] = response.files.map((f) => {
                    const clientId = crypto.randomUUID();
                    quarantineKeysRef.current.set(clientId, f.quarantine_key);

                    const parts = f.relative_path.split("/");
                    const targetDirPath = parts.length > 1 ? parts.slice(0, -1).join("/") : folderName;

                    return {
                        clientId,
                        uploadId: f.upload_id,
                        fileName: f.filename,
                        fileSize: f.size,
                        fileMimeType: f.mime_type,
                        title: titleFromFilename(f.filename),
                        status: "pending" as const,
                        progress: 0,
                        processingStatus: "",
                        targetDirPath,
                        folderName,
                        isFromBatchZip: true,
                    };
                });

                addItems(newItems);
                for (const item of newItems) start(item.clientId);

                if (response.skipped > 0) {
                    toast.warning(`${folderName}: ${t("filesSkipped", { count: response.skipped, errors: response.errors.slice(0, 2).join(", ") + (response.errors.length > 2 ? "…" : "") })}`);
                }

            } catch (err) {
                const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : t("folderUploadFailed"));
                if (msg !== "Upload cancelled") {
                    updateItem(placeholderId, { status: "error", error: msg });
                } else {
                    removeItem(placeholderId);
                }
                abortControllersRef.current.delete(placeholderId);
            }
        },
        [addItems, updateItem, removeItem, nextTempId, start, t],
    );

    const addFlatFiles = useCallback(
        (newFiles: FileList | File[] | ScannedFile[]) => {
            const currentCount = useUploadQueue.getState().items.length;
            const remaining = maxFilesPerBatch - currentCount;
            if (remaining <= 0) {
                toast.error(t("maxFilesPerBatch", { count: maxFilesPerBatch }));
                return;
            }
            const filesArray = Array.isArray(newFiles) ? newFiles : Array.from(newFiles);
            const capped = (filesArray as (File | ScannedFile)[]).slice(0, remaining);
            if (capped.length < newFiles.length) {
                toast.warning(t("onlyAddingCapped", { count: remaining, limit: maxFilesPerBatch }));
            }

            const scanned: ScannedFile[] = capped.map(f => {
                if ("file" in f && "relativePath" in f) return f;
                return { file: f as File, relativePath: (f as File).name };
            });

            processScannedFiles(scanned);
        },
        [processScannedFiles, maxFilesPerBatch, t],
    );

    useEffect(() => {
        const hasFiles = (initialFiles?.length ?? 0) > 0;
        const hasFolders = (initialFolderEntries?.length ?? 0) > 0;
        if (open && (hasFiles || hasFolders) && !initialFilesProcessedRef.current) {
            initialFilesProcessedRef.current = true;
            if (hasFiles) queueMicrotask(() => addFlatFiles(initialFiles!));
            if (hasFolders) {
                for (const { entry, name } of initialFolderEntries!) {
                    void processFolderViaZip(entry, name);
                }
            }
        }
        if (!open) {
            initialFilesProcessedRef.current = false;
        }
    }, [open, initialFiles, initialFolderEntries, addFlatFiles, processFolderViaZip]);

    const dismissOverlay = useDropZoneStore((s) => s.dismissOverlay);

    useEffect(() => {
        if (!open) return;
        const handlePaste = (e: ClipboardEvent) => {
            if (!e.clipboardData) return;
            const items = e.clipboardData.items;
            const imageFiles: File[] = [];
            for (let i = 0; i < items.length; i++) {
                const item = items[i];
                if (item.kind === "file" && item.type.startsWith("image/")) {
                    const file = item.getAsFile();
                    if (file) imageFiles.push(file);
                }
            }
            if (imageFiles.length > 0) {
                addFlatFiles(imageFiles);
            }
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
            if (dropped.files.length > 0) processScannedFiles(dropped.files);
            for (const { entry, name } of dropped.folders) {
                void processFolderViaZip(entry, name);
            }
        },
        [processScannedFiles, processFolderViaZip, t],
    );

    useEffect(() => {
        if (!open) return;

        const onDragOver = (e: DragEvent) => {
            if (!e.dataTransfer?.types.includes("Files")) return;
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
        };

        const onDrop = (e: DragEvent) => {
            e.preventDefault();
            e.stopPropagation();
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

    const retryFile = (clientId: string) => {
        const item = files.find((f) => f.clientId === clientId);
        if (!item) return;
        updateItem(clientId, {
            status: "pending",
            progress: 0,
            processingStatus: "",
            error: undefined,
        });
        start(clientId);
    };

    const removeFile = (clientId: string) => {
        const controller = abortControllersRef.current.get(clientId);
        if (controller) controller.abort();
        const tusHandle = tusHandlesRef.current.get(clientId);
        if (tusHandle) tusHandle.abort(true);

        const preview = previewUrlsRef.current.get(clientId);
        if (preview) {
            URL.revokeObjectURL(preview);
            previewUrlsRef.current.delete(clientId);
        }

        removeItem(clientId);
        fileObjectsRef.current.delete(clientId);
    };

    const updateTitleField = (clientId: string, title: string) => {
        updateItem(clientId, { title });
    };

    const pauseUpload = (clientId: string) => {
        const handle = tusHandlesRef.current.get(clientId);
        if (handle) {
            handle.pause();
            updateItem(clientId, { status: "paused" });
            setActiveCount(Math.max(0, useUploadQueue.getState().activeCount - 1));
            const item = files.find(f => f.clientId === clientId);
            if (item) {
                const nextPending = files.find(f => f.status === "pending" && !uploadQueueRef.current.includes(f.clientId));
                if (nextPending) start(nextPending.clientId);
            }
        }
    };

    const resumeUpload = (clientId: string) => {
        const handle = tusHandlesRef.current.get(clientId);
        const entry = files.find((f) => f.clientId === clientId);
        if (handle && entry) {
            if (useUploadQueue.getState().activeCount >= MAX_CONCURRENT_UPLOADS) {
                toast.error(t("concurrencyLimitReached", { count: MAX_CONCURRENT_UPLOADS }));
                return;
            }

            updateItem(clientId, { status: "uploading" });
            setActiveCount(useUploadQueue.getState().activeCount + 1);
            handle.resume();
        }
    };

    const inFlightFiles = files.filter(
        (f) => f.status === "uploading" || f.status === "pending" || f.status === "paused",
    );

    const canStage = doneFiles.length > 0 && inFlightCount === 0;

    const handleStage = () => {
        if (errorFiles.length > 0) {
            const confirmed = window.confirm(
                t("confirmFailedFiles", { count: errorFiles.length })
            );
            if (!confirmed) return;
        }

        const dirPaths = [...pendingDirPaths.keys()].sort(
            (a, b) => a.split("/").length - b.split("/").length || compareNatural(a, b),
        );

        const dirOps = dirPaths.map((path) => {
            const parts = path.split("/");
            const name = parts[parts.length - 1];
            const parentPath = parts.slice(0, -1).join("/");
            const parentId = parentPath
                ? pendingDirPaths.get(parentPath) ?? (directoryId || null)
                : (directoryId || null);
            return {
                op: "create_directory" as const,
                temp_id: pendingDirPaths.get(path)!,
                parent_id: parentId,
                name,
                type: "folder" as const,
                tags: batchTags.length > 0 ? batchTags : undefined,
            };
        });

        const matOps: CreateMaterialOp[] = doneFiles.map((f: QueueItem) => {
            const dirId = f.targetDirPath
                ? (pendingDirPaths.get(f.targetDirPath) ?? (directoryId || null))
                : (directoryId || null);
            return {
                op: "create_material" as const,
                temp_id: nextTempId("mat"),
                directory_id: dirId!,
                title: f.title || titleFromFilename(f.correctedName ?? f.fileName),
                type: "document" as const,
                file_key: f.fileKey!,
                file_name: f.correctedName ?? f.fileName,
                file_size: f.serverSize ?? f.fileSize,
                file_mime_type: f.mimeType || f.fileMimeType || "application/octet-stream",
                ...(parentMaterialId ? { parent_material_id: parentMaterialId } : {}),
                tags: batchTags.length > 0 ? batchTags : undefined,
            };
        });

        addOperations([...dirOps, ...matOps]);

        doneFiles.forEach((f: QueueItem) => {
            removeItem(f.clientId);
            fileObjectsRef.current.delete(f.clientId);
        });
        setPendingDirPaths(new Map());

        const total = dirOps.length + matOps.length;
        toast.success(t("addedToDraft", { count: total }));

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

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
        processDropItems(e.dataTransfer.items);
    };

    const doClose = () => {
        abortControllersRef.current.forEach((c) => c.abort());
        tusHandlesRef.current.forEach((h) => h.abort(false));

        files.forEach((f) => {
            const preview = previewUrlsRef.current.get(f.clientId);
            if (preview) URL.revokeObjectURL(preview);
        });
        clearAll();
        fileObjectsRef.current.clear();
        quarantineKeysRef.current.clear();
        previewUrlsRef.current.clear();
        setPendingDirPaths(new Map());
        uploadQueueRef.current = [];
        onOpenChange(false);
    };

    const handleClose = (nextOpen: boolean) => {
        if (nextOpen) {
            onOpenChange(true);
            return;
        }
        if (inFlightFiles.length > 0) {
            toast.warning(t("waits"));
            return;
        }
        if (files.length === 0) {
            doClose();
            return;
        }
        onOpenChange(false);
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
        isDragging,
        reAttachingClientId,
        config,
        
        fileObjectsRef,
        previewUrlsRef,
        etaMap,
        
        setEditingPath,
        setEditValue,
        setBatchTags,
        setReAttachingClientId,

        handleReAttach,
        commitRename,
        addFlatFiles,
        processDropItems,
        retryFile,
        removeFile,
        updateTitleField,
        pauseUpload,
        resumeUpload,
        handleStage,
        handleMimeConfirm,
        dismissPendingMime,
        
        handleDragOver,
        handleDragLeave,
        handleDrop,
        handleClose,
    };
}