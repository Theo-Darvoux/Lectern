"use client";

import { useEffect, useState, useId } from "react";
import { useTheme } from "next-themes";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

interface MermaidProps {
    chart: string;
}

export function Mermaid({ chart }: MermaidProps) {
    const t = useTranslations("Viewers.mermaid");
    const { resolvedTheme } = useTheme();
    const [svg, setSvg] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const componentId = useId().replace(/:/g, ""); // Use useId for stable ID, but remove colons for Mermaid

    useEffect(() => {
        let isMounted = true;
        const MAX_CHART_LENGTH = 50000;

        const renderChart = async () => {
            if (!chart || chart.trim() === "") {
                if (isMounted) {
                    setError(t("empty"));
                    setSvg(null);
                }
                return;
            }

            if (chart.length > MAX_CHART_LENGTH) {
                if (isMounted) {
                    setError(t("tooLarge", { length: chart.length, max: MAX_CHART_LENGTH }));
                    setSvg(null);
                }
                return;
            }

            try {
                const { default: mermaid } = await import("mermaid");
                if (!isMounted) return;
                // Initialize on every render ensures theme is correct
                mermaid.initialize({
                    startOnLoad: false,
                    theme: resolvedTheme === "dark" ? "dark" : "default",
                    securityLevel: "strict",
                    fontFamily: "inherit",
                    // Explicitly disable HTML labels for maximum security
                    htmlLabels: false,
                });

                const id = `mermaid-${componentId}`;
                const { svg: renderedSvg } = await mermaid.render(id, chart);
                
                if (isMounted) {
                    setSvg(renderedSvg);
                    setError(null);
                }
            } catch (err: unknown) {
                console.error("Mermaid render error:", err);
                if (isMounted) {
                    const message = err instanceof Error ? err.message : String(err);
                    setError(message || t("errorGeneric"));
                    setSvg(null);
                }
            }
        };

        renderChart();

        return () => {
            isMounted = false;
        };
    }, [chart, resolvedTheme, componentId]);

    if (error) {
        return (
            <div className="my-4 rounded-md border border-destructive bg-destructive/10 p-4 text-sm text-destructive">
                <span className="font-semibold text-base block mb-1">{t("title")}</span>
                <p className="font-mono text-xs opacity-90 break-words">{error}</p>
            </div>
        );
    }

    if (!svg) {
        return (
            <div className="my-4 flex items-center justify-center rounded-md bg-muted p-8 animate-pulse border border-border">
                <Loader2 className="mr-2 h-5 w-5 animate-spin text-muted-foreground" />
                <span className="text-sm text-muted-foreground">{t("rendering")}</span>
            </div>
        );
    }

    return (
        <div
            className="my-6 flex justify-center overflow-x-auto rounded-lg bg-background p-4 border border-border shadow-sm"
            dangerouslySetInnerHTML={{ __html: svg }}
        />
    );
}
