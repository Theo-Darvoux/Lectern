"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { apiFetch } from "@/lib/api-client";
import { Loader2, ImageOff, PlusCircle, UploadCloud, CheckCircle2 } from "lucide-react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { uploadFile, logicalFileSize } from "@/lib/upload-client";
import { useStagingStore } from "@/lib/staging-store";
import { useTranslations } from "next-intl";

interface AsyncMaterialImageProps {
    src: string;
    alt?: string;
    material?: Record<string, unknown>;
    className?: string;
}

// Simple in-memory cache to prevent duplicate requests for the same directory/material
// when rendering multiple images in the same markdown file.
const fetchCache = new Map<string, Promise<unknown>>();

function cachedApiFetch<T>(url: string): Promise<T> {
    if (!fetchCache.has(url)) {
        const promise = apiFetch<T>(url).catch((err) => {
            fetchCache.delete(url); // Don't cache errors aggressively
            throw err;
        });
        fetchCache.set(url, promise);
    }
    return fetchCache.get(url)! as Promise<T>;
}

// ────────────────────────────────────────────────────────────────────────────────
// Missing-image upload dialog
// ────────────────────────────────────────────────────────────────────────────────

/**
 * Lets a user upload the missing image as an attachment to the current material,
 * staged as a PR operation.
 *
 * Security constraints:
 *   - Only image/* MIME types accepted (validated client-side + server-side pipeline)
 *   - File extension must match the expected filename's extension
 *   - `file_name` in the staged op is hard-locked to `expectedFileName` — not user-editable
 *   - `parent_material_id` is hard-locked to `material.id` — attachment scope can't drift
 */
