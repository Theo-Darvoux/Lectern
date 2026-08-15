import { API_BASE } from "@/lib/api-client";

export type SSEChannel =
    | "notifications"
    | "pull_requests"
    | `material:${string}`
    | `directory:${string}`;

export interface SSEOptions {
    /** Logical source routed over the singleton master transport. */
    channel: SSEChannel;
    /** Map of event names to handlers. */
    listeners: Record<string, (event: MessageEvent) => void>;
    /** Base reconnect delay in ms (default: 1000). */
    reconnectDelay?: number;
    /** Startup delay in ms to absorb React Strict Mode mounts (default: 0). */
    startupDelay?: number;
    /** Reconcile authoritative state after a transport gap or queue overflow. */
    onResync: () => void;
}

export interface SSESubscription {
    close: () => void;
}

interface LogicalSubscriber {
    channel: SSEChannel;
    listeners: Map<string, (event: MessageEvent) => void>;
    reconnectDelay: number;
    onResync: () => void;
}

interface MasterEnvelope {
    channel: string;
    data: unknown;
}

interface PeerSnapshot {
    channels: SSEChannel[];
    eventNames: string[];
    lastSeen: number;
}

type CoordinatorMessage =
    | { kind: "snapshot"; sender: string; channels: SSEChannel[]; eventNames: string[] }
    | { kind: "snapshot_request"; sender: string }
    | { kind: "event"; sender: string; eventType: string; data: string; lastEventId: string }
    | { kind: "resync"; sender: string };

const COORDINATOR_NAME = "lectern-master-sse-v1";
const PEER_STALE_MS = 30_000;
// Keep this aligned with the API's URL topic budget. Excess logical channels
// remain subscribed locally and reconcile from authoritative APIs on a timer.
const MAX_MASTER_TOPICS = 20;
const OVERFLOW_RESYNC_MS = 30_000;
const tabId = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);

const subscribers = new Set<LogicalSubscriber>();
const peerSnapshots = new Map<string, PeerSnapshot>();
let eventSource: EventSource | null = null;
let connectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempt = 0;
let connectedUrl: string | null = null;
const attachedEventNames = new Set<string>();
let coordinator: BroadcastChannel | null = null;
let coordinatorHeartbeat: ReturnType<typeof setInterval> | null = null;
let overflowResyncTimer: ReturnType<typeof setInterval> | null = null;
let releaseLeadership: (() => void) | null = null;
let coordinatorGeneration = 0;
let coordinatorStarted = false;
let isTransportLeader = true;

function localSnapshot(): PeerSnapshot {
    const channels = [...new Set([...subscribers].map((subscriber) => subscriber.channel))];
    const names = new Set<string>();
    for (const subscriber of subscribers) {
        for (const name of subscriber.listeners.keys()) names.add(name);
    }
    return { channels, eventNames: [...names], lastSeen: Date.now() };
}

function postSnapshot(): void {
    const snapshot = localSnapshot();
    coordinator?.postMessage({
        kind: "snapshot",
        sender: tabId,
        channels: snapshot.channels,
        eventNames: snapshot.eventNames,
    } satisfies CoordinatorMessage);
}

function handleCoordinatorMessage(event: MessageEvent<CoordinatorMessage>): void {
    const message = event.data;
    if (!message || message.sender === tabId) return;

    if (message.kind === "snapshot_request") {
        postSnapshot();
        return;
    }
    if (message.kind === "event") {
        if (!isTransportLeader) {
            dispatchEvent(
                new MessageEvent(message.eventType, {
                    data: message.data,
                    lastEventId: message.lastEventId,
                }),
                false,
            );
        }
        return;
    }
    if (message.kind === "resync") {
        if (!isTransportLeader) resyncLocal();
        return;
    }

    peerSnapshots.set(message.sender, {
        channels: message.channels,
        eventNames: message.eventNames,
        lastSeen: Date.now(),
    });
    if (isTransportLeader) scheduleConnectionRefresh();
}

