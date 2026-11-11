/**
 * Enhanced localStorage wrapper that:
 * 1. Catches QuotaExceededError and warns the user (O10).
 * 2. Provides a way to sync state across tabs (O11).
 */

const QUOTA_NOTICE_INTERVAL_MS = 10_000;
const quotaListeners = new Set<() => void>();
let lastQuotaNotice = 0;

/** Subscribe to "local storage is full" events, throttled; returns the unsubscribe. */
export function onStorageFull(listener: () => void): () => void {
    quotaListeners.add(listener);
    return () => quotaListeners.delete(listener);
}

function reportStorageFull(): void {
    if (Date.now() - lastQuotaNotice < QUOTA_NOTICE_INTERVAL_MS) return;
    lastQuotaNotice = Date.now();
    for (const listener of quotaListeners) listener();
}

export const safeLocalStorage = {
    getItem: (name: string): string | null => {
        try {
            return localStorage.getItem(name);
        } catch {
            return null;
        }
    },
    setItem: (name: string, value: string): void => {
        try {
            localStorage.setItem(name, value);
        } catch (err) {
            if (err instanceof DOMException && (
                err.name === "QuotaExceededError" || 
                err.name === "NS_ERROR_DOM_QUOTA_REACHED"
            )) {
                console.error("Storage quota exceeded!", err);
                reportStorageFull();
            }
        }
    },
    removeItem: (name: string): void => {
        try {
            localStorage.removeItem(name);
        } catch {
            // ignore
        }
    }
};

/** Per-tab storage: a queue's File objects never outlive their tab either. */
export const safeSessionStorage = {
    getItem: (name: string): string | null => {
        try {
            return sessionStorage.getItem(name);
        } catch {
            return null;
        }
    },
    setItem: (name: string, value: string): void => {
        try {
            sessionStorage.setItem(name, value);
        } catch {
            // The queue still works for this page; it just will not survive a reload.
        }
    },
    removeItem: (name: string): void => {
        try {
            sessionStorage.removeItem(name);
        } catch {
            // ignore
        }
    },
};
