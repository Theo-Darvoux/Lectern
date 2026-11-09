import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
    closeAllSSEConnections,
    setSSEIdentity,
    subscribeToSSE as subscribeToSSEBase,
    type SSEOptions,
} from "./sse-client";

function subscribeToSSE(
    options: Omit<SSEOptions, "onResync"> & { onResync?: () => void },
) {
    return subscribeToSSEBase({ onResync: () => {}, ...options });
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

class MockEventSource {
    static instances: MockEventSource[] = [];

    url: string;
    withCredentials: boolean;
    onopen: ((e: Event) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    onmessage: ((e: MessageEvent) => void) | null = null;
    readyState = 0; // CONNECTING

    constructor(url: string, opts?: { withCredentials?: boolean }) {
        this.url = url;
        this.withCredentials = opts?.withCredentials ?? false;
        MockEventSource.instances.push(this);
    }

    open() {
        this.readyState = 1;
        this.onopen?.(new Event("open"));
    }

    fail() {
        this.onerror?.(new Event("error"));
    }

    deliver(channel: string, type: string, data: unknown = {}) {
        this.onmessage?.(
            new MessageEvent("message", { data: JSON.stringify({ channel, type, data }) }),
        );
    }

    close() {
        this.readyState = 2; // CLOSED
    }
}

class MockBroadcastChannel {
    static instances: MockBroadcastChannel[] = [];
    onmessage: ((event: MessageEvent) => void) | null = null;
    postMessage = vi.fn();
    close = vi.fn();

    constructor(public readonly name: string) {
        MockBroadcastChannel.instances.push(this);
    }

    receive(data: unknown) {
        this.onmessage?.(new MessageEvent("message", { data }));
    }
}

function sources() {
    return MockEventSource.instances;
}

function lastSource() {
    return sources().at(-1)!;
}

/** Flush the refresh debounce so pending subscription changes reach the transport. */
function flush() {
    vi.advanceTimersByTime(150);
}

function retryWindow() {
    // Base 1000ms retry plus at most 20% jitter.
    vi.advanceTimersByTime(1250);
}

const MATERIAL = "material:11111111-1111-4111-8111-111111111111" as const;
const DIRECTORY = "directory:22222222-2222-4222-8222-222222222222" as const;

vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test" }));

beforeEach(() => {
    MockEventSource.instances = [];
    MockBroadcastChannel.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    Object.defineProperty(navigator, "locks", {
        configurable: true,
        value: undefined,
    });
    vi.useFakeTimers();
    setSSEIdentity("user-1");
});

afterEach(() => {
    closeAllSSEConnections();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("transport", () => {
    it("opens one credentialed stream carrying the entity topics and a client id", () => {
        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();

        expect(sources()).toHaveLength(1);
        const url = new URL(lastSource().url);
        expect(url.pathname).toBe("/events/sse");
        expect(url.searchParams.getAll("topic")).toEqual([MATERIAL]);
        expect(url.searchParams.get("client")).toBeTruthy();
        expect(lastSource().withCredentials).toBe(true);
    });

    it("keeps one stream across logical channels", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        subscribeToSSE({ channel: "pull_requests", listeners: {} });
        subscribeToSSE({ channel: "uploads", listeners: {} });
        flush();

        expect(sources()).toHaveLength(1);
    });

    it("coalesces a burst of subscription changes into one stream", () => {
        const first = subscribeToSSE({ channel: MATERIAL, listeners: {} });
        first.close();
        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        subscribeToSSE({ channel: DIRECTORY, listeners: {} });
        flush();

        expect(sources()).toHaveLength(1);
        expect(new URL(lastSource().url).searchParams.getAll("topic")).toEqual([
            DIRECTORY,
            MATERIAL,
        ]);
    });

    it("waits for an account before connecting and closes when it signs out", () => {
        setSSEIdentity(null);
        subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();
        expect(sources()).toHaveLength(0);

        setSSEIdentity("user-1");
        flush();
        expect(sources()).toHaveLength(1);

        setSSEIdentity(null);
        expect(lastSource().readyState).toBe(2);
    });

    it("replaces the stream when the account changes", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();
        const first = lastSource();

        setSSEIdentity("user-2");
        flush();

        expect(first.readyState).toBe(2);
        expect(sources()).toHaveLength(2);
    });

    it("closes the stream with the last subscriber", () => {
        const connection = subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();

        connection.close();
        flush();

        expect(lastSource().readyState).toBe(2);
    });

    it("does not touch a subscription to a channel already carried", () => {
        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();
        lastSource().open();

        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();

        expect(sources()).toHaveLength(1);
        expect(lastSource().readyState).toBe(1);
    });
});

describe("topic changes", () => {
    it("keeps the old stream until the replacement opens", () => {
        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();
        const original = lastSource();
        original.open();

        subscribeToSSE({ channel: DIRECTORY, listeners: {} });
        flush();
        const replacement = lastSource();

        expect(replacement).not.toBe(original);
        expect(original.readyState).toBe(1);

        replacement.open();
        expect(original.readyState).toBe(2);
    });

    it("resyncs only channels the new stream gained", () => {
        const onMaterial = vi.fn();
        const onDirectory = vi.fn();
        subscribeToSSE({ channel: MATERIAL, listeners: {}, onResync: onMaterial });
        flush();
        lastSource().open();
        onMaterial.mockClear();

        subscribeToSSE({ channel: DIRECTORY, listeners: {}, onResync: onDirectory });
        flush();
        lastSource().open();

        expect(onDirectory).toHaveBeenCalledOnce();
        expect(onMaterial).not.toHaveBeenCalled();
    });

    it("does not resync when the old stream ends just before the replacement opens", () => {
        const onMaterial = vi.fn();
        subscribeToSSE({ channel: MATERIAL, listeners: {}, onResync: onMaterial });
        flush();
        const original = lastSource();
        original.open();
        onMaterial.mockClear();

        subscribeToSSE({ channel: DIRECTORY, listeners: {} });
        flush();
        original.fail(); // The server ends the old stream first.
        lastSource().open();

        expect(onMaterial).not.toHaveBeenCalled();
    });

    it("keeps the old stream when the replacement fails, then retries", () => {
        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();
        const original = lastSource();
        original.open();

        subscribeToSSE({ channel: DIRECTORY, listeners: {} });
        flush();
        lastSource().fail();
        expect(original.readyState).toBe(1);

        retryWindow();
        expect(sources()).toHaveLength(3);
    });
});

describe("topic changes that revert", () => {
    it("drops a pending replacement when the open stream already fits", () => {
        const directory = subscribeToSSE({ channel: DIRECTORY, listeners: {} });
        flush();
        const original = lastSource();
        original.open();

        const material = subscribeToSSE({ channel: MATERIAL, listeners: {} });
        flush();
        const pending = lastSource();

        material.close();
        flush();

        expect(pending.readyState).toBe(2);
        expect(original.readyState).toBe(1);
        expect(sources()).toHaveLength(2);
        directory.close();
    });
});

describe("reconnection", () => {
    it("reconnects after a dropped stream with backoff, not immediately", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();
        lastSource().open();
        lastSource().fail();

        expect(sources()).toHaveLength(1);
        retryWindow();
        expect(sources()).toHaveLength(2);
    });

    it("does not spend retries on new subscribers during backoff", () => {
        subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();
        lastSource().fail();

        subscribeToSSE({ channel: MATERIAL, listeners: {} });
        vi.advanceTimersByTime(200);
        expect(sources()).toHaveLength(1);

        retryWindow();
        expect(sources()).toHaveLength(2);
        expect(new URL(lastSource().url).searchParams.getAll("topic")).toEqual([MATERIAL]);
    });

    it("resyncs every channel after a real gap, once per reconnect", () => {
        const onResync = vi.fn();
        subscribeToSSE({ channel: "notifications", listeners: {}, onResync });
        flush();
        lastSource().open();
        expect(onResync).toHaveBeenCalledOnce();

        lastSource().fail();
        expect(onResync).toHaveBeenCalledOnce();

        retryWindow();
        lastSource().open();
        expect(onResync).toHaveBeenCalledTimes(2);
    });

    it("close() cancels a pending retry", () => {
        const connection = subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();
        lastSource().fail();
        connection.close();

        vi.advanceTimersByTime(5000);

        expect(sources()).toHaveLength(1);
    });
});

describe("routing", () => {
    it("routes the same event type only to its envelope channel", () => {
        const materialHandler = vi.fn();
        const directoryHandler = vi.fn();
        subscribeToSSE({ channel: MATERIAL, listeners: { child_updated: materialHandler } });
        subscribeToSSE({ channel: DIRECTORY, listeners: { child_updated: directoryHandler } });
        flush();

        lastSource().deliver(MATERIAL, "child_updated", { id: "item" });

        expect(materialHandler).toHaveBeenCalledOnce();
        expect(directoryHandler).not.toHaveBeenCalled();
        expect(materialHandler.mock.calls[0][0].data).toBe(JSON.stringify({ id: "item" }));
    });

    it("ignores malformed messages", () => {
        const handler = vi.fn();
        subscribeToSSE({ channel: "notifications", listeners: { notification: handler } });
        flush();

        lastSource().onmessage?.(new MessageEvent("message", { data: "not json" }));
        lastSource().onmessage?.(new MessageEvent("message", { data: JSON.stringify({ type: 1 }) }));

        expect(handler).not.toHaveBeenCalled();
    });
});

describe("topic budget", () => {
    function subscribeMany(count: number, onResync: () => void) {
        for (let index = 0; index < count; index += 1) {
            subscribeToSSE({
                channel: `directory:${String(index).padStart(2, "0")}`,
                listeners: {},
                onResync,
            });
        }
        flush();
    }

    it("stays within the budget and polls only channels it could not carry", () => {
        const onResync = vi.fn();
        subscribeMany(21, onResync);
        lastSource().open();
        onResync.mockClear();

        expect(new URL(lastSource().url).searchParams.getAll("topic")).toHaveLength(20);

        vi.advanceTimersByTime(30_000);
        expect(onResync).toHaveBeenCalledOnce();
    });

    it("stops polling once every channel fits", () => {
        const onResync = vi.fn();
        const extra = subscribeToSSE({ channel: "directory:zz", listeners: {}, onResync });
        subscribeMany(20, () => {});
        lastSource().open();

        extra.close();
        flush();
        onResync.mockClear();

        vi.advanceTimersByTime(60_000);
        expect(onResync).not.toHaveBeenCalled();
    });
});

describe("cross-tab coordination", () => {
    function enableCoordination() {
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
        Object.defineProperty(navigator, "locks", { configurable: true, value: locks });
        vi.stubGlobal("BroadcastChannel", MockBroadcastChannel);
        return {
            locks,
            acquire: async () => {
                leadership.acquire?.();
                await Promise.resolve();
            },
        };
    }

    function coordinator() {
        return MockBroadcastChannel.instances[0];
    }

    function posted(kind: string) {
        return coordinator().postMessage.mock.calls.filter(([message]) => message.kind === kind);
    }

    it("waits for leadership before opening the stream", async () => {
        const { locks, acquire } = enableCoordination();
        subscribeToSSE({ channel: "notifications", listeners: {} });
        flush();

        expect(sources()).toHaveLength(0);
        expect(locks.request).toHaveBeenCalledOnce();

        await acquire();
        flush();
        expect(sources()).toHaveLength(1);
    });

    it("a follower routes forwarded events and never connects", () => {
        enableCoordination();
        const handler = vi.fn();
        subscribeToSSE({ channel: "notifications", listeners: { notification: handler } });
        flush();

        coordinator().receive({
            kind: "event",
            sender: "leader",
            payload: JSON.stringify({ channel: "notifications", type: "notification", data: {} }),
        });

        expect(handler).toHaveBeenCalledOnce();
        expect(sources()).toHaveLength(0);
    });

    it("the leader includes peer channels and forwards events", async () => {
        const { acquire } = enableCoordination();
        subscribeToSSE({ channel: "notifications", listeners: {} });
        await acquire();
        coordinator().receive({ kind: "snapshot", sender: "peer", channels: [MATERIAL] });
        flush();

        expect(new URL(lastSource().url).searchParams.getAll("topic")).toEqual([MATERIAL]);

        lastSource().deliver(MATERIAL, "material_updated");
        expect(posted("event")).toHaveLength(1);
    });

    it("drops a leaving peer's channels", async () => {
        const { acquire } = enableCoordination();
        subscribeToSSE({ channel: "notifications", listeners: {} });
        await acquire();
        coordinator().receive({ kind: "snapshot", sender: "peer", channels: [MATERIAL] });
        flush();

        coordinator().receive({ kind: "leaving", sender: "peer" });
        flush();

        expect(new URL(lastSource().url).searchParams.getAll("topic")).toEqual([]);
    });

    it("keeps a quiet peer through background timer throttling", async () => {
        const { acquire } = enableCoordination();
        subscribeToSSE({ channel: "notifications", listeners: {} });
        await acquire();
        coordinator().receive({ kind: "snapshot", sender: "peer", channels: [MATERIAL] });
        flush();
        const stream = lastSource();

        vi.advanceTimersByTime(60_000);

        expect(lastSource()).toBe(stream);
    });

    it("forwards a resync to followers with its channels", async () => {
        const { acquire } = enableCoordination();
        subscribeToSSE({ channel: "notifications", listeners: {} });
        await acquire();
        flush();

        lastSource().open();

        expect(posted("resync")).toEqual([
            [{ kind: "resync", sender: expect.any(String), channels: ["notifications"] }],
        ]);
    });
});
