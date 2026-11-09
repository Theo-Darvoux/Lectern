import { API_BASE } from "@/lib/api-client";

export type SSEChannel =
    | "notifications"
    | "pull_requests"
    | "uploads"
    | `material:${string}`
    | `directory:${string}`;

export interface SSEOptions {
    /** Logical source routed over the browser's single transport. */
    channel: SSEChannel;
    /** Map of event types to handlers. */
    listeners: Record<string, (event: MessageEvent) => void>;
    /** Reconcile authoritative state after a possible delivery gap. */
    onResync: () => void;
}

export interface SSESubscription {
    close: () => void;
}

interface Transport {
    source: EventSource;
    url: string;
    channels: Set<SSEChannel>;
}

interface PeerSnapshot {
    channels: SSEChannel[];
    lastSeen: number;
}

type CoordinatorMessage =
    | { kind: "snapshot"; sender: string; channels: SSEChannel[] }
    | { kind: "snapshot_request"; sender: string }
    | { kind: "leaving"; sender: string }
    | { kind: "event"; sender: string; payload: string }
    | { kind: "resync"; sender: string; channels: SSEChannel[] };

const COORDINATOR_NAME = "lectern-master-sse-v2";
const HEARTBEAT_MS = 10_000;
// Browsers may run a hidden tab's timers only once a minute. Peers must outlive
// that throttling, or each background tab would churn the leader's transport.
const PEER_STALE_MS = 150_000;
// Coalesces bursts of subscription changes (React Strict Mode, tree re-renders)
// into one transport change.
const REFRESH_DEBOUNCE_MS = 100;
// Keep this aligned with the API's topic budget. Excess channels remain
// subscribed and reconcile from authoritative APIs on a timer.
const MAX_ENTITY_TOPICS = 20;
const OVERFLOW_RESYNC_MS = 30_000;
const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;

// Identifies this browser's stream to the server. Leadership decides which
// tab's id is in use, so reconnecting from the same tab replaces its old stream.
const tabId = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);

const subscribers = new Set<SSEOptions>();
const peers = new Map<string, PeerSnapshot>();
let identity: string | null = null;
let openTransport: Transport | null = null;
let nextTransport: Transport | null = null;
let liveChannels = new Set<SSEChannel>();
let connectTimer: ReturnType<typeof setTimeout> | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let retryAttempt = 0;
let overflowTimer: ReturnType<typeof setInterval> | null = null;
let coordinator: BroadcastChannel | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let releaseLeadership: (() => void) | null = null;
let coordinatorGeneration = 0;
let coordinatorStarted = false;
let isLeader = true;

function post(message: CoordinatorMessage): void {
    coordinator?.postMessage(message);
}

function localChannels(): SSEChannel[] {
    return [...new Set([...subscribers].map((subscriber) => subscriber.channel))];
}

function postSnapshot(): void {
    post({ kind: "snapshot", sender: tabId, channels: localChannels() });
}

function isEntityChannel(channel: SSEChannel): boolean {
    return channel.startsWith("material:") || channel.startsWith("directory:");
}

function desiredChannels(): Set<SSEChannel> {
    const channels = new Set(localChannels());
    if (isLeader) {
        for (const peer of peers.values()) {
            for (const channel of peer.channels) channels.add(channel);
        }
    }
    return channels;
}

/** The subset of `desired` the transport can carry, chosen deterministically. */
function coveredChannels(desired: Set<SSEChannel>): Set<SSEChannel> {
    const kept = [...desired].filter(isEntityChannel).sort().slice(0, MAX_ENTITY_TOPICS);
    return new Set([...desired].filter((channel) => !isEntityChannel(channel)).concat(kept));
}

function transportUrl(channels: Set<SSEChannel>): string {
    const params = new URLSearchParams({ client: tabId });
    for (const channel of [...channels].filter(isEntityChannel).sort()) {
        params.append("topic", channel);
    }
    return `${API_BASE}/events/sse?${params}`;
}

function routeEvent(raw: string): void {
    let message: { channel?: unknown; type?: unknown; data?: unknown };
    try {
        message = JSON.parse(raw);
    } catch {
        return;
    }
    const { channel, type, data } = message;
    if (typeof channel !== "string" || typeof type !== "string") return;

    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const subscriber of subscribers) {
        if (subscriber.channel === channel) subscriber.listeners[type]?.(event);
    }
}

function resyncLocal(channels: SSEChannel[]): void {
    const affected = new Set(channels);
    for (const subscriber of subscribers) {
        if (affected.has(subscriber.channel)) subscriber.onResync();
    }
}

function resync(channels: SSEChannel[]): void {
    if (channels.length === 0) return;
    resyncLocal(channels);
    post({ kind: "resync", sender: tabId, channels });
}

function closeTransports(): void {
    openTransport?.source.close();
    nextTransport?.source.close();
    openTransport = null;
    nextTransport = null;
    liveChannels = new Set();
    retryAttempt = 0;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
}

function scheduleRetry(): void {
    if (retryTimer) return;
    const backoff = Math.min(RETRY_BASE_MS * 2 ** retryAttempt, RETRY_MAX_MS);
    // Jitter keeps a server restart from synchronizing every browser.
    const delay = backoff + Math.random() * backoff * 0.2;
    retryAttempt += 1;
    retryTimer = setTimeout(() => {
        retryTimer = null;
        connect();
    }, delay);
}

