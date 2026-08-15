"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { apiFetchRetry } from "@/lib/api-client";
import { useConfigStore, type PublicConfig } from "@/lib/stores";
import { parseSegments, buildFontsUrlForNames } from "@/lib/fonts";
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

        void fetchConfig(controller.signal);
        return () => {
            controller.abort();
            bc?.close();
        };
    }, [fetchConfig]);

    // Apply config changes immediately to DOM/CSS whenever the store updates
    useEffect(() => {
        if (!config) return;

        // Update tab title if the page hasn't set a custom one (i.e. still the static default).
        // Re-runs on pathname change so navigation to pages without custom titles also updates.
        if (config.site_name && !document.title.includes(" • ")) {
            document.title = config.site_name;
        }

        // Inject Google Fonts for any fonts used in the site name style
        if (config.site_name_style) {
            const segments = parseSegments(config.site_name_style);
            if (segments) {
                const usedFonts = [...new Set(segments.map((s) => s.font).filter(Boolean))];
                const url = buildFontsUrlForNames(usedFonts);
                if (url && !document.querySelector(`link[data-lectern-fonts]`)) {
                    const link = document.createElement("link");
                    link.rel = "stylesheet";
                    link.href = url;
                    link.setAttribute("data-lectern-fonts", "1");
                    document.head.appendChild(link);
                } else if (url) {
                    const existing = document.querySelector(`link[data-lectern-fonts]`) as HTMLLinkElement | null;
                    if (existing && existing.href !== url) existing.href = url;
                }
            }
        }

        const faviconUrl = config.site_favicon_url || config.site_logo_url;
        if (faviconUrl) {
            // Update all existing icon links (Next.js injects several rel variants)
            const iconLinks = document.querySelectorAll<HTMLLinkElement>("link[rel~='icon'], link[rel='shortcut icon']");
            if (iconLinks.length === 0) {
                const link = document.createElement('link');
                link.rel = 'icon';
                link.href = faviconUrl;
                document.head.appendChild(link);
            } else {
                iconLinks.forEach(l => { l.href = faviconUrl; });
            }
        }

        // Inject primary color if needed (custom CSS variable)
        if (config.primary_color) {
            document.documentElement.style.setProperty('--primary-custom', config.primary_color);

            // Calculate and set a contrasting foreground color
            const hex = config.primary_color.replace('#', '');
            if (hex.length === 6) {
                const r = parseInt(hex.substring(0, 2), 16);
                const g = parseInt(hex.substring(2, 4), 16);
                const b = parseInt(hex.substring(4, 6), 16);
                const brightness = (r * 299 + g * 587 + b * 114) / 1000;

                // If the background is light, use dark text; otherwise use light text
                const foreground = brightness > 165 ? 'oklch(0.205 0 0)' : 'oklch(0.985 0 0)';
                document.documentElement.style.setProperty('--primary-foreground-custom', foreground);
            } else {
                // Fallback to light text for custom colors if we can't parse it
                document.documentElement.style.setProperty('--primary-foreground-custom', 'oklch(0.985 0 0)');
            }
        }
    }, [config, pathname]);

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
