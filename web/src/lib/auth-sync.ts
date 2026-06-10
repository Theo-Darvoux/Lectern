import { lockedRefresh, registerTokenRefreshCallback, apiFetch, isRetriableError } from "./api-client";
import { clearAccessToken, decodeToken, getAccessToken, hasAuthHint, setAccessToken } from "./auth-tokens";
import { useAuthStore } from "./stores";
import type { UserBrief } from "./guest";

type AuthMessage =
    | { type: "TOKEN_REFRESHED"; token: string }
    | { type: "TOKEN_ACQUIRED"; token: string }
    | { type: "LOGOUT" };

let channel: BroadcastChannel | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let initialized = false;

export function initAuthSync(): () => void {
    if (typeof window === "undefined") return () => {};
    if (initialized) return () => {};

    channel = new BroadcastChannel("lectern_auth");
    channel.onmessage = handleMessage;

    document.addEventListener("visibilitychange", handleVisibility);

    registerTokenRefreshCallback((token: string) => {
        broadcastTokenRefreshed(token);
        scheduleRefreshTimer(token);
    });

    initialized = true;

    return () => {
        if (channel) {
            channel.close();
            channel = null;
        }
        document.removeEventListener("visibilitychange", handleVisibility);
        clearRefreshTimer();
        initialized = false;
    };
}

async function handleMessage(event: MessageEvent<AuthMessage>) {
    if (typeof window === "undefined") return;

    const data = event.data;

    switch (data.type) {
        case "TOKEN_REFRESHED":
            setAccessToken(data.token);
            scheduleRefreshTimer(data.token);
            break;
        case "TOKEN_ACQUIRED":
            setAccessToken(data.token);
            scheduleRefreshTimer(data.token);
            try {
                // Fetch user data directly using apiFetch to update the store
                const userData = await apiFetch<UserBrief>("/users/me");
                useAuthStore.getState().setUser(userData);
            } catch (error) {
                console.error("Failed to sync user data across tabs", error);
            }
            break;
        case "LOGOUT":
            clearAccessToken();
            clearRefreshTimer();
            useAuthStore.getState().logout();
            break;
    }
}

async function handleVisibility() {
    if (typeof document === "undefined") return;
    if (document.visibilityState !== "visible") return;

    const token = getAccessToken();

    if (token) {
        const decoded = decodeToken(token);
        if (decoded && decoded.exp) {
            const now = Math.floor(Date.now() / 1000);
            if (decoded.exp - now < 120) {
                try {
                    const refreshed = await lockedRefresh();
                    if (refreshed) {
                        setAccessToken(refreshed.accessToken);
                        scheduleRefreshTimer(refreshed.accessToken);
                        broadcastTokenRefreshed(refreshed.accessToken);
                    } else {
                        performLogout();
                    }
                } catch (err) {
                    if (!isRetriableError(err)) {
                        performLogout();
                    }
                }
            }
        }
    } else if (hasAuthHint()) {
        // We don't have a token in memory, but we have a hint that we should be logged in
        // (Another tab might still be active, or this is a fresh reload and the interceptor hasn't fired yet)
        try {
            const refreshed = await lockedRefresh();
            if (refreshed) {
                setAccessToken(refreshed.accessToken);
                scheduleRefreshTimer(refreshed.accessToken);
                broadcastTokenRefreshed(refreshed.accessToken);
                if (refreshed.user) useAuthStore.getState().setUser(refreshed.user);
            }
            // If it fails, we let the interceptor handle it or stay logged out.
        } catch {
            // Ignore, likely genuinely logged out
        }
    }
}

export function scheduleRefreshTimer(token: string) {
    if (typeof window === "undefined") return;

    clearRefreshTimer();

    const decoded = decodeToken(token);
    if (!decoded || !decoded.exp) return;

    const now = Math.floor(Date.now() / 1000);
    const delay = Math.max((decoded.exp - now - 60) * 1000, 5000);

    refreshTimer = setTimeout(async () => {
        try {
            const refreshed = await lockedRefresh();
            if (refreshed) {
                setAccessToken(refreshed.accessToken);
                scheduleRefreshTimer(refreshed.accessToken);
                broadcastTokenRefreshed(refreshed.accessToken);
            } else {
                performLogout();
            }
        } catch (err) {
            console.error("Proactive refresh failed", err);
            if (isRetriableError(err)) {
                // Transient error: retry in 30 seconds instead of logging out
                clearRefreshTimer();
                refreshTimer = setTimeout(() => {
                    scheduleRefreshTimer(token);
                }, 30000);
            } else {
                performLogout();
            }
        }
    }, delay);
}

function clearRefreshTimer() {
    if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
    }
}

function broadcastTokenRefreshed(token: string) {
    if (channel) {
        channel.postMessage({ type: "TOKEN_REFRESHED", token });
    }
}

export function broadcastTokenAcquired(token: string) {
    if (channel) {
        channel.postMessage({ type: "TOKEN_ACQUIRED", token });
    }
}

function broadcastLogout() {
    if (channel) {
        channel.postMessage({ type: "LOGOUT" });
    }
}

export function performLogout() {
    clearAccessToken();
    clearRefreshTimer();
    useAuthStore.getState().logout();
    broadcastLogout();
}
