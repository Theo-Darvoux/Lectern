"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { apiFetchRetry } from "@/lib/api-client";
import { useConfigStore, type PublicConfig } from "@/lib/stores";
import { brandColorVariables, wordmarkFontsHref } from "@/lib/brand";
import { BackgroundWatermark } from "@/components/background-watermark";
import { normalizePathname } from "@/lib/utils";

type ConfigLoadState = "loading" | "ready" | "error";

export function ConfigProvider({ children }: { children: ReactNode }) {
    const config = useConfigStore((state) => state.config);
    const setConfig = useConfigStore((state) => state.setConfig);
    const rawPathname = usePathname();
    const pathname = normalizePathname(rawPathname);
    const router = useRouter();
    const tSetup = useTranslations("Setup");
    const [loadState, setLoadState] = useState<ConfigLoadState>(config ? "ready" : "loading");
    const [loadError, setLoadError] = useState<string | null>(null);
    // The root layout renders with the config it fetched on the server. The
    // client fetches it only when the server could not.
    const renderedWithConfig = useRef(config !== null);

    const fetchConfig = useCallback(async (signal?: AbortSignal) => {
        setLoadState("loading");
        setLoadError(null);
        try {
            // This endpoint is intentionally public and is the authoritative
            // source for whether first-run setup is required. Bound the request
            // so `/setup` can always settle into either usable UI or retry UI.
            const data = await apiFetchRetry<PublicConfig>("/auth/methods", {
                skipAuth: true,
                timeoutMs: 5_000,
                retries: 1,
                retryBaseDelayMs: 500,
                signal,
            });
            if (signal?.aborted) return;
            setConfig(data);
            setLoadState("ready");
        } catch (error) {
            if (signal?.aborted) return;
            console.error("Failed to fetch public config", error);
            setLoadError(error instanceof Error ? error.message : String(error));
            setLoadState("error");
        }
    }, [setConfig]);

    // Fresh instance with no admin: force the first-run setup flow.
    useEffect(() => {
        if (config?.needs_setup && pathname !== "/setup") {
            router.replace("/setup");
        }
    }, [config?.needs_setup, pathname, router]);

    // Initial fetch and BroadcastChannel setup
    useEffect(() => {
        const controller = new AbortController();
        const bc = typeof BroadcastChannel !== "undefined"
            ? new BroadcastChannel("lectern_config_updates")
            : null;

        if (bc) {
            bc.onmessage = (event) => {
                if (event.data === "refresh") {
                    void fetchConfig();
                }
            };
        }

        if (!renderedWithConfig.current) void fetchConfig(controller.signal);
        return () => {
            controller.abort();
            bc?.close();
        };
    }, [fetchConfig]);

    // The server already rendered the branding into the page. Apply it again
    // when the config arrives or changes on the client (the API was down while
    // rendering, or another tab broadcast a refresh).
    useEffect(() => {
        if (!config) return;

        const fontsHref = wordmarkFontsHref(config.site_name_style);
        if (fontsHref) {
            const existing = document.querySelector<HTMLLinkElement>("link[data-lectern-fonts]");
            if (!existing) {
                const link = document.createElement("link");
                link.rel = "stylesheet";
                link.href = fontsHref;
                link.setAttribute("data-lectern-fonts", "1");
                document.head.appendChild(link);
            } else if (existing.href !== fontsHref) {
                existing.href = fontsHref;
            }
        }

        for (const [name, value] of Object.entries(brandColorVariables(config.primary_color))) {
            document.documentElement.style.setProperty(name, value);
        }
    }, [config]);

    // The setup form depends on `/auth/methods` for both the durable
    // `needs_setup` marker and whether the operator bootstrap token is required.
    // Do not mount the normal app/auth shell until that prerequisite resolves.
    if (pathname === "/setup" && !config) {
        if (loadState === "error") {
            return (
                <main className="flex min-h-svh items-center justify-center p-4">
                    <div
                        className="w-full max-w-md rounded-xl border bg-card p-6 text-card-foreground shadow-sm"
                        role="alert"
                    >
                        <h1 className="text-lg font-semibold">{tSetup("installationCheckFailedTitle")}</h1>
                        <p className="mt-2 text-sm text-muted-foreground">
                            {tSetup("installationCheckFailedDescription")}
                        </p>
                        {process.env.NODE_ENV === "development" && loadError && (
                            <pre className="mt-3 max-h-32 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
                                {loadError}
                            </pre>
                        )}
                        <button
                            type="button"
                            className="mt-5 inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
                            onClick={() => void fetchConfig()}
                        >
                            {tSetup("retry")}
                        </button>
                    </div>
                </main>
            );
        }

        return (
            <main className="flex min-h-svh items-center justify-center p-4" role="status" aria-live="polite">
                <div className="flex flex-col items-center gap-4 text-center">
                    <div
                        className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent"
                        aria-hidden="true"
                    />
                    <p className="text-sm font-medium text-muted-foreground">
                        {tSetup("checkingInstallation")}
                    </p>
                </div>
            </main>
        );
    }

    return (
        <>
            <BackgroundWatermark />
            {children}
        </>
    );
}
