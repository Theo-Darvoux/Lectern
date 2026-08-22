"use client";

import Link from "next/link";
import { GitPullRequest, ChevronRight, GitMerge } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionHeader } from "./section-header";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import type { PullRequestOut } from "./types";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface RecentPRsSectionProps {
    prs: PullRequestOut[];
    isLoading?: boolean;
    hasLoaded?: boolean;
}

function PRRowSkeleton() {
    return (
        <div className="flex items-center gap-3 px-4 py-3 border-b last:border-b-0">
            <Skeleton className="h-4 w-4 shrink-0 rounded" />
            <div className="flex-1 space-y-1.5">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
            </div>
            <Skeleton className="h-5 w-12 rounded-full shrink-0" />
            <Skeleton className="h-4 w-4 shrink-0 rounded" />
        </div>
    );
}


function PRRow({ pr }: { pr: PullRequestOut }) {
    const t = useTranslations("Home");
    const authorLabel = pr.author?.display_name ?? t("unknownAuthor");

    const timeAgo = formatDistanceToNow(new Date(pr.created_at), {
        addSuffix: true,
    });

    return (
        <Link
            href={`/pull-requests/${pr.id}`}
            className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50 border-b last:border-b-0 focus-visible:outline-none focus-visible:bg-muted/50"
        >
            {/* Status icon */}
            <GitPullRequest className="h-4 w-4 shrink-0 text-green-500" />

            {/* Title + meta */}
            <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium leading-snug group-hover:underline">
                    {pr.title}
                </p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    <span className="font-medium">{authorLabel}</span>
                    <span className="mx-1 opacity-50">·</span>
                    {timeAgo}
                    <span className="mx-1 opacity-50">·</span>
                    <span className="font-mono opacity-60">
                        #{pr.id.slice(0, 8)}
                    </span>
                </p>
            </div>

            {/* Badges */}
            <div className="flex shrink-0 items-center gap-2">
                {/* Open status */}
                <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                    {t("open")}
                </span>
            </div>

            {/* Chevron */}
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-muted-foreground" />
        </Link>
    );
}

export function RecentPRsSection({ prs, isLoading = false, hasLoaded }: RecentPRsSectionProps) {
    const t = useTranslations("Home");
    const loaded = hasLoaded ?? (prs.length > 0 || !isLoading);
    // Don't render an empty section once data is loaded
    if (loaded && prs.length === 0) return null;

    return (
        <section aria-label={t("recentContributions")} aria-busy={isLoading}>
            <SectionHeader
                title={t("recentContributions")}
                icon={<GitPullRequest className="h-4 w-4" />}
                seeAllHref="/pull-requests"
                seeAllLabel={t("allContributions")}
            />

            <div className="mt-4 rounded-xl border bg-card shadow-sm overflow-hidden">
                {isLoading && !loaded ? (
                    <>
                        <PRRowSkeleton />
                        <PRRowSkeleton />
                        <PRRowSkeleton />
                    </>
                ) : prs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
                        <GitMerge className="h-8 w-8 text-muted-foreground/30" />
                        <p className="text-sm text-muted-foreground">
                            {t("noOpenContributions")}
                        </p>
                    </div>
                ) : (
                    <div
                        className={cn(isLoading && "opacity-60 transition-opacity")}
                        aria-busy={isLoading}
                    >
                        {prs.map((pr) => <PRRow key={pr.id} pr={pr} />)}
                    </div>
                )}
            </div>
        </section>
    );
}
