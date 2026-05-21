"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api-client";
import { createSSEConnection } from "@/lib/sse-client";

interface AnnotationAuthor {
    id: string;
    display_name: string | null;
    avatar_url: string | null;
}

export interface AnnotationData {
    id: string;
    material_id: string;
    version_id: string | null;
    author_id: string | null;
    author: AnnotationAuthor | null;
    body: string;
    page: number | null;
    selection_text: string | null;
    position_data: Record<string, unknown> | null;
    thread_id: string | null;
    reply_to_id: string | null;
    created_at: string;
    updated_at: string;
}

export interface ThreadData {
    root: AnnotationData;
    replies: AnnotationData[];
}

interface CursorPaginatedThreads {
    items: ThreadData[];
    total: number;
    next_cursor: string | null;
}

const PAGE_LIMIT = 50;

export function useAnnotations(materialId: string | null) {
    const [threads, setThreads] = useState<ThreadData[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(false);
    const [total, setTotal] = useState(0);
    const [hasMore, setHasMore] = useState(false);

    const nextCursorRef = useRef<string | null>(null);
    const loadingRef = useRef(false);
    // tracks root annotation IDs already in state — used for SSE dedup
    const seenRootIds = useRef<Set<string>>(new Set());
    // mirror of threads for synchronous reads in event handlers (avoids stale closures)
    const threadsRef = useRef<ThreadData[]>([]);

    useEffect(() => {
        threadsRef.current = threads;
    }, [threads]);

    const fetchAnnotations = useCallback(
        async (reset = true) => {
            if (!materialId || loadingRef.current) return;
            if (reset) nextCursorRef.current = null;

            loadingRef.current = true;
            setLoading(true);
            setError(false);
            try {
                const params = new URLSearchParams({ limit: String(PAGE_LIMIT) });
                if (!reset && nextCursorRef.current) {
                    params.set("cursor", nextCursorRef.current);
                }

                const data = await apiFetch<CursorPaginatedThreads>(
                    `/materials/${materialId}/annotations?${params}`
                );

                if (reset) {
                    seenRootIds.current = new Set(data.items.map((t) => t.root.id));
                } else {
                    for (const t of data.items) seenRootIds.current.add(t.root.id);
                }
                setThreads((prev) => (reset ? data.items : [...prev, ...data.items]));
                setTotal(data.total);
                nextCursorRef.current = data.next_cursor;
                setHasMore(data.next_cursor !== null);
            } catch (err) {
                setError(true);
                toast.error(err instanceof Error ? err.message : "Failed to load annotations");
            } finally {
                loadingRef.current = false;
                setLoading(false);
            }
        },
        [materialId]
    );

    const loadMore = useCallback(async () => {
        if (!hasMore) return;
        await fetchAnnotations(false);
    }, [hasMore, fetchAnnotations]);

    useEffect(() => {
        setThreads([]);
        setHasMore(false);
        setError(false);
        seenRootIds.current = new Set();
        threadsRef.current = [];
        nextCursorRef.current = null;
        if (materialId) fetchAnnotations(true);
    }, [materialId, fetchAnnotations]);

    // --- apply helpers (shared by SSE path and local mutation path) ---

    const applyCreated = useCallback((ann: AnnotationData) => {
        if (ann.reply_to_id === null) {
            // root annotation — idempotent via seenRootIds ref
            if (seenRootIds.current.has(ann.id)) return;
            seenRootIds.current.add(ann.id);
            setThreads((prev) => [{ root: ann, replies: [] }, ...prev]);
            setTotal((n) => n + 1);
        } else {
            // reply — find thread and append if not already present
            setThreads((prev) =>
                prev.map((t) => {
                    if (t.root.id !== ann.thread_id) return t;
                    if (t.replies.some((r) => r.id === ann.id)) return t;
                    return { ...t, replies: [...t.replies, ann] };
                })
            );
        }
    }, []);

    const applyUpdated = useCallback((ann: AnnotationData) => {
        setThreads((prev) =>
            prev.map((t) => {
                if (t.root.id === ann.id) return { ...t, root: ann };
                const idx = t.replies.findIndex((r) => r.id === ann.id);
                if (idx === -1) return t;
                const replies = [...t.replies];
                replies[idx] = ann;
                return { ...t, replies };
            })
        );
    }, []);

    const applyDeleted = useCallback((id: string, threadId: string) => {
        if (id === threadId) {
            // root deleted — drop entire thread
            seenRootIds.current.delete(id);
            setThreads((prev) => prev.filter((t) => t.root.id !== id));
            setTotal((n) => Math.max(0, n - 1));
        } else {
            // reply deleted
            setThreads((prev) =>
                prev.map((t) => {
                    if (t.root.id !== threadId) return t;
                    return { ...t, replies: t.replies.filter((r) => r.id !== id) };
                })
            );
        }
    }, []);

    // --- SSE listener ---

    useEffect(() => {
        if (!materialId) return;

        const connection = createSSEConnection({
            url: `/materials/${materialId}/sse`,
            listeners: {
                annotation_created: (e) => {
                    try {
                        applyCreated(JSON.parse(e.data).annotation);
                    } catch {
                        fetchAnnotations(true);
                    }
                },
                annotation_updated: (e) => {
                    try {
                        applyUpdated(JSON.parse(e.data).annotation);
                    } catch {
                        fetchAnnotations(true);
                    }
                },
                annotation_deleted: (e) => {
                    try {
                        const { id, thread_id } = JSON.parse(e.data);
                        applyDeleted(id, thread_id);
                    } catch {
                        fetchAnnotations(true);
                    }
                },
            },
            startupDelay: 50,
        });

        return () => connection.close();
    }, [materialId, applyCreated, applyUpdated, applyDeleted, fetchAnnotations]);

    // --- mutations: update state from response, never refetch ---

    const createAnnotation = useCallback(
        async (body: string, selectionText?: string, positionData?: Record<string, unknown>, docPage?: number, replyToId?: string) => {
            if (!materialId) return null;
            const payload: Record<string, unknown> = { body };
            if (selectionText) payload.selection_text = selectionText;
            if (positionData) payload.position_data = positionData;
            if (docPage !== undefined) payload.page = docPage;
            if (replyToId) payload.reply_to_id = replyToId;

            // capture response and update state directly — never refetch
            const annotation = await apiFetch<AnnotationData>(
                `/materials/${materialId}/annotations`,
                {
                    method: "POST",
                    body: JSON.stringify(payload),
                }
            );
            applyCreated(annotation);
            return annotation;
        },
        [materialId, applyCreated]
    );

    const editAnnotation = useCallback(
        async (annotationId: string, body: string) => {
            // capture response and update state directly — never refetch
            const annotation = await apiFetch<AnnotationData>(`/annotations/${annotationId}`, {
                method: "PATCH",
                body: JSON.stringify({ body }),
            });
            applyUpdated(annotation);
        },
        [applyUpdated]
    );

    const deleteAnnotation = useCallback(
        async (annotationId: string) => {
            // read threadsRef to find thread_id synchronously, before any state mutation
            const thread = threadsRef.current.find(
                (t) => t.root.id === annotationId || t.replies.some((r) => r.id === annotationId)
            );
            if (thread) {
                applyDeleted(annotationId, thread.root.id);
            }
            await apiFetch<void>(`/annotations/${annotationId}`, { method: "DELETE" });
        },
        [applyDeleted]
    );

    return {
        threads,
        loading,
        error,
        total,
        hasMore,
        loadMore,
        fetchAnnotations,
        createAnnotation,
        editAnnotation,
        deleteAnnotation,
    };
}

export type AnnotationsAPI = ReturnType<typeof useAnnotations>;
export const AnnotationsContext = createContext<AnnotationsAPI | null>(null);
export function useAnnotationsContext() {
    return useContext(AnnotationsContext);
}
