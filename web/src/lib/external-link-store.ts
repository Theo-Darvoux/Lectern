import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { safeLocalStorage } from "./safe-storage";
import { isExternalUrl, getDomainFromUrl, normalizeTargetUrl } from "./url-utils";

interface ExternalLinkState {
    isOpen: boolean;
    targetUrl: string;
    domain: string;
    trustedDomains: string[];
    openLink: (url: string, routerNavigate?: (path: string) => void) => void;
    closeDialog: () => void;
    confirmAndOpen: (trustDomain?: boolean) => void;
    addTrustedDomain: (domain: string) => void;
    removeTrustedDomain: (domain: string) => void;
}

export const useExternalLinkStore = create<ExternalLinkState>()(
    persist(
        (set, get) => ({
            isOpen: false,
            targetUrl: "",
            domain: "",
            trustedDomains: [],

            openLink: (rawUrl: string, routerNavigate?: (path: string) => void) => {
                if (!rawUrl || typeof rawUrl !== "string") return;
                const normalized = normalizeTargetUrl(rawUrl);

                if (!isExternalUrl(normalized)) {
                    // Internal URL: navigate directly without warning
                    if (routerNavigate) {
                        try {
                            const parsed = new URL(
                                normalized,
                                typeof window !== "undefined" ? window.location.origin : "http://localhost",
                            );
                            const pathAndQuery = parsed.pathname + parsed.search + parsed.hash;
                            routerNavigate(pathAndQuery);
                            return;
                        } catch {
                            routerNavigate(normalized);
                            return;
                        }
                    }
                    if (typeof window !== "undefined") {
                        window.location.href = normalized;
                    }
                    return;
                }

                // External URL: check if domain is already trusted
                const domain = getDomainFromUrl(normalized);
                const isTrusted = domain && get().trustedDomains.includes(domain.toLowerCase());

                if (isTrusted) {
                    if (typeof window !== "undefined") {
                        window.open(normalized, "_blank", "noopener,noreferrer");
                    }
                    return;
                }

                // Show warning dialog
                set({
                    isOpen: true,
                    targetUrl: normalized,
                    domain: domain || normalized,
                });
            },

            closeDialog: () => {
                set({ isOpen: false, targetUrl: "", domain: "" });
            },

            confirmAndOpen: (trustThisDomain = false) => {
                const { targetUrl, domain, trustedDomains } = get();
                if (!targetUrl) {
                    set({ isOpen: false });
                    return;
                }

                if (trustThisDomain && domain) {
                    const normalizedDomain = domain.toLowerCase();
                    if (!trustedDomains.includes(normalizedDomain)) {
                        set({ trustedDomains: [...trustedDomains, normalizedDomain] });
                    }
                }

                if (typeof window !== "undefined") {
                    window.open(targetUrl, "_blank", "noopener,noreferrer");
                }
                set({ isOpen: false, targetUrl: "", domain: "" });
            },

            addTrustedDomain: (domain: string) => {
                const normalized = domain.trim().toLowerCase();
                if (!normalized) return;
                const { trustedDomains } = get();
                if (!trustedDomains.includes(normalized)) {
                    set({ trustedDomains: [...trustedDomains, normalized] });
                }
            },

            removeTrustedDomain: (domain: string) => {
                const normalized = domain.trim().toLowerCase();
                const { trustedDomains } = get();
                set({ trustedDomains: trustedDomains.filter((d) => d !== normalized) });
            },
        }),
        {
            name: "wikint-trusted-domains",
            storage: createJSONStorage(() => safeLocalStorage),
            partialize: (state) => ({ trustedDomains: state.trustedDomains }),
        },
    ),
);