function MissingImageUploadDialog({
    open,
    onClose,
    expectedFileName,
    material,
}: {
    open: boolean;
    onClose: () => void;
    expectedFileName: string;
    material: Record<string, unknown>;
}) {
    const t = useTranslations("MissingImage");
    const [status, setStatus] = useState<"idle" | "uploading" | "done" | "error">("idle");
    const [progress, setProgress] = useState(0);
    const [processingStatus, setProcessingStatus] = useState("");
    const [errorMsg, setErrorMsg] = useState("");
    const [isDragging, setIsDragging] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const abortRef = useRef<AbortController | null>(null);

    const addOperations = useStagingStore((s) => s.addOperations);
    const nextTempId = useStagingStore((s) => s.nextTempId);
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);

    const expectedExt = expectedFileName.split(".").pop()?.toLowerCase() ?? "";

    // Reset state whenever the dialog opens
    useEffect(() => {
        if (open) {
            setStatus("idle");
            setProgress(0);
            setProcessingStatus("");
            setErrorMsg("");
            setIsDragging(false);
        }
    }, [open]);

    const handleFile = useCallback(
        async (file: File) => {
            // Validate MIME is an image
            if (!file.type.startsWith("image/")) {
                setErrorMsg(t("onlyImages"));
                return;
            }

            const controller = new AbortController();
            abortRef.current = controller;
            setStatus("uploading");
            setProgress(0);
            setErrorMsg("");
            setProcessingStatus(t("preparing"));

            try {
                const result = await uploadFile(file, {
                    onProgress: setProgress,
                    onStatusUpdate: (msg) => setProcessingStatus(msg),
                    signal: controller.signal,
                });

                // Stage as attachment — file_name and parent_material_id are locked,
                // the user cannot influence them through this dialog.
                addOperations([
                    {
                        op: "create_material",
                        temp_id: nextTempId("mat"),
                        directory_id: (material.directory_id as string | null) ?? null,
                        title: expectedFileName,
                        type: "document",
                        file_key: result.file_key,
                        file_name: expectedFileName, // hard-locked — cannot be changed by user
                        file_size: logicalFileSize(result),
                        file_mime_type: result.mime_type,
                        parent_material_id: material.id as string, // locked to this material
                    },
                ]);

                setStatus("done");
                setTimeout(() => {
                    onClose();
                    setReviewOpen(true);
                }, 800);
            } catch (err) {
                if ((err as Error).message === "Upload cancelled") {
                    setStatus("idle");
                    return;
                }
                setStatus("error");
                setErrorMsg((err as Error).message || t("failed"));
            } finally {
                abortRef.current = null;
            }
        },
        [addOperations, nextTempId, setReviewOpen, material, expectedFileName, onClose],
    );

    return (
        <Dialog
            open={open}
            onOpenChange={(o) => {
                if (!o && status !== "uploading") onClose();
            }}
        >
            <DialogContent className="sm:max-w-sm">
                <DialogHeader>
                    <DialogTitle>{t("title")}</DialogTitle>
                    <DialogDescription>
                        {t("description")}
                    </DialogDescription>
                </DialogHeader>

                {/* Locked expected filename — read-only indicator */}
                <div className="rounded-md bg-muted px-3 py-2 flex items-center gap-2">
                    <span className="text-xs font-mono text-foreground flex-1 truncate">
                        {expectedFileName}
                    </span>
                    <span className="shrink-0 rounded border border-amber-400/40 bg-amber-50 dark:bg-amber-950/30 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
                        {t("locked")}
                    </span>
                </div>

                {status === "idle" && (
                    <div
                        role="region"
                        aria-label={t("dropZoneLabel")}
                        className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
                            isDragging
                                ? "border-primary bg-primary/5"
                                : "hover:border-primary/50 hover:bg-muted/30"
                        }`}
                        onDragOver={(e) => {
                            e.preventDefault();
                            setIsDragging(true);
                        }}
                        onDragLeave={() => setIsDragging(false)}
                        onDrop={(e) => {
                            e.preventDefault();
                            setIsDragging(false);
                            const file = e.dataTransfer.files[0];
                            if (file) void handleFile(file);
                        }}
                        onClick={() => fileInputRef.current?.click()}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                fileInputRef.current?.click();
                            }
                        }}
                        tabIndex={0}
                    >
                        <UploadCloud className="h-7 w-7 mx-auto text-muted-foreground mb-2" />
                        <p className="text-sm text-muted-foreground">
                            {t.rich("dropHere", {
                                ext: (chunks) => <span className="font-semibold text-foreground">.{expectedExt}</span>
                            })}
                        </p>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={(e) => {
                                const f = e.target.files?.[0];
                                if (f) void handleFile(f);
                                e.target.value = "";
                            }}
                        />
                    </div>
                )}

                {errorMsg && status !== "uploading" && (
                    <div className="space-y-2">
                        <p className="text-sm text-destructive">{errorMsg}</p>
                        {status === "error" && (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setStatus("idle");
                                    setErrorMsg("");
                                }}
                            >
                                {t("tryAgain")}
                            </Button>
                        )}
                    </div>
                )}

                {status === "uploading" && (
                    <div className="space-y-3 py-1">
                        <div className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin text-primary shrink-0" />
                            <span className="text-xs text-muted-foreground truncate">
                                {processingStatus || t("uploading")}
                            </span>
                        </div>
                        <div
                            className="h-1.5 bg-secondary rounded-full overflow-hidden"
                            role="progressbar"
                            aria-valuenow={Math.min(Math.round((progress * 100) / 80), 100)}
                            aria-valuemin={0}
                            aria-valuemax={100}
                        >
                            <div
                                className="h-full bg-primary transition-all duration-300"
                                style={{
                                    width: `${Math.min(Math.round((progress * 100) / 80), 100)}%`,
                                }}
                            />
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => abortRef.current?.abort()}
                        >
                            {t("cancel")}
                        </Button>
                    </div>
                )}

                {status === "done" && (
                    <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400 py-1">
                        <CheckCircle2 className="h-4 w-4 shrink-0" />
                        {t("staged")}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}

// ────────────────────────────────────────────────────────────────────────────────
// Main component
// ────────────────────────────────────────────────────────────────────────────────

export function AsyncMaterialImage({ src, alt, material, className }: AsyncMaterialImageProps) {
    const t = useTranslations("MissingImage");
    const [url, setUrl] = useState<string | null>(null);
    const [error, setError] = useState(false);
    const [loading, setLoading] = useState(true);
    const [uploadDialogOpen, setUploadDialogOpen] = useState(false);

    useEffect(() => {
        let mounted = true;

        async function resolveAndLoad() {
            try {
                // 1. Check if src is already an absolute URL
                if (src.startsWith("http://") || src.startsWith("https://") || src.startsWith("data:")) {
                    if (mounted) {
                        setUrl(src);
                        setLoading(false);
                    }
                    return;
                }

                // If no material context is provided, we can't reliably resolve relative paths
                if (!material) {
                    if (mounted) setError(true);
                    return;
                }

                // Handle wiki-links like "image.png" or "attachments/image.png"
                // Decode URI encoding (e.g. "Pasted%20image%20..." → "Pasted image ...")
                // Extract just the filename to be robust against standard markdown relativity
                const fileName = decodeURIComponent(src.split("/").pop() || src);

                // For Obsidian-style pasted images ("Pasted image 20260410132919.png"),
                // also try matching against just the timestamp portion ("20260410132919.png").
                const pastedImageMatch = /^pasted image (.+)$/i.exec(fileName);
                const altFileName = pastedImageMatch ? pastedImageMatch[1] : null;

                function matchesFileName(m: Record<string, unknown>): boolean {
                    const vInfo = m.current_version_info as Record<string, unknown> | undefined;
                    return (
                        m.title === fileName ||
                        vInfo?.file_name === fileName ||
                        (altFileName !== null && (m.title === altFileName || vInfo?.file_name === altFileName))
                    );
                }

                let targetMaterialId: string | null = null;

                // Step 1: Try attachments of the current material
                if (material.id) {
                    try {
                        const attachments = await cachedApiFetch<Record<string, unknown>[]>(`/materials/${material.id}/attachments`);
                        const matched = attachments.find(matchesFileName);
                        if (matched) targetMaterialId = matched.id as string;
                    } catch {
                        // ignore
                    }
                }

                // Step 2: Try siblings in the same directory
                if (!targetMaterialId && material.directory_id) {
                    try {
                        const children = await cachedApiFetch<{ materials: Record<string, unknown>[] }>(
                            `/directories/${material.directory_id}/children`
                        );
                        const matched = children.materials?.find(matchesFileName);
                        if (matched) targetMaterialId = matched.id as string;
                    } catch {
                        // ignore
                    }
                }

                // Step 3: Try global search as a fallback
                if (!targetMaterialId) {
                    try {
                        const searchRes = await cachedApiFetch<{ items: Record<string, unknown>[] }>(
                            `/search?query=${encodeURIComponent(fileName)}&limit=10`
                        );
                        const matched = searchRes.items?.find(
                            (item) => item.search_type === "material" && matchesFileName(item),
                        );
                        if (matched) targetMaterialId = matched.id as string;
                    } catch {
                        // ignore
                    }
                }

                // If found, fetch the presigned inline URL
                if (targetMaterialId && mounted) {
                    const inlineRes = await cachedApiFetch<{ url: string }>(`/materials/${targetMaterialId}/inline`);
                    setUrl(inlineRes.url);
                } else {
                    if (mounted) setError(true);
                }
            } catch {
                if (mounted) setError(true);
            } finally {
                if (mounted) setLoading(false);
            }
        }

        resolveAndLoad();

        return () => {
            mounted = false;
        };
    }, [src, material]);

    if (loading) {
        return (
            <span className="my-4 flex items-center justify-center rounded-md bg-muted p-8 animate-pulse border border-border">
                <Loader2 className="mr-2 h-5 w-5 animate-spin text-muted-foreground" />
                <span className="text-sm text-muted-foreground">{t("resolving", { src })}</span>
            </span>
        );
    }

    if (error || !url) {
        const decodedFileName = decodeURIComponent(src.split("/").pop() || src);
        const canUpload = !!(material?.id);

        return (
            <>
                <span
                    className="my-4 flex flex-wrap items-center gap-3 rounded-md bg-destructive/10 p-4 border border-destructive/20 text-destructive text-sm"
                    title={decodedFileName}
                >
                    <ImageOff className="h-5 w-5 shrink-0" />
                    <span className="flex-1 min-w-0 break-all">{t("failedToLoad", { fileName: decodedFileName })}</span>
                    {canUpload && (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="shrink-0 h-7 gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive hover:border-destructive/60 bg-transparent"
                            aria-label={t("uploadMissingLabel", { fileName: decodedFileName })}
                            onClick={(e) => {
                                e.stopPropagation();
                                setUploadDialogOpen(true);
                            }}
                        >
                            <PlusCircle className="h-3.5 w-3.5" />
                            {t("upload")}
                        </Button>
                    )}
                </span>
                {canUpload && (
                    <MissingImageUploadDialog
                        open={uploadDialogOpen}
                        onClose={() => setUploadDialogOpen(false)}
                        expectedFileName={decodedFileName}
                        material={material!}
                    />
                )}
            </>
        );
    }

    return (
        <span className="block my-4 text-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={url} alt={alt || src} className={className} loading="lazy" />
        </span>
    );
}
