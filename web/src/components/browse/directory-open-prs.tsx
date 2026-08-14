"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { apiFetchWithResponse } from "@/lib/api-client";
import { subscribeToSSE } from "@/lib/sse-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Send,
    FilePlus,
    FilePenLine,
    FileX,
    FolderPlus,
    FolderPen,
    FolderX,
    ArrowRightLeft,
    ChevronLeft,
    ChevronRight,
    ChevronDown,
    Loader2,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useTranslations } from "next-intl";

const OP_ICONS: Record<string, React.ElementType> = {
    create_material: FilePlus,
    edit_material: FilePenLine,
    delete_material: FileX,
    create_directory: FolderPlus,
    edit_directory: FolderPen,
    delete_directory: FolderX,
    move_item: ArrowRightLeft,
};

interface PR {
    id: string;
    title: string;
    status: string;
    type: string;
    summary_types?: string[];
    author: { id: string; display_name: string } | null;
    created_at: string;
}

interface DirectoryOpenPRsProps {
    directoryId: string;
}

export function DirectoryOpenPRs({ directoryId }: DirectoryOpenPRsProps) {
    const t = useTranslations("Browse");
    const tPRs = useTranslations("PRs");
    const [prs, setPrs] = useState<PR[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [isNavigating, setIsNavigating] = useState(false);
    const [isExpanded, setIsExpanded] = useState(false);

    const fetchPRs = useCallback(
        async (p: number) => {
            if (!directoryId) return;

            try {
                const { data, response } = await apiFetchWithResponse<PR[]>(
                    `/pull-requests/for-item?targetType=directory&targetId=${directoryId}&page=${p}&limit=10`,
                );

                const total = parseInt(
                    response.headers.get("X-Total-Count") || "0",
                    10,
                );
                setTotalCount(total);
                setPrs(data);
            } catch {
                // Silently ignore — not critical
            }
        },
        [directoryId],
    );

    useEffect(() => {
        let cancelled = false;

        async function initialFetch() {
            setLoading(true);
            setPage(1);
            await fetchPRs(1);
            if (!cancelled) setLoading(false);
        }

        initialFetch();
        return () => {
            cancelled = true;
        };
    }, [directoryId, fetchPRs]);

    useEffect(() => {
        if (!directoryId) return;
        const connection = subscribeToSSE({
            channel: `directory:${directoryId}`,
            listeners: {
                pr_opened: () => { setPage(1); fetchPRs(1); },
                pr_closed: () => { setPage(1); fetchPRs(1); },
            },
            onResync: () => { setPage(1); fetchPRs(1); },
            startupDelay: 50,
        });
        return () => connection.close();
    }, [directoryId, fetchPRs]);

    const handlePageChange = async (newPage: number) => {
        if (isNavigating || newPage < 1) return;
        setIsNavigating(true);
        await fetchPRs(newPage);
        setPage(newPage);
        setIsNavigating(false);
    };

    if (loading || prs.length === 0) return null;

    const totalPages = Math.ceil(totalCount / 10);

    return (
        <div className="rounded-lg border border-amber-200 bg-amber-50/50 dark:border-amber-800/50 dark:bg-amber-950/20 overflow-hidden transition-all duration-200">
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="flex w-full items-center justify-between gap-2 px-4 py-2.5 hover:bg-amber-100/40 dark:hover:bg-amber-900/10 transition-colors text-left"
            >
                <div className="flex items-center gap-2">
                    <Send className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                    <span className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                        {t("openContributionCount", { count: totalCount })}{" "}
                        <span className="opacity-70">{t("inThisFolder")}</span>
                    </span>
                </div>
                {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-amber-500 transition-transform duration-200 rotate-180" />
                ) : (
                    <ChevronDown className="h-4 w-4 text-amber-500 transition-transform duration-200" />
                )}
            </button>

            {isExpanded && (
                <div className="border-t border-amber-200/60 dark:border-amber-800/40 animate-in fade-in slide-in-from-top-1 duration-200 relative">
                    {isNavigating && (
                        <div className="absolute inset-0 bg-amber-50/20 dark:bg-amber-950/20 backdrop-blur-[1px] z-10 flex items-center justify-center">
                            <Loader2 className="h-6 w-6 animate-spin text-amber-600 dark:text-amber-400" />
                        </div>
                    )}
                    
                    <div className="divide-y divide-amber-200/40 dark:divide-amber-800/30">
                        {prs.map((pr) => {
                            const types =
                                pr.summary_types && pr.summary_types.length > 0
                                    ? pr.summary_types
                                    : [pr.type];
                            return (
                                <Link
                                    key={pr.id}
                                    href={`/pull-requests/${pr.id}`}
                                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-amber-100/50 dark:hover:bg-amber-900/20 transition-colors group"
                                >
                                    <Send className="h-4 w-4 shrink-0 text-amber-600" />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <span className="text-sm font-medium truncate group-hover:underline">
                                                {pr.title}
                                            </span>
                                            {types.map((t) => {
                                                const Icon = OP_ICONS[t];
                                                return (
                                                    <Badge
                                                        key={t}
                                                        variant="outline"
                                                        className="shrink-0 gap-0.5 text-[10px] h-5 border-amber-300 dark:border-amber-700 text-amber-700 dark:text-amber-400"
                                                    >
                                                        {Icon && (
                                                            <Icon className="h-2.5 w-2.5" />
                                                        )}
                                                        {tPRs.has(`operations.${t}`) ? tPRs(`operations.${t}`) : t.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")}
                                                    </Badge>
                                                );
                                            })}
                                        </div>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            #{pr.id.slice(0, 8)} &middot;{" "}
                                            {formatDistanceToNow(
                                                new Date(pr.created_at),
                                                { addSuffix: true },
                                            )}{" "}
                                            {t("by")}{" "}
                                            {pr.author?.display_name || "[deleted]"}
                                        </p>
                                    </div>
                                    <ChevronRight className="h-4 w-4 shrink-0 text-amber-400 dark:text-amber-600 group-hover:text-amber-600 dark:group-hover:text-amber-400 transition-colors" />
                                </Link>
                            );
                        })}
                    </div>

                    {totalCount > 10 && (
                        <div className="p-2 px-4 flex items-center justify-between border-t border-amber-200/40 dark:border-amber-800/30">
                            <Button
                                variant="ghost"
                                size="sm"
                                disabled={page === 1 || isNavigating}
                                onClick={(e) => { e.stopPropagation(); handlePageChange(page - 1); }}
                                className="text-amber-700 dark:text-amber-400 hover:bg-amber-100/50 dark:hover:bg-amber-900/20 h-8 gap-1"
                            >
                                <ChevronLeft className="h-4 w-4" />
                                {t("previous")}
                            </Button>
                            
                            <span className="text-[10px] font-medium text-amber-800/60 dark:text-amber-300/60">
                                {t("pageOf", { page, total: totalPages })}
                            </span>

                            <Button
                                variant="ghost"
                                size="sm"
                                disabled={page === totalPages || isNavigating}
                                onClick={(e) => { e.stopPropagation(); handlePageChange(page + 1); }}
                                className="text-amber-700 dark:text-amber-400 hover:bg-amber-100/50 dark:hover:bg-amber-900/20 h-8 gap-1"
                            >
                                {t("next")}
                                <ChevronRight className="h-4 w-4" />
                            </Button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
