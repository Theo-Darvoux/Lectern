"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
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
    const [total, setTotal] = useState(0);
    const [hasMore, setHasMore] = useState(false);

    const nextCursorRef = useRef<string | null>(null);
    const loadingRef = useRef(false);

    const fetchAnnotations = useCallback(
        async (reset = true) => {
            if (!materialId || loadingRef.current) return;
            if (reset) nextCursorRef.current = null;

            loadingRef.current = true;
            setLoading(true);
            try {
                const params = new URLSearchParams({ limit: String(PAGE_LIMIT) });
                if (!reset && nextCursorRef.current) {
                    params.set("cursor", nextCursorRef.current);
                }

                const data = await apiFetch<CursorPaginatedThreads>(
                    `/materials/${materialId}/annotations?${params}`
                );

                setThreads((prev) => (reset ? data.items : [...prev, ...data.items]));
                setTotal(data.total);
                nextCursorRef.current = data.next_cursor;
                setHasMore(data.next_cursor !== null);
            } catch {
                // silent
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
        nextCursorRef.current = null;
        if (materialId) fetchAnnotations(true);
    }, [materialId, fetchAnnotations]);

    useEffect(() => {
        if (!materialId) return;

        const connection = createSSEConnection({
            url: `/materials/${materialId}/sse`,
            listeners: {
                annotation_created: () => fetchAnnotations(true),
                annotation_deleted: () => fetchAnnotations(true),
            },
            startupDelay: 50,
        });

        return () => connection.close();
    }, [materialId, fetchAnnotations]);

    const createAnnotation = useCallback(
        async (body: string, selectionText?: string, positionData?: Record<string, unknown>, docPage?: number, replyToId?: string) => {
            if (!materialId) return null;
            const payload: Record<string, unknown> = { body };
            if (selectionText) payload.selection_text = selectionText;
            if (positionData) payload.position_data = positionData;
            if (docPage !== undefined) payload.page = docPage;
            if (replyToId) payload.reply_to_id = replyToId;

            const annotation = await apiFetch<AnnotationData>(
                `/materials/${materialId}/annotations`,
                {
                    method: "POST",
                    body: JSON.stringify(payload),
                }
            );
            await fetchAnnotations(true);
            return annotation;
        },
        [materialId, fetchAnnotations]
    );

    const editAnnotation = useCallback(
        async (annotationId: string, body: string) => {
            await apiFetch<AnnotationData>(`/annotations/${annotationId}`, {
                method: "PATCH",
                body: JSON.stringify({ body }),
            });
            await fetchAnnotations(true);
        },
        [fetchAnnotations]
    );

    const deleteAnnotation = useCallback(
        async (annotationId: string) => {
            await apiFetch<void>(`/annotations/${annotationId}`, {
                method: "DELETE",
            });
            await fetchAnnotations(true);
        },
        [fetchAnnotations]
    );

    return {
        threads,
        loading,
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
