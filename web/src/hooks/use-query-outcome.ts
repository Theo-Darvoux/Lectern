import { useEffect, useRef } from "react";

interface QueryOutcome {
    isSuccess: boolean;
    isError: boolean;
    dataUpdatedAt: number;
    errorUpdatedAt: number;
}

/** Report each settled fetch of a query to a parent that tracks readiness. */
export function useQueryOutcome(query: QueryOutcome, onReady?: () => void, onError?: () => void): void {
    const callbacks = useRef({ onReady, onError });
    useEffect(() => {
        callbacks.current = { onReady, onError };
    }, [onReady, onError]);

    useEffect(() => {
        if (query.isSuccess) callbacks.current.onReady?.();
    }, [query.isSuccess, query.dataUpdatedAt]);

    useEffect(() => {
        if (query.isError) callbacks.current.onError?.();
    }, [query.isError, query.errorUpdatedAt]);
}