function connect(): void {
    const desired = desiredChannels();
    if (!isLeader || !identity || desired.size === 0) {
        closeTransports();
        return;
    }

    const channels = coveredChannels(desired);
    const url = transportUrl(channels);
    if (nextTransport?.url === url) return;
    nextTransport?.source.close();
    nextTransport = null;
    if (openTransport?.url === url) return;

    const source = new EventSource(url, { withCredentials: true });
    const transport: Transport = { source, url, channels };
    nextTransport = transport;

    source.onmessage = (event) => {
        routeEvent(event.data);
        post({ kind: "event", sender: tabId, payload: event.data });
    };

    source.onopen = () => {
        if (nextTransport !== transport) return;
        // Only now retire the old stream, so a topic change leaves no gap on
        // channels both streams carry and those need no reconciliation.
        openTransport?.source.close();
        openTransport = transport;
        nextTransport = null;
        retryAttempt = 0;
        const gained = [...channels].filter((channel) => !liveChannels.has(channel));
        liveChannels = new Set(channels);
        resync(gained);
    };

    source.onerror = () => {
        source.close();
        if (nextTransport === transport) nextTransport = null;
        else if (openTransport === transport) openTransport = null;
        else return;
        if (!openTransport && !nextTransport) liveChannels = new Set();
        if (!nextTransport) scheduleRetry();
    };
}

function syncOverflowPolling(): void {
    const uncovered = (): SSEChannel[] => {
        const desired = desiredChannels();
        const covered = coveredChannels(desired);
        return [...desired].filter((channel) => !covered.has(channel));
    };

    if (isLeader && identity && uncovered().length > 0) {
        overflowTimer ??= setInterval(() => resync(uncovered()), OVERFLOW_RESYNC_MS);
    } else if (overflowTimer) {
        clearInterval(overflowTimer);
        overflowTimer = null;
    }
}

function scheduleRefresh(): void {
    syncOverflowPolling();
    if (!isLeader || retryTimer) return;
    if (connectTimer) clearTimeout(connectTimer);
    connectTimer = setTimeout(() => {
        connectTimer = null;
        connect();
    }, REFRESH_DEBOUNCE_MS);
}

function handleCoordinatorMessage(event: MessageEvent<CoordinatorMessage>): void {
    const message = event.data;
    if (!message || message.sender === tabId) return;

    switch (message.kind) {
        case "snapshot_request":
            postSnapshot();
            break;
        case "event":
            if (!isLeader) routeEvent(message.payload);
            break;
        case "resync":
            if (!isLeader) resyncLocal(message.channels);
            break;
        case "leaving":
            if (peers.delete(message.sender) && isLeader) scheduleRefresh();
            break;
        case "snapshot":
            peers.set(message.sender, { channels: message.channels, lastSeen: Date.now() });
            if (isLeader) scheduleRefresh();
            break;
    }
}

function pruneStalePeers(): void {
    const staleBefore = Date.now() - PEER_STALE_MS;
    let changed = false;
    for (const [peerId, peer] of peers) {
        if (peer.lastSeen >= staleBefore) continue;
        peers.delete(peerId);
        changed = true;
    }
    if (changed) scheduleRefresh();
}

function announceLeaving(): void {
    post({ kind: "leaving", sender: tabId });
}

function announcePresence(event: PageTransitionEvent): void {
    if (event.persisted) postSnapshot();
}

function stopCoordinator(): void {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    if (typeof window !== "undefined") {
        window.removeEventListener("pagehide", announceLeaving);
        window.removeEventListener("pageshow", announcePresence);
    }
    coordinator?.close();
    coordinator = null;
    peers.clear();
}

function ensureCoordinator(): void {
    if (coordinatorStarted || typeof window === "undefined") return;
    coordinatorStarted = true;
    if (typeof BroadcastChannel === "undefined" || !navigator.locks) return;

    try {
        coordinator = new BroadcastChannel(COORDINATOR_NAME);
    } catch {
        return;
    }
    isLeader = false;
    coordinator.onmessage = handleCoordinatorMessage;
    window.addEventListener("pagehide", announceLeaving);
    window.addEventListener("pageshow", announcePresence);
    heartbeatTimer = setInterval(() => {
        postSnapshot();
        if (isLeader) pruneStalePeers();
    }, HEARTBEAT_MS);

    const generation = ++coordinatorGeneration;
    void navigator.locks
        .request(COORDINATOR_NAME, { mode: "exclusive" }, async () => {
            if (generation !== coordinatorGeneration) return;
            isLeader = true;
            post({ kind: "snapshot_request", sender: tabId });
            scheduleRefresh();
            await new Promise<void>((resolve) => {
                releaseLeadership = resolve;
            });
        })
        .catch(() => {
            if (generation !== coordinatorGeneration) return;
            stopCoordinator();
            isLeader = true;
            scheduleRefresh();
        });
}

/**
 * Bind the transport to the signed-in account. A stream authenticates once, so
 * it must be replaced whenever the account behind the browser's cookie changes.
 */
export function setSSEIdentity(userId: string | null): void {
    if (userId === identity) return;
    identity = userId;
    closeTransports();
    scheduleRefresh();
}

/** Close the transport and remove every logical subscription. */
export function closeAllSSEConnections(): void {
    if (connectTimer) clearTimeout(connectTimer);
    connectTimer = null;
    closeTransports();
    subscribers.clear();
    identity = null;
    if (overflowTimer) clearInterval(overflowTimer);
    overflowTimer = null;
    stopCoordinator();
    coordinatorGeneration += 1;
    releaseLeadership?.();
    releaseLeadership = null;
    coordinatorStarted = false;
    isLeader = true;
}

/**
 * Register a logical live-update subscription on the browser's single stream.
 * Callers own only their handler lifetime; transport ownership stays here.
 */
export function subscribeToSSE(options: SSEOptions): SSESubscription {
    ensureCoordinator();
    const subscriber = { ...options };
    subscribers.add(subscriber);
    postSnapshot();
    scheduleRefresh();

    return {
        close() {
            if (!subscribers.delete(subscriber)) return;
            postSnapshot();
            scheduleRefresh();
        },
    };
}
