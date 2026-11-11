"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiFetchRetry } from "@/lib/api-client";
import { registerViewerPrint, unregisterViewerPrint } from "@/lib/viewer-print-registry";
import { ViewerShell } from "./viewer-shell";
import { useTranslations } from "next-intl";
import { useConfigStore } from "@/lib/stores";
import { loadEuroofficeApi } from "@/lib/eurooffice-api";

interface DocEditor {
    destroyEditor: () => void;
}

/** Payload of the editor's onError event; its shape differs between versions. */
interface DocsApiError {
    data?: { errorCode?: number; errorDescription?: string } | number;
    errorCode?: number;
    description?: string;
}

declare global {
    interface Window {
        /** Global set by the Euro-Office (OnlyOffice) API script. */
        DocsAPI?: { DocEditor: new (containerId: string, config: Record<string, unknown>) => DocEditor };
    }
}

const errorMessage = (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback;

interface OfficeViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

export function OfficeViewer({ materialId, fileName, fileKey }: OfficeViewerProps) {
    const t = useTranslations("Viewers.office");
    const config = useConfigStore((state) => state.config);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const editorRef = useRef<DocEditor | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const readyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [loadAttempt, setLoadAttempt] = useState(0);
    const reactId = useId();
    const editorContainerId = `office-editor-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
    const retry = useCallback(() => setLoadAttempt((attempt) => attempt + 1), []);

    useEffect(() => {
        const container = containerRef.current;
        let isMounted = true;
        let isReady = false;
        const controller = new AbortController();

        const markReady = () => {
            isReady = true;
            if (readyTimeoutRef.current) {
                clearTimeout(readyTimeoutRef.current);
                readyTimeoutRef.current = null;
            }
            if (isMounted) setLoading(false);
        };

        const loadEditor = (editorConfig: Record<string, unknown>) => {
            const docsApi = window.DocsAPI;
            if (!docsApi) {
                setError(t("scriptError"));
                setLoading(false);
                return;
            }

            try {
                // Cleanup existing editor if any
                if (editorRef.current) {
                    try { editorRef.current.destroyEditor(); } catch {}
                    editorRef.current = null;
                }

                // Prepare the DOM container manually to avoid React hydration/unmount conflicts.
                // We create a fresh inner div for OnlyOffice to take over.
                if (containerRef.current) {
                    const editorHost = document.createElement("div");
                    editorHost.id = editorContainerId;
                    editorHost.style.width = "100%";
                    editorHost.style.height = "100%";
                    containerRef.current.replaceChildren(editorHost);
                }

                // Treat a missing readiness event as a failure. Dismissing the
                // overlay as if loading succeeded leaves users with a blank,
                // non-recoverable editor.
                if (readyTimeoutRef.current) clearTimeout(readyTimeoutRef.current);
                readyTimeoutRef.current = setTimeout(() => {
                    if (!isMounted || isReady) return;
                    try { editorRef.current?.destroyEditor(); } catch {}
                    editorRef.current = null;
                    setError(t("readyTimeout"));
                    setLoading(false);
                }, 30000);

                // Initialize the editor with the backend-provided config
                editorRef.current = new docsApi.DocEditor(editorContainerId, {
                    ...editorConfig,
                    height: "100%",
                    width: "100%",
                    events: {
                        onAppReady: () => {
                            markReady();
                            registerViewerPrint(materialId, {
                                print: () => {
                                    const iframe = containerRef.current?.querySelector("iframe");
                                    if (iframe?.contentWindow) {
                                        iframe.contentWindow.print();
                                    } else {
                                        window.print();
                                    }
                                }
                            });
                        },
                        onError: (e: DocsApiError | undefined) => {
                            const data = e?.data;
                            const code = (typeof data === "object" ? data.errorCode : data) ?? e?.errorCode;
                            const desc = (typeof data === "object" ? data.errorDescription : undefined) ?? e?.description ?? "";
                            console.error("OnlyOffice Editor Error:", { code, desc, raw: e });
                            isReady = true;
                            if (readyTimeoutRef.current) {
                                clearTimeout(readyTimeoutRef.current);
                                readyTimeoutRef.current = null;
                            }
                            if (isMounted) {
                                const detail = code != null ? ` (Code: ${code}${desc ? ` — ${desc}` : ""})` : "";
                                setError(`${t("engineError")}${detail}`);
                                setLoading(false);
                            }
                        },
                        onDocumentReady: () => {
                            markReady();
                        }
                    }
                });
            } catch (err) {
                console.error("Exception during editor init:", err);
                if (isMounted) {
                    setError(t("initFailed", { message: errorMessage(err, t("unknownError")) }));
                    setLoading(false);
                }
            }
        };

        const init = async () => {
            try {
                setLoading(true);
                setError(null);

                // Fetch the signed config and load the editor engine in parallel.
                // A transient engine request gets two bounded retries before the
                // viewer presents the manual retry action.
                const rawEuroofficeUrl = config?.eurooffice_public_url || process.env.NEXT_PUBLIC_EUROOFFICE_URL || "/eurooffice/";
                const loadEngine = async () => {
                    const retryDelays = [400, 1200];
                    let lastError: unknown;
                    for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
                        try {
                            await loadEuroofficeApi(rawEuroofficeUrl);
                            return;
                        } catch (err) {
                            lastError = err;
                            if (attempt < retryDelays.length) {
                                await new Promise((resolve) => setTimeout(resolve, retryDelays[attempt]));
                            }
                        }
                    }
                    throw lastError;
                };

                const [editorConfig] = await Promise.all([
                    apiFetchRetry<Record<string, unknown>>(`/eurooffice/config/${materialId}`, {
                        signal: controller.signal,
                        timeoutMs: 15_000,
                    }),
                    loadEngine(),
                ]);

                if (!isMounted) return;
                loadEditor(editorConfig);
            } catch (err) {
                // Navigating to another document aborts the previous signed-config
                // request by design. Treat that lifecycle cancellation as silent;
                // logging it makes Next's development overlay report a false error.
                if (!isMounted || controller.signal.aborted) return;
                console.error("EuroOffice startup error:", err);
                setError(t("startupError", { message: errorMessage(err, t("unknownError")) }));
                setLoading(false);
            }
        };

        init();

        return () => {
            isMounted = false;
            controller.abort();
            if (readyTimeoutRef.current) {
                clearTimeout(readyTimeoutRef.current);
                readyTimeoutRef.current = null;
            }
            if (editorRef.current) {
                try { editorRef.current.destroyEditor(); } catch {}
                editorRef.current = null;
            }
            if (container) {
                container.innerHTML = '';
            }
            unregisterViewerPrint(materialId);
        };
    }, [materialId, fileName, fileKey, config?.eurooffice_public_url, editorContainerId, loadAttempt, t]);

    return (
        <ViewerShell loading={false} error={error} onRetry={retry} className="h-full">
            <div className="relative w-full h-full bg-muted/5">
                {/* 
                  We use a ref-based container and manual innerHTML to isolate OnlyOffice 
                  from React's DOM reconciliation, preventing 'Node.removeChild' crashes.
                */}
                <div ref={containerRef} className="w-full h-full" />
                
                {loading && (
                    <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-background/85 backdrop-blur-sm">
                        <div className="flex flex-col items-center gap-4 rounded-2xl border bg-background/90 p-8 shadow-lg">
                            <Loader2 className="h-12 w-12 animate-spin text-primary motion-reduce:animate-none" />
                            <div className="flex flex-col items-center">
                                <p className="text-lg font-semibold bg-gradient-to-br from-foreground to-foreground/70 bg-clip-text text-transparent">
                                    {t("initializing")}
                                </p>
                                <p className="mt-1 animate-pulse text-xs text-muted-foreground motion-reduce:animate-none">
                                    {t("preparing")}
                                </p>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </ViewerShell>
    );
}