function ensureCoordinator(): void {
    if (coordinatorStarted || typeof window === "undefined") return;
    coordinatorStarted = true;

    if (typeof BroadcastChannel === "undefined" || !navigator.locks) {
        isTransportLeader = true;
        return;
    }

    isTransportLeader = false;
    try {
        coordinator = new BroadcastChannel(COORDINATOR_NAME);
    } catch {
        isTransportLeader = true;
        return;
    }
    coordinator.onmessage = handleCoordinatorMessage;
    const generation = ++coordinatorGeneration;

    coordinatorHeartbeat = setInterval(() => {
        postSnapshot();
        if (!isTransportLeader) return;
        const staleBefore = Date.now() - PEER_STALE_MS;
        let changed = false;
        for (const [peerId, snapshot] of peerSnapshots) {
            if (snapshot.lastSeen >= staleBefore) continue;
            peerSnapshots.delete(peerId);
            changed = true;
        }
        if (changed) scheduleConnectionRefresh();
    }, 10_000);

    void navigator.locks
        .request(COORDINATOR_NAME, { mode: "exclusive" }, async () => {
            if (generation !== coordinatorGeneration) return;
            isTransportLeader = true;
            coordinator?.postMessage({
                kind: "snapshot_request",
                sender: tabId,
            } satisfies CoordinatorMessage);
            scheduleConnectionRefresh();
            await new Promise<void>((resolve) => {
                releaseLeadership = resolve;
            });
            releaseLeadership = null;
            isTransportLeader = false;
            eventSource?.close();
            eventSource = null;
            connectedUrl = null;
            attachedEventNames.clear();
        })
        .catch(() => {
            if (generation !== coordinatorGeneration) return;
            if (coordinatorHeartbeat) clearInterval(coordinatorHeartbeat);
            coordinatorHeartbeat = null;
            coordinator?.close();
            coordinator = null;
            peerSnapshots.clear();
            isTransportLeader = true;
            scheduleConnectionRefresh();
        });

    postSnapshot();
}

function aggregateChannels(): Set<SSEChannel> {
    const channels = new Set<SSEChannel>(localSnapshot().channels);
    if (isTransportLeader) {
        for (const snapshot of peerSnapshots.values()) {
            for (const channel of snapshot.channels) channels.add(channel);
        }
    }
    return channels;
}

function masterUrl(): string {
    const params = new URLSearchParams();
    const topics = new Set<string>();

    for (const channel of aggregateChannels()) {
        if (
            channel.startsWith("material:") ||
            channel.startsWith("directory:")
        ) {
            if (topics.size < MAX_MASTER_TOPICS) topics.add(channel);
        }
    }

    for (const topic of [...topics].sort()) params.append("topic", topic);
    const query = params.toString();
    return `${API_BASE}/events/sse${query ? `?${query}` : ""}`;
}

function syncOverflowResync(): void {
    const entityTopicCount = [...aggregateChannels()].filter(
        (channel) => channel.startsWith("material:") || channel.startsWith("directory:"),
    ).length;
    if (entityTopicCount > MAX_MASTER_TOPICS && !overflowResyncTimer) {
        overflowResyncTimer = setInterval(resyncAll, OVERFLOW_RESYNC_MS);
    } else if (entityTopicCount <= MAX_MASTER_TOPICS && overflowResyncTimer) {
        clearInterval(overflowResyncTimer);
        overflowResyncTimer = null;
    }
}

function reconnectBaseDelay(): number {
    let delay = 1000;
    for (const subscriber of subscribers) {
        delay = Math.min(delay, subscriber.reconnectDelay);
    }
    return delay;
}

function dispatchEvent(event: MessageEvent, broadcast = true): void {
    let channel: string | null = null;
    let data = event.data;

    try {
        const envelope = JSON.parse(event.data) as MasterEnvelope;
        if (typeof envelope.channel === "string" && "data" in envelope) {
            channel = envelope.channel;
            data = JSON.stringify(envelope.data);
        }
    } catch {
        return;
    }
    if (channel === null) return;

    const routed = new MessageEvent(event.type, {
        data,
        lastEventId: event.lastEventId,
        origin: event.origin,
    });

    for (const subscriber of subscribers) {
        if (subscriber.channel !== channel) continue;
        subscriber.listeners.get(event.type)?.(routed);
    }

    if (broadcast && isTransportLeader) {
        coordinator?.postMessage({
            kind: "event",
            sender: tabId,
            eventType: event.type,
            data: event.data,
            lastEventId: event.lastEventId,
        } satisfies CoordinatorMessage);
    }
}

function resyncLocal(): void {
    for (const subscriber of subscribers) subscriber.onResync();
}

function resyncAll(): void {
    resyncLocal();
    if (isTransportLeader) {
        coordinator?.postMessage({ kind: "resync", sender: tabId } satisfies CoordinatorMessage);
    }
}

