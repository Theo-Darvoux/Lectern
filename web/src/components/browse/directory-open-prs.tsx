"use client";

import { useEffect, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { useDateLocale } from "@/hooks/use-date-locale";

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
    is_anonymous?: boolean;
    created_at: string;
}

interface DirectoryOpenPRsProps {
    directoryId: string;
}

export function DirectoryOpenPRs({ directoryId }: DirectoryOpenPRsProps) {
    const t = useTranslations("Browse");
    const tPRs = useTranslations("PRs");
    const dateLocale = useDateLocale();
    const queryClient = useQueryClient();
    const [isExpanded, setIsExpanded] = useState(false);
    // The page belongs to one directory: another directory starts on page 1.
    const [pageState, setPageState] = useState({ directoryId, page: 1 });
    const page = pageState.directoryId === directoryId ? pageState.page : 1;
    const setPage = (next: number) => setPageState({ directoryId, page: next });

    // Not critical to the folder view: on failure the section stays hidden.
    const query = useQuery({
        queryKey: ["pull-requests", "for-item", "directory", directoryId, page],
        queryFn: async ({ signal }) => {
            const { data, response } = await apiFetchWithResponse<PR[]>(
                `/pull-requests/for-item?targetType=directory&targetId=${directoryId}&page=${page}&limit=10`,
                { signal },
            );
            return { items: data, total: parseInt(response.headers.get("X-Total-Count") || "0", 10) };
        },
        enabled: !!directoryId,
        placeholderData: keepPreviousData,
    });
    const prs = query.data?.items ?? [];
    const totalCount = query.data?.total ?? 0;
    const isNavigating = query.isPlaceholderData && query.isFetching;

    // A page emptied by a closed contribution sends the reader back to the first one.
    const pageEmptied = !query.isPlaceholderData && query.data?.items.length === 0 && page > 1;
    useEffect(() => {
        if (pageEmptied) setPageState({ directoryId, page: 1 });
    }, [pageEmptied, directoryId]);

    useEffect(() => {
        if (!directoryId) return;
        const refresh = () =>
            void queryClient.invalidateQueries({
                queryKey: ["pull-requests", "for-item", "directory", directoryId],
            });
        const showNewest = () => {
            setPageState({ directoryId, page: 1 });
            refresh();
        };
        const connection = subscribeToSSE({
            channel: `directory:${directoryId}`,
            listeners: { pr_opened: showNewest, pr_closed: showNewest },
            onResync: refresh,
        });
        return () => connection.close();
    }, [directoryId, queryClient]);

    const handlePageChange = (newPage: number) => {
        if (isNavigating || newPage < 1) return;
        setPage(newPage);
    };

    if (prs.length === 0) return null;

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
                                                { addSuffix: true, locale: dateLocale },
                                            )}{" "}
                                            {t("by")}{" "}
                                            {pr.author?.display_name ||
                                                (pr.is_anonymous ? tPRs("anonymous") : "[deleted]")}
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
