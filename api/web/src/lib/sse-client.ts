import { API_BASE } from "@/lib/api-client";

export interface SSEOptions {
    /** API path relative to API_BASE, e.g. "/materials/123/sse" */
    url: string;
    /** Map of event names to handlers. Handlers receive the raw MessageEvent so they can read event.data. */
    listeners: Record<string, (event: MessageEvent) => void>;
    /** Reconnect delay in ms (default: 5000) */
    reconnectDelay?: number;
    /** Startup delay in ms to handle React Strict Mode (default: 0) */
    startupDelay?: number;
    /** Reconcile authoritative state after the server reports an overflow. */
    onResync?: () => void;
}

export interface SSEConnection {
    close: () => void;
}

/**
 * Creates a reconnecting EventSource connection.
 * Returns an object with a close() method for cleanup.
 */
export function createSSEConnection(options: SSEOptions): SSEConnection {
    const {
        url,
        listeners,
        reconnectDelay = 5000,
        startupDelay = 0,
        onResync = () => window.location.reload(),
    } = options;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    function connect() {
        if (cancelled) return;

        const fullUrl = url.startsWith("http") ? url : `${API_BASE}${url}`;
        es = new EventSource(fullUrl, { withCredentials: true });

        if (onResync) {
            es.addEventListener("resync_required", onResync as EventListener);
        }
        for (const [eventName, handler] of Object.entries(listeners)) {
            es.addEventListener(eventName, handler as EventListener);
        }

        es.onerror = () => {
            es?.close();
            es = null;
            if (!cancelled) {
                reconnectTimer = setTimeout(connect, reconnectDelay);
            }
        };
    }

    const startTimer = setTimeout(connect, startupDelay);

    return {
        close() {
            cancelled = true;
            clearTimeout(startTimer);
            if (reconnectTimer) clearTimeout(reconnectTimer);
            es?.close();
            es = null;
        },
    };
}