function eventNames(): Set<string> {
    const names = new Set<string>();
    for (const subscriber of subscribers) {
        for (const name of subscriber.listeners.keys()) names.add(name);
    }
    if (isTransportLeader) {
        for (const snapshot of peerSnapshots.values()) {
            for (const name of snapshot.eventNames) names.add(name);
        }
    }
    return names;
}

function connect(): void {
    connectTimer = null;
    if (eventSource || !isTransportLeader || aggregateChannels().size === 0) return;

    const url = masterUrl();
    const source = new EventSource(url, { withCredentials: true });
    eventSource = source;
    connectedUrl = url;

    for (const name of eventNames()) {
        source.addEventListener(name, dispatchEvent as EventListener);
        attachedEventNames.add(name);
    }
    source.addEventListener("resync_required", resyncAll as EventListener);

    source.onopen = () => {
        if (eventSource !== source) return;
        // A newly elected tab may have missed best-effort pub/sub events while
        // leadership changed, so every successful transport open reconciles
        // authoritative state before resuming incremental delivery.
        reconnectAttempt = 0;
        resyncAll();
    };

    source.onerror = () => {
        if (eventSource !== source) return;
        source.close();
        eventSource = null;
        connectedUrl = null;
        attachedEventNames.clear();
        if (aggregateChannels().size === 0) return;

        // In browsers without reliable cross-tab coordination, the server's
        // distributed lease rejects follower transports. Reconcile on each
        // failed attempt so those tabs degrade to bounded polling, not stale UI.
        resyncAll();

        const base = reconnectBaseDelay();
        const exponent = Math.min(reconnectAttempt, 5);
        const backoff = base * 2 ** exponent;
        // Keep the first retry deterministic and add bounded jitter thereafter
        // so a server restart does not synchronize every connected browser.
        const jitter = reconnectAttempt === 0 ? 0 : Math.random() * backoff * 0.2;
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, Math.min(backoff + jitter, 30_000));
    };
}

function scheduleConnectionRefresh(): void {
    postSnapshot();
    syncOverflowResync();
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    if (connectTimer) {
        clearTimeout(connectTimer);
        connectTimer = null;
    }

    if (!isTransportLeader || aggregateChannels().size === 0) {
        eventSource?.close();
        eventSource = null;
        connectedUrl = null;
        attachedEventNames.clear();
        return;
    }

    const desiredUrl = masterUrl();
    if (eventSource && connectedUrl === desiredUrl) {
        for (const name of eventNames()) {
            if (attachedEventNames.has(name)) continue;
            eventSource.addEventListener(name, dispatchEvent as EventListener);
            attachedEventNames.add(name);
        }
        return;
    }

    if (eventSource) {
        eventSource.close();
        eventSource = null;
        connectedUrl = null;
        attachedEventNames.clear();
    }

    connectTimer = setTimeout(connect, 0);
}

/** Close the singleton transport and remove every logical subscription. */
export function closeAllSSEConnections(): void {
    if (connectTimer) clearTimeout(connectTimer);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    connectTimer = null;
    reconnectTimer = null;
    eventSource?.close();
    eventSource = null;
    connectedUrl = null;
    attachedEventNames.clear();
    subscribers.clear();
    peerSnapshots.clear();
    reconnectAttempt = 0;
    if (overflowResyncTimer) clearInterval(overflowResyncTimer);
    overflowResyncTimer = null;
    if (coordinatorHeartbeat) clearInterval(coordinatorHeartbeat);
    coordinatorHeartbeat = null;
    coordinator?.close();
    coordinator = null;
    coordinatorGeneration += 1;
    releaseLeadership?.();
    releaseLeadership = null;
    coordinatorStarted = false;
    isTransportLeader = true;
}

/**
 * Register a logical live-update subscription on the singleton master stream.
 * Callers own only their handler lifetime; transport ownership stays here.
 */
export function subscribeToSSE(options: SSEOptions): SSESubscription {
    const {
        channel,
        listeners,
        reconnectDelay = 1000,
        startupDelay = 0,
        onResync,
    } = options;

    const subscriber: LogicalSubscriber = {
        channel,
        listeners: new Map(Object.entries(listeners)),
        reconnectDelay,
        onResync,
    };
    let active = true;

    const startTimer = setTimeout(() => {
        if (!active) return;
        ensureCoordinator();
        subscribers.add(subscriber);
        scheduleConnectionRefresh();
    }, startupDelay);

    return {
        close() {
            if (!active) return;
            active = false;
            clearTimeout(startTimer);
            if (subscribers.delete(subscriber)) scheduleConnectionRefresh();
        },
    };
}
