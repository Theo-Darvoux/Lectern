import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createSSEConnection } from "./sse-client";

// ---------------------------------------------------------------------------
// EventSource mock
// ---------------------------------------------------------------------------

class MockEventSource {
    static instances: MockEventSource[] = [];

    url: string;
    withCredentials: boolean;
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

    dispatchCustomEvent(type: string) {
        const listeners = this.handlers[type] ?? [];
        const event = new Event(type);
        for (const l of listeners) {
            if (typeof l === "function") l(event);
            else l.handleEvent(event);
        }
    }

    close() {
        this.readyState = 2; // CLOSED
    }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test" }));

beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("createSSEConnection", () => {
    it("opens an EventSource to the full URL", () => {
        createSSEConnection({ url: "/materials/123/sse", listeners: {} });
        vi.runAllTimers();
        expect(MockEventSource.instances).toHaveLength(1);
        expect(MockEventSource.instances[0].url).toBe("http://api.test/materials/123/sse");
    });

    it("uses a literal URL when it starts with http", () => {
        createSSEConnection({ url: "http://other.host/sse", listeners: {} });
        vi.runAllTimers();
        expect(MockEventSource.instances[0].url).toBe("http://other.host/sse");
    });

    it("registers provided event listeners", () => {
        const handler = vi.fn();
        createSSEConnection({
            url: "/test/sse",
            listeners: { material_deleted: handler },
        });
        vi.runAllTimers();
        MockEventSource.instances[0].dispatchCustomEvent("material_deleted");
        expect(handler).toHaveBeenCalledOnce();
    });

    it("registers multiple listeners independently", () => {
        const onDelete = vi.fn();
        const onChild = vi.fn();
        createSSEConnection({
            url: "/directories/abc/sse",
            listeners: { directory_deleted: onDelete, child_added: onChild },
        });
        vi.runAllTimers();
        const es = MockEventSource.instances[0];
        es.dispatchCustomEvent("directory_deleted");
        es.dispatchCustomEvent("child_added");
        expect(onDelete).toHaveBeenCalledOnce();
        expect(onChild).toHaveBeenCalledOnce();
    });

    it("respects startupDelay before connecting", () => {
        createSSEConnection({ url: "/test/sse", listeners: {}, startupDelay: 100 });
        expect(MockEventSource.instances).toHaveLength(0);
        vi.advanceTimersByTime(99);
        expect(MockEventSource.instances).toHaveLength(0);
        vi.advanceTimersByTime(1);
        expect(MockEventSource.instances).toHaveLength(1);
    });

    it("reconnects after onerror with reconnectDelay", () => {
        createSSEConnection({ url: "/test/sse", listeners: {}, reconnectDelay: 500 });
        vi.runAllTimers();
        expect(MockEventSource.instances).toHaveLength(1);

        MockEventSource.instances[0].onerror?.(new Event("error"));
        expect(MockEventSource.instances).toHaveLength(1); // no immediate reconnect

        vi.advanceTimersByTime(500);
        expect(MockEventSource.instances).toHaveLength(2);
    });

    it("close() stops reconnection after error", () => {
        const conn = createSSEConnection({
            url: "/test/sse",
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
        const conn = createSSEConnection({
            url: "/test/sse",
            listeners: {},
            startupDelay: 200,
        });
        conn.close();
        vi.advanceTimersByTime(500);
        expect(MockEventSource.instances).toHaveLength(0);
    });

    it("close() closes the underlying EventSource", () => {
        const conn = createSSEConnection({ url: "/test/sse", listeners: {} });
        vi.runAllTimers();
        const es = MockEventSource.instances[0];
        conn.close();
        expect(es.readyState).toBe(2); // CLOSED
    });
});
