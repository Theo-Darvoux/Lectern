import { useState, useEffect, useRef } from "react";
import { apiFetch } from "@/lib/api-client";

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
    metadata?: Record<string, unknown>;
}

export interface SearchResponse {
    items: SearchResult[];
    total: number;
    page: number;
    limit: number;
}

export function useSearch(query: string, delay = 300) {
    const [debouncedQuery, setDebouncedQuery] = useState(query);
    const [results, setResults] = useState<SearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const prevQueryRef = useRef(query);

    useEffect(() => {
        if (query === prevQueryRef.current && results.length > 0) return;

        const handler = setTimeout(() => {
            setDebouncedQuery(query);
            if (!query.trim()) {
                setResults([]);
                setLoading(false);
            } else {
                setLoading(true);
            }
        }, delay);

        prevQueryRef.current = query;

        return () => {
            clearTimeout(handler);
        };
    }, [query, delay, results.length]);

    useEffect(() => {
        if (!debouncedQuery.trim()) {
            return;
        }

        let isMounted = true;

        apiFetch<SearchResponse>(`/search?query=${encodeURIComponent(debouncedQuery)}&limit=10`)
            .then((data) => {
                if (isMounted) {
                    setResults(data.items);
                }
            })
            .catch((err) => {
                console.error("Search API error:", err);
                if (isMounted) setResults([]);
            })
            .finally(() => {
                if (isMounted) setLoading(false);
            });

        return () => {
            isMounted = false;
        };
    }, [debouncedQuery]);

    return { results, loading };
}
