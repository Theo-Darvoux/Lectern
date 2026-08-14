"use client";

import { create } from "zustand";
import type { QueueItem } from "./upload-queue";

export interface UploadTelemetry {
    progress?: number;
    processingStatus?: string;
    stageIndex?: number;
    stageTotal?: number;
}

type TelemetryById = Readonly<Record<string, UploadTelemetry>>;

interface UploadTelemetryState {
    byId: TelemetryById;
}

export const useUploadTelemetry = create<UploadTelemetryState>()(() => ({
    byId: {},
}));

const pending = new Map<string, UploadTelemetry>();
let scheduledFrame: number | ReturnType<typeof setTimeout> | null = null;
let scheduledWithAnimationFrame = false;

function flushTelemetry(): void {
    scheduledFrame = null;
    if (pending.size === 0) return;

    const updates = new Map(pending);
    pending.clear();
    useUploadTelemetry.setState((state) => {
        const byId = { ...state.byId };
        for (const [clientId, patch] of updates) {
            byId[clientId] = { ...byId[clientId], ...patch };
        }
        return { byId };
    });
}

function scheduleFlush(): void {
    if (scheduledFrame !== null) return;

    if (typeof requestAnimationFrame === "function") {
        scheduledWithAnimationFrame = true;
        scheduledFrame = requestAnimationFrame(flushTelemetry);
        return;
    }

    scheduledWithAnimationFrame = false;
    scheduledFrame = setTimeout(flushTelemetry, 16);
}

/** Coalesces hot upload callbacks to at most one state update per display frame. */
export function updateUploadTelemetry(clientId: string, patch: UploadTelemetry): void {
    pending.set(clientId, { ...pending.get(clientId), ...patch });
    scheduleFlush();
}

export function clearUploadTelemetry(clientId: string): void {
    pending.delete(clientId);
    useUploadTelemetry.setState((state) => {
        if (!(clientId in state.byId)) return state;
        const byId = { ...state.byId };
        delete byId[clientId];
        return { byId };
    });
}

export function clearAllUploadTelemetry(): void {
    pending.clear();
    if (scheduledFrame !== null) {
        if (scheduledWithAnimationFrame && typeof cancelAnimationFrame === "function") {
            cancelAnimationFrame(scheduledFrame as number);
        } else {
            clearTimeout(scheduledFrame as ReturnType<typeof setTimeout>);
        }
        scheduledFrame = null;
    }
    useUploadTelemetry.setState({ byId: {} });
}

export function mergeUploadTelemetry(
    items: readonly QueueItem[],
    byId: TelemetryById,
): QueueItem[] {
    return items.map((item) => {
        const telemetry = byId[item.clientId];
        return telemetry ? { ...item, ...telemetry } : item;
    });
}
