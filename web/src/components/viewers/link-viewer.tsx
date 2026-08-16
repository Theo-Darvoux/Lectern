"use client";

import { useState } from "react";
import { ExternalLink, Copy, Check, Globe, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { isExternalUrl } from "@/lib/url-utils";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";

interface LinkViewerProps {
    material: Record<string, unknown> & {
        id?: string;
        title?: string;
        type?: string;
        description?: string | null;
        tags?: string[] | null;
        metadata?: Record<string, unknown> | null;
    };
}

export function LinkViewer({ material }: LinkViewerProps) {
    const t = useTranslations("LinkViewer");
    const router = useRouter();
    const openLink = useExternalLinkStore((s) => s.openLink);
    const [copied, setCopied] = useState(false);

    const targetUrl = String(
        material.metadata?.url ??
        material.metadata?.link ??
        ""
    ).trim();

    const isExternal = isExternalUrl(targetUrl);

    const handleOpen = () => {
        if (!targetUrl) return;
        openLink(targetUrl, (path) => router.push(path));
    };

    const handleCopy = async () => {
        if (!targetUrl) return;
        try {
            await navigator.clipboard.writeText(targetUrl);
            setCopied(true);
            toast.success(t("linkCopied"));
            setTimeout(() => setCopied(false), 2000);
        } catch {
            toast.error("Failed to copy link");
        }
    };

    return (
        <div className="flex flex-1 flex-col items-center justify-center p-6 md:p-12">
            <div className="w-full max-w-lg space-y-6 rounded-xl border border-border/80 bg-card p-6 shadow-sm text-center">
                {/* Icon badge */}
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-50 text-sky-600 dark:bg-sky-950/50 dark:text-sky-400 ring-8 ring-sky-50/50 dark:ring-sky-950/20">
                    {isExternal ? <Globe className="h-8 w-8" /> : <Link2 className="h-8 w-8" />}
                </div>

                {/* Title and metadata */}
                <div className="space-y-2">
                    <h2 className="text-xl font-bold tracking-tight text-foreground">
                        {material.title}
                    </h2>
                    {material.description && (
                        <p className="text-sm text-muted-foreground whitespace-pre-wrap max-w-md mx-auto">
                            {material.description}
                        </p>
                    )}
                </div>

                {/* URL display card */}
                {targetUrl ? (
                    <div className="space-y-3">
                        <div className="flex items-center gap-2 rounded-lg border border-border/80 bg-muted/50 p-2.5 text-left dark:bg-zinc-900/60">
                            <span className="min-w-0 flex-1 font-mono text-xs text-foreground truncate select-all px-1">
                                {targetUrl}
                            </span>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
                                onClick={handleCopy}
                                title={t("copyLink")}
                            >
                                {copied ? (
                                    <Check className="h-3.5 w-3.5 text-green-500" />
                                ) : (
                                    <Copy className="h-3.5 w-3.5" />
                                )}
                            </Button>
                        </div>

                        {/* Open link button */}
                        <div className="pt-2">
                            <Button
                                size="lg"
                                onClick={handleOpen}
                                className="w-full gap-2 font-medium shadow-sm"
                            >
                                <ExternalLink className="h-4 w-4" />
                                {t("openLink")}
                            </Button>
                        </div>
                    </div>
                ) : (
                    <p className="text-xs text-muted-foreground italic py-2">
                        {t("noUrl")}
                    </p>
                )}
            </div>
        </div>
    );
}
