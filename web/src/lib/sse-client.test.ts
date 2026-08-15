import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
    closeAllSSEConnections,
    subscribeToSSE as subscribeToSSEBase,
    type SSEOptions,
} from "./sse-client";

function subscribeToSSE(
    options: Omit<SSEOptions, "onResync"> & { onResync?: () => void },
) {
    return subscribeToSSEBase({ onResync: () => {}, ...options });
}

// ---------------------------------------------------------------------------
// EventSource mock
// ---------------------------------------------------------------------------

class MockEventSource {
    static instances: MockEventSource[] = [];

    url: string;
    withCredentials: boolean;
    onopen: ((e: Event) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    private handlers: Record<string, EventListenerOrEventListenerObject[]> = {};
    readyState = 1; // OPEN

    constructor(url: string, opts?: { withCredentials?: boolean }) {
        this.url = url;
        this.withCredentials = opts?.withCredentials ?? false;
        MockEventSource.instances.push(this);
    }

    addEventListener(type: string, handler: EventListenerOrEventListenerObject) {
        if (!this.handlers[type]) this.handlers[type] = [];
        this.handlers[type].push(handler);
    }

    removeEventListener(type: string, handler: EventListenerOrEventListenerObject) {
        const listeners = this.handlers[type];
        if (!listeners) return;
        this.handlers[type] = listeners.filter((listener) => listener !== handler);
    }

    dispatchCustomEvent(
        type: string,
        data = JSON.stringify({ channel: "notifications", data: {} }),
    ) {
        const listeners = this.handlers[type] ?? [];
        const event = new MessageEvent(type, { data });
        for (const l of listeners) {
            if (typeof l === "function") l(event);
            else l.handleEvent(event);
        }
    }

    close() {
        this.readyState = 2; // CLOSED
    }
}

class MockBroadcastChannel {
    onmessage: ((event: MessageEvent) => void) | null = null;
    postMessage = vi.fn();
    close = vi.fn();

    constructor(public readonly name: string) {}
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test" }));

beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    Object.defineProperty(navigator, "locks", {
        configurable: true,
        value: undefined,
    });
    vi.useFakeTimers();
});

