"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiFetchRetry } from "@/lib/api-client";
import { registerViewerPrint, unregisterViewerPrint } from "@/lib/viewer-print-registry";
import { printPdfBlob } from "@/lib/print-utils";
import { ViewerShell } from "./viewer-shell";
import { useLocale, useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { useConfigStore } from "@/lib/stores";
import { loadEuroofficeApi } from "@/lib/eurooffice-api";

interface DocEditor {
    destroyEditor: () => void;
    downloadAs?: (format?: string) => void;
}

/** Payload of the editor's onError event; its shape differs between versions. */
interface DocsApiError {
    data?: { errorCode?: number; errorDescription?: string } | number;
    errorCode?: number;
    description?: string;
}

/** Payload of onDownloadAs: `{ fileType, url }` since 7.0, a bare URL before. */
interface DocsApiDownloadAs {
    data?: { fileType?: string; url?: string } | string;
}

declare global {
    interface Window {
        /** Global set by the Euro-Office (OnlyOffice) API script. */
        DocsAPI?: { DocEditor: new (containerId: string, config: Record<string, unknown>) => DocEditor };
    }
}

interface PendingPrint {
    resolve: () => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
}

const READY_TIMEOUT_MS = 30_000;
/** Converting a large presentation to PDF can take a while on the server. */
const PRINT_EXPORT_TIMEOUT_MS = 90_000;
const SUPPORTED_EDITOR_LANGS = new Set(["en", "fr"]);

/** Editor error codes (sdkjs `c_oAscError.ID`) that have a clearer message than the raw code. */
const ERROR_MESSAGE_KEYS: Record<number, "errorDownload" | "errorConversion" | "errorTimeout" | "errorTooLarge"> = {
    [-2]: "errorTimeout",
    [-3]: "errorConversion",
    [-4]: "errorDownload",
    [-82]: "errorConversion",
    [-84]: "errorTooLarge",
};

const errorMessage = (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback;

/** The editor's compact mobile layout fits phones; tablets keep the desktop one. */
const prefersMobileEditor = () =>
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(max-width: 767px) and (pointer: coarse)").matches;

/** Read the applied theme class before next-themes has resolved, to avoid a second editor load. */
const documentTheme = (): "light" | "dark" =>
    typeof document !== "undefined" && document.documentElement.classList.contains("dark")
        ? "dark"
        : "light";

async function printExportedPdf(url: string): Promise<void> {
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`PDF export request failed (${response.status})`);
        printPdfBlob(await response.blob());
    } catch (err) {
        // A document server on another origin may not allow the fetch; opening
        // the export still lets the user print it from the browser's viewer.
        if (!window.open(url, "_blank")) throw err;
    }
}

interface OfficeViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

export function OfficeViewer({ materialId, fileName, fileKey }: OfficeViewerProps) {
    const t = useTranslations("Viewers.office");
    const locale = useLocale();
    const { resolvedTheme } = useTheme();
    const config = useConfigStore((state) => state.config);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const editorRef = useRef<DocEditor | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const readyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingPrintRef = useRef<PendingPrint | null>(null);
    const [loadAttempt, setLoadAttempt] = useState(0);
    const reactId = useId();
    const editorContainerId = `office-editor-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
    const retry = useCallback(() => setLoadAttempt((attempt) => attempt + 1), []);

    const editorLang = SUPPORTED_EDITOR_LANGS.has(locale) ? locale : "en";
    const editorTheme = resolvedTheme === "dark" || resolvedTheme === "light" ? resolvedTheme : documentTheme();

    useEffect(() => {
        const container = containerRef.current;
        let isMounted = true;
        let isReady = false;
        const controller = new AbortController();

        const settlePendingPrint = (failure?: Error) => {
            const pending = pendingPrintRef.current;
            if (!pending) return;
            pendingPrintRef.current = null;
            clearTimeout(pending.timer);
            if (failure) pending.reject(failure);
            else pending.resolve();
        };

        // The editor prints through its own PDF export: printing the iframe
        // would only capture the editor chrome and the visible page.
        const printDocument = () =>
            new Promise<void>((resolve, reject) => {
                const editor = editorRef.current;
                if (!editor?.downloadAs) {
                    reject(new Error("EuroOffice editor cannot export this document"));
                    return;
                }
                settlePendingPrint(new Error("Superseded by a newer print request"));
                const timer = setTimeout(
                    () => settlePendingPrint(new Error("EuroOffice PDF export timed out")),
                    PRINT_EXPORT_TIMEOUT_MS,
                );
                pendingPrintRef.current = { resolve, reject, timer };
                editor.downloadAs("pdf");
            });

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
                }, READY_TIMEOUT_MS);

                // Initialize the editor with the backend-provided config
                editorRef.current = new docsApi.DocEditor(editorContainerId, {
                    ...editorConfig,
                    type: prefersMobileEditor() ? "mobile" : "desktop",
                    height: "100%",
                    width: "100%",
                    events: {
                        onAppReady: () => {
                            markReady();
                            registerViewerPrint(materialId, { print: printDocument });
                        },
                        onDownloadAs: (e: DocsApiDownloadAs | undefined) => {
                            if (!pendingPrintRef.current) return;
                            const data = e?.data;
                            const url = typeof data === "string" ? data : data?.url;
                            if (!url) {
                                settlePendingPrint(new Error("EuroOffice PDF export returned no file"));
                                return;
                            }
                            const pending = pendingPrintRef.current;
                            pendingPrintRef.current = null;
                            clearTimeout(pending.timer);
                            printExportedPdf(url).then(pending.resolve, (err: unknown) =>
                                pending.reject(err instanceof Error ? err : new Error(String(err))),
                            );
                        },
                        onError: (e: DocsApiError | undefined) => {
                            const data = e?.data;
                            const code = (typeof data === "object" ? data.errorCode : data) ?? e?.errorCode;
                            const desc = (typeof data === "object" ? data.errorDescription : undefined) ?? e?.description ?? "";
                            console.error("OnlyOffice Editor Error:", { code, desc, raw: e });
                            settlePendingPrint(new Error("EuroOffice editor error"));
                            isReady = true;
                            if (readyTimeoutRef.current) {
                                clearTimeout(readyTimeoutRef.current);
                                readyTimeoutRef.current = null;
                            }
                            if (isMounted) {
                                const messageKey = code != null ? ERROR_MESSAGE_KEYS[code] : undefined;
                                const detail = code != null ? ` (Code: ${code}${desc ? ` — ${desc}` : ""})` : "";
                                setError(messageKey ? `${t(messageKey)} (Code: ${code})` : `${t("engineError")}${detail}`);
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

                // Language and theme are signed into the config server-side.
                const query = new URLSearchParams({ lang: editorLang, theme: editorTheme });
                const [editorConfig] = await Promise.all([
                    apiFetchRetry<Record<string, unknown>>(`/eurooffice/config/${materialId}?${query}`, {
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
            settlePendingPrint(new Error("Document viewer closed"));
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
    }, [materialId, fileName, fileKey, config?.eurooffice_public_url, editorContainerId, editorLang, editorTheme, loadAttempt, t]);

    return (
        <ViewerShell
            loading={false}
            error={error}
            onRetry={retry}
            downloadMaterialId={materialId}
            className="h-full"
        >
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
