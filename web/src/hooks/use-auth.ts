"use client";

import { useCallback } from "react";
import { apiFetch, ApiError, lockedRefresh } from "@/lib/api-client";
import { setAccessToken, getAccessToken, hasAuthHint } from "@/lib/auth-tokens";
import { useAuthStore } from "@/lib/stores";
import type { UserBrief } from "@/lib/guest";
import { broadcastTokenAcquired, performLogout, scheduleRefreshTimer } from "@/lib/auth-sync";

export function useAuth() {
    const user = useAuthStore((state) => state.user);
    const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
    const isLoading = useAuthStore((state) => state.isLoading);
    const bootstrapError = useAuthStore((state) => state.bootstrapError);
    const setUser = useAuthStore((state) => state.setUser);
    const setLoading = useAuthStore((state) => state.setLoading);
    const setBootstrapError = useAuthStore((state) => state.setBootstrapError);

    const requestCode = useCallback(async (email: string) => {
        await apiFetch("/auth/request-code", {
            method: "POST",
            body: JSON.stringify({ email }),
            skipAuth: true,
        });
    }, []);

    const verifyCode = useCallback(async (email: string, code: string) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/verify-code", {
            method: "POST",
            body: JSON.stringify({ email, code }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const verifyMagicLink = useCallback(async (token: string) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/verify-magic-link", {
            method: "POST",
            body: JSON.stringify({ token }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const continueAsGuest = useCallback(async () => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/guest", {
            method: "POST",
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const logout = useCallback(async () => {
        try {
            await apiFetch("/auth/logout", { method: "POST" });
        } catch {
            // ignore
        }
        performLogout();
    }, []);

    const handleAuthError = useCallback((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
            performLogout();
        } else if (err instanceof ApiError && err.status === 403 && err.error_code === "USER_PENDING") {
            // User exists but is pending approval — set a minimal pending state so
            // isAuthenticated stays true and LayoutShell doesn't redirect to /login.
            setUser({ id: "", email: "", display_name: null, avatar_url: null, role: "pending", onboarded: false, auto_approve: false });
            if (typeof window !== "undefined" && !window.location.pathname.startsWith("/pending-approval")) {
                window.location.replace("/pending-approval");
            }
        }
    }, [setUser]);

    const fetchMe = useCallback(async () => {
        setLoading(true);
        try {
            const me = await apiFetch<UserBrief>("/users/me");
            setUser(me);
            const token = getAccessToken();
            if (token) scheduleRefreshTimer(token);
        } catch (err) {
            handleAuthError(err);
        } finally {
            setLoading(false);
        }
    }, [setUser, setLoading, handleAuthError]);

    // Initial auth resolution on app load. The access token lives in memory only,
    // so after a page reload all we have is the persisted hint. Rather than fire a
    // guaranteed-401 `/users/me` and only then refresh, refresh first — a single
    // request that also returns the user — and skip `/users/me` entirely.
    const bootstrapAuth = useCallback(async () => {
        setLoading(true);
        setBootstrapError(null);
        try {
            if (!getAccessToken()) {
                if (!hasAuthHint()) {
                    // No token and no hint: definitely logged out. Don't touch the network.
                    setUser(null);
                    return;
                }
                const refreshed = await lockedRefresh();
                if (!refreshed) {
                    performLogout();
                    return;
                }
                setAccessToken(refreshed.accessToken);
                scheduleRefreshTimer(refreshed.accessToken);
                if (refreshed.user) {
                    setUser(refreshed.user);
                    return;
                }
                // Token but no user (e.g. older API): fall through to /users/me.
            }
            const me = await apiFetch<UserBrief>("/users/me", { timeoutMs: 10_000 });
            setUser(me);
            const token = getAccessToken();
            if (token) scheduleRefreshTimer(token);
        } catch (err) {
            if (err instanceof ApiError && (err.status === 401 || (err.status === 403 && err.error_code === "USER_PENDING"))) {
                handleAuthError(err);
            } else {
                setBootstrapError(err instanceof Error ? err.message : "Authentication initialization failed");
            }
        } finally {
            setLoading(false);
        }
    }, [setUser, setLoading, setBootstrapError, handleAuthError]);

    const verifyGoogleOAuth = useCallback(async (credential: string) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/google", {
            method: "POST",
            body: JSON.stringify({ credential }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const loginWithPassword = useCallback(async (email: string, password: string) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/login", {
            method: "POST",
            body: JSON.stringify({ email, password }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const registerWithPassword = useCallback(async (email: string, code: string, password: string, displayName?: string) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/register", {
            method: "POST",
            body: JSON.stringify({ email, code, password, display_name: displayName || null }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    const setup = useCallback(async (
        email: string,
        password: string,
        displayName?: string,
        bootstrapToken?: string,
    ) => {
        const data = await apiFetch<{
            access_token: string;
            user: UserBrief;
            is_new_user: boolean;
        }>("/auth/setup", {
            method: "POST",
            body: JSON.stringify({
                email,
                password,
                display_name: displayName || null,
                bootstrap_token: bootstrapToken || null,
            }),
            skipAuth: true,
        });

        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefreshTimer(data.access_token);
        broadcastTokenAcquired(data.access_token);
        return data;
    }, [setUser]);

    return { user, isAuthenticated, isLoading, bootstrapError, requestCode, verifyCode, verifyMagicLink, verifyGoogleOAuth, loginWithPassword, registerWithPassword, setup, continueAsGuest, logout, fetchMe, bootstrapAuth };
}