afterEach(() => {
    closeAllSSEConnections();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("subscribeToSSE", () => {
    it("opens the master endpoint with the requested entity topic", () => {
        subscribeToSSE({ channel: "material:123", listeners: {} });
        vi.runAllTimers();
        expect(MockEventSource.instances).toHaveLength(1);
        expect(MockEventSource.instances[0].url).toBe(
            "http://api.test/events/sse?topic=material%3A123",
        );
    });

    it("sends credentials on the master transport", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        vi.runAllTimers();
        expect(MockEventSource.instances[0].url).toBe("http://api.test/events/sse");
        expect(MockEventSource.instances[0].withCredentials).toBe(true);
    });

    it("registers provided event listeners", () => {
        const handler = vi.fn();
        subscribeToSSE({
            channel: "notifications",
            listeners: { material_deleted: handler },
        });
        vi.runAllTimers();
        MockEventSource.instances[0].dispatchCustomEvent("material_deleted");
        expect(handler).toHaveBeenCalledOnce();
    });

    it("registers multiple listeners independently", () => {
        const onDelete = vi.fn();
        const onChild = vi.fn();
        subscribeToSSE({
            channel: "directory:abc",
            listeners: { directory_deleted: onDelete, child_added: onChild },
        });
        vi.runAllTimers();
        const es = MockEventSource.instances[0];
        const data = JSON.stringify({ channel: "directory:abc", data: {} });
        es.dispatchCustomEvent("directory_deleted", data);
        es.dispatchCustomEvent("child_added", data);
        expect(onDelete).toHaveBeenCalledOnce();
        expect(onChild).toHaveBeenCalledOnce();
    });

    it("respects startupDelay before connecting", () => {
        subscribeToSSE({ channel: "notifications", listeners: {}, startupDelay: 100 });
        expect(MockEventSource.instances).toHaveLength(0);
        vi.advanceTimersByTime(99);
        expect(MockEventSource.instances).toHaveLength(0);
        vi.advanceTimersByTime(1);
        vi.runOnlyPendingTimers();
        expect(MockEventSource.instances).toHaveLength(1);
    });

    it("reconnects after onerror with reconnectDelay", () => {
        subscribeToSSE({ channel: "notifications", listeners: {}, reconnectDelay: 500 });
        vi.runAllTimers();
        expect(MockEventSource.instances).toHaveLength(1);

        MockEventSource.instances[0].onerror?.(new Event("error"));
        expect(MockEventSource.instances).toHaveLength(1); // no immediate reconnect

        vi.advanceTimersByTime(500);
        expect(MockEventSource.instances).toHaveLength(2);
    });

    it("reconciles when a fallback tab's transport is rejected", () => {
        const onResync = vi.fn();
        subscribeToSSE({
            channel: "notifications",
            listeners: {},
            onResync,
            reconnectDelay: 500,
        });
        vi.runAllTimers();

        MockEventSource.instances[0].onerror?.(new Event("error"));

        expect(onResync).toHaveBeenCalledOnce();
    });

    it("close() stops reconnection after error", () => {
        const conn = subscribeToSSE({
            channel: "notifications",
            listeners: {},
            reconnectDelay: 500,
        });
        vi.runAllTimers();
        MockEventSource.instances[0].onerror?.(new Event("error"));
        conn.close();
        vi.advanceTimersByTime(1000);
        // Still only 1 instance — cancelled flag prevents reconnect
        expect(MockEventSource.instances).toHaveLength(1);
    });

    it("close() before startup prevents connection", () => {
        const conn = subscribeToSSE({
            channel: "notifications",
            listeners: {},
            startupDelay: 200,
        });
        conn.close();
        vi.advanceTimersByTime(500);
        expect(MockEventSource.instances).toHaveLength(0);
    });

    it("close() closes the underlying EventSource", () => {
        const conn = subscribeToSSE({ channel: "notifications", listeners: {} });
        vi.runAllTimers();
        const es = MockEventSource.instances[0];
        conn.close();
        expect(es.readyState).toBe(2); // CLOSED
    });

    it("calls onResync for the overflow control event", () => {
        const onResync = vi.fn();
        subscribeToSSE({
            channel: "notifications",
            listeners: {},
            onResync,
        });
        vi.runAllTimers();

        MockEventSource.instances[0].dispatchCustomEvent("resync_required");

        expect(onResync).toHaveBeenCalledOnce();
    });

    it("reconciles authoritative state whenever a transport opens", () => {
        const onResync = vi.fn();
        subscribeToSSE({ channel: "notifications", listeners: {}, onResync });
        vi.runAllTimers();

        MockEventSource.instances[0].onopen?.(new Event("open"));

        expect(onResync).toHaveBeenCalledOnce();
    });

    it("shares one EventSource between subscribers to the same URL", () => {
        const firstHandler = vi.fn();
        const secondHandler = vi.fn();

        const first = subscribeToSSE({
            channel: "material:shared",
            listeners: { material_updated: firstHandler },
        });
        const second = subscribeToSSE({
            channel: "material:shared",
            listeners: { annotation_created: secondHandler },
        });

        vi.runAllTimers();

        expect(MockEventSource.instances).toHaveLength(1);
        const es = MockEventSource.instances[0];

        const data = JSON.stringify({ channel: "material:shared", data: {} });
        es.dispatchCustomEvent("material_updated", data);
        es.dispatchCustomEvent("annotation_created", data);
        expect(firstHandler).toHaveBeenCalledOnce();
        expect(secondHandler).toHaveBeenCalledOnce();

        first.close();
        expect(es.readyState).toBe(1);

        es.dispatchCustomEvent("material_updated", data);
        es.dispatchCustomEvent("annotation_created", data);
        expect(firstHandler).toHaveBeenCalledOnce();
        expect(secondHandler).toHaveBeenCalledTimes(2);

        second.close();
        expect(es.readyState).toBe(2);
    });

    it("reconnects a shared URL only once after an error", () => {
        subscribeToSSE({
            channel: "material:shared",
            listeners: { material_updated: vi.fn() },
            reconnectDelay: 500,
        });
        subscribeToSSE({
            channel: "material:shared",
            listeners: { annotation_created: vi.fn() },
            reconnectDelay: 500,
        });

        vi.runAllTimers();
        expect(MockEventSource.instances).toHaveLength(1);

        MockEventSource.instances[0].onerror?.(new Event("error"));
        vi.advanceTimersByTime(500);

        expect(MockEventSource.instances).toHaveLength(2);
    });

    it("uses one master EventSource for different logical channels", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        subscribeToSSE({ channel: "pull_requests", listeners: {} });

        vi.runAllTimers();

        expect(MockEventSource.instances).toHaveLength(1);
    });

    it("keeps the master URL valid and polls when logical topics exceed its budget", () => {
        const onResync = vi.fn();
        for (let index = 0; index < 21; index += 1) {
            subscribeToSSE({
                channel: `directory:${index}`,
                listeners: {},
                onResync,
            });
        }

        vi.advanceTimersByTime(0);
        vi.advanceTimersToNextTimer();

        const url = new URL(MockEventSource.instances.at(-1)?.url ?? "");
        expect(url.searchParams.getAll("topic")).toHaveLength(20);

        vi.advanceTimersByTime(30_000);
        expect(onResync).toHaveBeenCalledTimes(21);
    });

    it("routes the same event type only to its envelope channel", () => {
        const materialHandler = vi.fn();
        const directoryHandler = vi.fn();
        subscribeToSSE({
            channel: "material:123",
            listeners: { child_updated: materialHandler },
        });
        subscribeToSSE({
            channel: "directory:456",
            listeners: { child_updated: directoryHandler },
        });
        vi.runAllTimers();

        MockEventSource.instances[0].dispatchCustomEvent(
            "child_updated",
            JSON.stringify({ channel: "material:123", data: { id: "item" } }),
        );

        expect(materialHandler).toHaveBeenCalledOnce();
        expect(directoryHandler).not.toHaveBeenCalled();
        expect(materialHandler.mock.calls[0][0].data).toBe(JSON.stringify({ id: "item" }));
    });

    it("waits for cross-tab leadership before opening the master transport", async () => {
        const leadership: { acquire?: () => void } = {};
        const locks = {
            request: vi.fn(
                (_name: string, _options: LockOptions, callback: (lock: Lock) => Promise<void>) => {
                    leadership.acquire = () => {
                        void callback({} as Lock);
                    };
                    return new Promise<void>(() => {});
                },
            ),
        };
        Object.defineProperty(navigator, "locks", {
            configurable: true,
            value: locks,
        });
        vi.stubGlobal("BroadcastChannel", MockBroadcastChannel);

        subscribeToSSE({ channel: "notifications", listeners: {} });
        vi.advanceTimersByTime(0);

        expect(MockEventSource.instances).toHaveLength(0);
        expect(locks.request).toHaveBeenCalledOnce();

        const acquire = leadership.acquire;
        if (!acquire) throw new Error("leadership callback was not registered");
        acquire();
        await Promise.resolve();
        vi.advanceTimersByTime(0);

        expect(MockEventSource.instances).toHaveLength(1);
    });

});
