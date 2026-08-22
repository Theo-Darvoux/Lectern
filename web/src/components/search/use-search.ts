import { useState, useEffect, useRef } from "react";
import { ApiError, apiFetch } from "@/lib/api-client";

export interface SearchResult {
    id: string;
    search_type: "material" | "directory";
    title?: string;
    name?: string;
    description?: string;
    tags?: string[];
    module?: string;
    type?: string;
    status?: "important" | "current" | "deprecated" | "archived";
    browse_path: string;
    total_views?: number;
    views_today?: number;
    is_liked?: boolean;
    like_count?: number;
    file_name?: string;
    file_mime_type?: string;
    ancestor_path?: string;
    match_context?: string;
    matched_field?: "file_name" | "tag" | "author" | "description" | "path" | "code";
    url?: string;
    metadata?: Record<string, unknown>;
}

export interface SearchResponse {
    items: SearchResult[];
    total: number;
    page: number;
    limit: number;
}

export type SearchStatus = "idle" | "debouncing" | "loading" | "success" | "empty" | "error";

export function getSearchErrorMessageKey(
    error: Error | null,
): "refineQuery" | "slowDown" | "searchUnavailableDescription" {
    if (error instanceof ApiError && error.status === 400) return "refineQuery";
    if (error instanceof ApiError && error.status === 429) return "slowDown";
    return "searchUnavailableDescription";
}

export type SearchKind = "material" | "directory";

export interface SearchOptions {
    delay?: number;
    page?: number;
    limit?: number;
    kind?: SearchKind;
    materialType?: string;
    status?: SearchResult["status"];
    directoryId?: string;
    recursive?: boolean;
}

export function buildSearchPath(query: string, options: SearchOptions = {}): string {
    const params = new URLSearchParams();
    params.set("query", query.trim());
    params.set("page", String(options.page ?? 1));
    params.set("limit", String(options.limit ?? 10));
    if (options.kind) params.set("kind", options.kind);
    if (options.materialType) params.set("material_type", options.materialType);
    if (options.status) params.set("status", options.status);
    if (options.directoryId) params.set("directory_id", options.directoryId);
    if (options.recursive) params.set("recursive", "true");
    return `/search?${params.toString()}`;
}

export function useSearch(query: string, options: SearchOptions | number = {}) {
    const normalizedOptions = typeof options === "number" ? { delay: options } : options;
    const {
        delay = 300,
        page = 1,
        limit = 10,
        kind,
        materialType,
        status: statusFilter,
        directoryId,
        recursive = false,
    } = normalizedOptions;
    const [debouncedQuery, setDebouncedQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [status, setStatus] = useState<SearchStatus>(query.trim() ? "debouncing" : "idle");
    const [error, setError] = useState<Error | null>(null);
    const [total, setTotal] = useState(0);
    const [resolvedRequestKey, setResolvedRequestKey] = useState<string | null>(null);
    const [requestVersion, setRequestVersion] = useState(0);
    const latestQueryRef = useRef(query);
    const requestControllerRef = useRef<AbortController | null>(null);
    const requestKey = buildSearchPath(query, {
        page,
        limit,
        kind,
        materialType,
        status: statusFilter,
        directoryId,
        recursive,
    });

    useEffect(() => {
        latestQueryRef.current = query;
        requestControllerRef.current?.abort();
        setResults([]);
        setTotal(0);
        setError(null);
        setResolvedRequestKey(null);
        setStatus(query.trim() ? "debouncing" : "idle");

        const handler = setTimeout(() => {
            setDebouncedQuery(query);
            if (!query.trim()) {
                setResults([]);
                setStatus("idle");
            } else {
                // A user can return to the last debounced value before this
                // timer fires. The version ensures that identical text still
                // starts a fresh request after the previous one was aborted.
                setRequestVersion((version) => version + 1);
                setStatus("loading");
            }
        }, delay);

        return () => {
            clearTimeout(handler);
        };
    }, [query, delay]);

    useEffect(() => {
        if (!debouncedQuery.trim() || latestQueryRef.current !== debouncedQuery) {
            return;
        }

        let isMounted = true;
        const controller = new AbortController();
        requestControllerRef.current = controller;
        setResults([]);
        setTotal(0);
        setError(null);
        setStatus("loading");

        const activeRequestKey = buildSearchPath(debouncedQuery, {
            page,
            limit,
            kind,
            materialType,
            status: statusFilter,
            directoryId,
            recursive,
        });
        apiFetch<SearchResponse>(activeRequestKey, {
            signal: controller.signal,
            timeoutMs: 10_000,
        })
            .then((data) => {
                if (isMounted && latestQueryRef.current === debouncedQuery) {
                    setResults(data.items);
                    setTotal(data.total);
                    setResolvedRequestKey(activeRequestKey);
                    setStatus(data.items.length > 0 ? "success" : "empty");
                }
            })
            .catch((err) => {
                if (controller.signal.aborted) return;
                console.error("Search API error:", err);
                if (isMounted) {
                    setResults([]);
                    setTotal(0);
                    setError(err instanceof Error ? err : new Error(String(err)));
                    setStatus("error");
                }
            });

        return () => {
            isMounted = false;
            controller.abort();
        };
    }, [debouncedQuery, page, limit, kind, materialType, statusFilter, directoryId, recursive, requestVersion]);

    const retry = () => {
        if (query.trim()) setRequestVersion((version) => version + 1);
    };

    return {
        results,
        total,
        status,
        error,
        retry,
        requestKey,
        resolvedRequestKey,
        loading: status === "debouncing" || status === "loading",
    };
}
