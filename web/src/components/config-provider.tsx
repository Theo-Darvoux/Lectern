"use client";

import { useEffect, ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api-client";
import { useConfigStore, PublicConfig } from "@/lib/stores";
import { parseSegments, buildFontsUrlForNames } from "@/lib/fonts";
import { BackgroundWatermark } from "@/components/background-watermark";

export function ConfigProvider({ children }: { children: ReactNode }) {
    const { config, setConfig } = useConfigStore();
    const pathname = usePathname();
    const router = useRouter();

    // Fresh instance with no admin: force the first-run setup flow.
    useEffect(() => {
        if (config?.needs_setup && pathname !== "/setup") {
            router.replace("/setup");
        }
    }, [config?.needs_setup, pathname, router]);

    // Initial fetch and BroadcastChannel setup
    useEffect(() => {
        const bc = new BroadcastChannel("wikint_config_updates");

        const fetchConfig = async () => {
            try {
                const data = await apiFetch<PublicConfig>("/auth/methods");
                setConfig(data);
            } catch (error) {
                console.error("Failed to fetch public config", error);
            }
        };

        bc.onmessage = (event) => {
            if (event.data === "refresh") {
                fetchConfig();
            }
        };

        fetchConfig();
        return () => bc.close();
    }, [setConfig]);

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
                if (url && !document.querySelector(`link[data-wikint-fonts]`)) {
                    const link = document.createElement("link");
                    link.rel = "stylesheet";
                    link.href = url;
                    link.setAttribute("data-wikint-fonts", "1");
                    document.head.appendChild(link);
                } else if (url) {
                    const existing = document.querySelector(`link[data-wikint-fonts]`) as HTMLLinkElement | null;
                    if (existing && existing.href !== url) existing.href = url;
                }
            }
        }

        if (config.site_favicon_url) {
            // Update all existing icon links (Next.js injects several rel variants)
            const iconLinks = document.querySelectorAll<HTMLLinkElement>("link[rel~='icon'], link[rel='shortcut icon']");
            if (iconLinks.length === 0) {
                const link = document.createElement('link');
                link.rel = 'icon';
                link.href = config.site_favicon_url;
                document.head.appendChild(link);
            } else {
                iconLinks.forEach(l => { l.href = config.site_favicon_url!; });
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

    return (
        <>
            <BackgroundWatermark />
            {children}
        </>
    );
}
