"use client";

import { useEffect, useState } from "react";
import { apiRequest, fetchMaterialFile } from "@/lib/api-client";

interface UseMaterialFileOptions {
    materialId: string;
    fileKey: string;
    mode?: "text" | "blob" | "arrayBuffer" | "url";
    maxBytes?: number;
}

interface UseMaterialFileReturn {
    content: string;
    blobUrl: string | null;
    arrayBuffer: ArrayBuffer | null;
    loading: boolean;
    error: string | null;
    truncated: boolean;
}

export function useMaterialFile({
    materialId,
    fileKey,
    mode = "text",
    maxBytes,
}: UseMaterialFileOptions): UseMaterialFileReturn {
    const [content, setContent] = useState("");
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [arrayBuffer, setArrayBuffer] = useState<ArrayBuffer | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [truncated, setTruncated] = useState(false);

    useEffect(() => {
        let cancelled = false;
        let objectUrl: string | null = null;

        const load = async () => {
            // Reset state on start (deferred slightly to avoid flicker if cache-busting)
            queueMicrotask(() => {
                if (cancelled) return;
                setLoading(true);
                setError(null);
                setTruncated(false);
                if (mode === "text") setContent("");
                else if (mode === "blob") setBlobUrl(null);
                else setArrayBuffer(null);
            });

            try {
                if (mode === "url") {
                    const { getMaterialFileUrl } = await import("@/lib/api-client");
                    const url = await getMaterialFileUrl(materialId);
                    if (!cancelled) {
                        setBlobUrl(url);
                    }
                    return;
                }

                if (mode === "text") {
                    // Text files are gzip-compressed in R2. Fetching via the raw worker
                    // URL can produce garbled output if Cloudflare's cache decompresses
                    // the body transparently while keeping the content-encoding header,
                    // causing double-decompression. The /text-content endpoint handles
                    // decompression correctly server-side and returns clean UTF-8 text.
                    const res = await apiRequest(`/materials/${materialId}/text-content`);
                    let text = await res.text();
                    if (maxBytes && text.length > maxBytes) {
                        text = text.slice(0, maxBytes);
                        if (!cancelled) setTruncated(true);
                    }
                    if (!cancelled) setContent(text);
                    return;
                }

                const res = await fetchMaterialFile(materialId);

                if (mode === "blob") {
                    const blob = await res.blob();
                    if (!cancelled) {
                        objectUrl = URL.createObjectURL(blob);
                        setBlobUrl(objectUrl);
                    }
                } else if (mode === "arrayBuffer") {
                    const buffer = await res.arrayBuffer();
                    if (!cancelled) setArrayBuffer(buffer);
                }
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : "Failed to load material file");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        load();

        return () => {
            cancelled = true;
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
            }
        };
    }, [materialId, fileKey, mode, maxBytes]);

    return { content, blobUrl, arrayBuffer, loading, error, truncated };
}
