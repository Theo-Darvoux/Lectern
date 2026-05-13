"use client";

import { useEffect, useState } from "react";
import { fetchMaterialFile } from "@/lib/api-client";

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
                } else {
                    // mode === "text"
                    const contentLength = Number(res.headers.get("content-length") ?? NaN);
                    let text: string;

                    if (maxBytes && !isNaN(contentLength) && contentLength > maxBytes) {
                        // Use streaming for large files if possible
                        if (res.body) {
                            const reader = res.body.getReader();
                            const chunks: any[] = [];
                            let received = 0;
                            while (received < maxBytes) {
                                const { done, value } = await reader.read();
                                if (done || !value) break;
                                chunks.push(value);
                                received += value.byteLength;
                            }
                            reader.cancel();
                            const blob = new Blob(chunks);
                            text = await blob.text();
                            if (!cancelled) setTruncated(true);
                        } else {
                            // Fallback to slice
                            text = await res.text();
                            if (text.length > maxBytes) {
                                text = text.slice(0, maxBytes);
                                if (!cancelled) setTruncated(true);
                            }
                        }
                    } else {
                        text = await res.text();
                        if (maxBytes && text.length > maxBytes) {
                            text = text.slice(0, maxBytes);
                            if (!cancelled) setTruncated(true);
                        }
                    }

                    if (!cancelled) setContent(text);
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
