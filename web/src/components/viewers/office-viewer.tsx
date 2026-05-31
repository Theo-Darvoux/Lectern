"use client";

import { useEffect, useState, useRef } from "react";
import { Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { registerViewerPrint, unregisterViewerPrint } from "@/lib/viewer-print-registry";
import { ViewerShell } from "./viewer-shell";
import { useTranslations } from "next-intl";

interface OfficeViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

const BASE_EUROOFFICE_URL = process.env.NEXT_PUBLIC_EUROOFFICE_URL || "http://localhost/eurooffice/";
const EUROOFFICE_URL = BASE_EUROOFFICE_URL.endsWith("/") ? BASE_EUROOFFICE_URL : `${BASE_EUROOFFICE_URL}/`;

export function OfficeViewer({ materialId, fileName, fileKey }: OfficeViewerProps) {
    const t = useTranslations("Viewers.office");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const scriptRef = useRef<HTMLScriptElement | null>(null);
    const editorRef = useRef<any>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        let isMounted = true;

        const loadEditor = (config: any) => {
            if (!(window as any).DocsAPI) {
                setError(t("scriptError"));
                setLoading(false);
                return;
            }

            try {
                // Cleanup existing editor if any
                if (editorRef.current) {
                    try { editorRef.current.destroyEditor(); } catch (e) {}
                    editorRef.current = null;
                }

                // Prepare the DOM container manually to avoid React hydration/unmount conflicts.
                // We create a fresh inner div for OnlyOffice to take over.
                if (containerRef.current) {
                    containerRef.current.innerHTML = '<div id="office-editor-container" style="width:100%;height:100%;"></div>';
                }

                // Initialize the editor with the backend-provided config
                editorRef.current = new (window as any).DocsAPI.DocEditor("office-editor-container", {
                    ...config,
                    height: "100%",
                    width: "100%",
                    events: {
                        onAppReady: () => {
                            if (isMounted) setLoading(false);
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
                        onError: (e: any) => {
                            const code = e?.data?.errorCode ?? e?.data ?? e?.errorCode;
                            const desc = e?.data?.errorDescription ?? e?.description ?? "";
                            console.error("OnlyOffice Editor Error:", { code, desc, raw: e });
                            if (isMounted) {
                                const detail = code != null ? ` (Code: ${code}${desc ? ` — ${desc}` : ""})` : "";
                                setError(`${t("engineError")}${detail}`);
                                setLoading(false);
                            }
                        },
                        onDocumentReady: () => {
                            if (isMounted) setLoading(false);
                        }
                    }
                });
            } catch (err: any) {
                console.error("Exception during editor init:", err);
                if (isMounted) {
                    setError(t("initFailed", { message: err.message || "Unknown error" }));
                    setLoading(false);
                }
            }
        };

        const init = async () => {
            try {
                setLoading(true);
                setError(null);

                // 1. Fetch the signed OnlyOffice/Euro-Office config from the backend
                const config = await apiFetch<any>(`/eurooffice/config/${materialId}`);

                if (!isMounted) return;

                // 2. Load the API script if not already present
                if (!(window as any).DocsAPI) {
                    const script = document.createElement("script");
                    script.id = "eurooffice-api-script";
                    script.src = `${EUROOFFICE_URL}web-apps/apps/api/documents/api.js`;
                    script.async = true;
                    script.onload = () => {
                        if (isMounted) loadEditor(config);
                    };
                    script.onerror = (e) => {
                        console.error("Script load error:", e);
                        if (isMounted) {
                            setError(t("loadScriptError"));
                            setLoading(false);
                        }
                    };
                    document.head.appendChild(script);
                    scriptRef.current = script;
                } else {
                    loadEditor(config);
                }
            } catch (err: any) {
                console.error("Config fetch error:", err);
                if (isMounted) {
                    setError(t("configError", { message: err.message || "Unknown error" }));
                    setLoading(false);
                }
            }
        };

        init();

        return () => {
            isMounted = false;
            if (editorRef.current) {
                try { editorRef.current.destroyEditor(); } catch (e) {}
                editorRef.current = null;
            }
            if (containerRef.current) {
                containerRef.current.innerHTML = '';
            }
            unregisterViewerPrint(materialId);
        };
    }, [materialId, fileName, fileKey]);

    return (
        <ViewerShell loading={false} error={error} className="h-full">
            <div className="relative w-full h-full bg-muted/5">
                {/* 
                  We use a ref-based container and manual innerHTML to isolate OnlyOffice 
                  from React's DOM reconciliation, preventing 'Node.removeChild' crashes.
                */}
                <div ref={containerRef} className="w-full h-full" />
                
                {loading && (
                    <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-background/80 backdrop-blur-md">
                        <div className="flex flex-col items-center gap-4 p-8 rounded-2xl bg-background/50 border shadow-2xl scale-110">
                            <Loader2 className="w-12 h-12 animate-spin text-primary" />
                            <div className="flex flex-col items-center">
                                <p className="text-lg font-semibold bg-gradient-to-br from-foreground to-foreground/70 bg-clip-text text-transparent">
                                    {t("initializing")}
                                </p>
                                <p className="text-xs text-muted-foreground animate-pulse mt-1">
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
