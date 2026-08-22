"use client";

import Link from "next/link";
import { Folder, MapPin, ArrowRight, CornerDownRight, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface PathSegment {
    name: string;
    slug?: string;
    isTemp?: boolean;
}

interface PRLocationBreadcrumbProps {
    className?: string;
    pathSegments?: PathSegment[];
    pathString?: string;
    browseUrl?: string;
    rootLabel?: string;
    isNewFolder?: boolean;
    compact?: boolean;
}

export function PRLocationBreadcrumb({
    className,
    pathSegments,
    pathString,
    browseUrl,
    rootLabel = "Root",
    isNewFolder = false,
    compact = false,
}: PRLocationBreadcrumbProps) {
    if (pathSegments && pathSegments.length > 0) {
        const segmentsWithHref = pathSegments.map((seg, i) => {
            if (!seg.slug) {
                return { ...seg, href: undefined };
            }
            const slugPath = pathSegments
                .slice(0, i + 1)
                .map((s) => s.slug)
                .filter(Boolean)
                .join("/");
            return {
                ...seg,
                href: `/browse/${slugPath}`,
            };
        });

        return (
            <nav
                aria-label="Location in wiki"
                className={cn(
                    "flex items-center flex-wrap gap-1 text-xs text-muted-foreground min-w-0",
                    className,
                )}
            >
                <MapPin className="h-3.5 w-3.5 shrink-0 opacity-60 text-primary" />
                <Link
                    href="/browse"
                    className="hover:text-foreground hover:underline transition-colors shrink-0"
                >
                    {rootLabel}
                </Link>

                {segmentsWithHref.map((seg, i) => {
                    const isLast = i === segmentsWithHref.length - 1;
                    const href = seg.href;

                    return (
                        <span key={i} className="inline-flex items-center gap-1 min-w-0">
                            <span className="opacity-40">›</span>
                            {href && !seg.isTemp ? (
                                <Link
                                    href={href}
                                    className={cn(
                                        "hover:text-foreground hover:underline truncate transition-colors max-w-[160px]",
                                        isLast && "font-medium text-foreground",
                                    )}
                                    title={seg.name}
                                >
                                    {seg.name}
                                </Link>
                            ) : (
                                <span
                                    className={cn(
                                        "truncate max-w-[160px]",
                                        isLast && "font-medium text-foreground",
                                        seg.isTemp && "text-primary italic",
                                    )}
                                    title={seg.name}
                                >
                                    {seg.name}
                                </span>
                            )}
                            {seg.isTemp && (
                                <Badge variant="secondary" className="text-[10px] px-1 py-0 h-4 font-normal gap-0.5">
                                    <Sparkles className="h-2.5 w-2.5 text-primary" />
                                    New
                                </Badge>
                            )}
                        </span>
                    );
                })}
            </nav>
        );
    }

    if (pathString) {
        const segments = pathString.split(" › ");
        return (
            <div
                className={cn(
                    "flex items-center flex-wrap gap-1 text-xs text-muted-foreground min-w-0",
                    className,
                )}
            >
                <MapPin className="h-3.5 w-3.5 shrink-0 opacity-60 text-primary" />
                {browseUrl ? (
                    <Link
                        href={browseUrl}
                        className="hover:text-foreground hover:underline truncate max-w-[280px] transition-colors"
                        title={pathString}
                    >
                        {pathString}
                    </Link>
                ) : (
                    <span className="truncate max-w-[280px]" title={pathString}>
                        {pathString}
                    </span>
                )}
                {isNewFolder && (
                    <Badge variant="secondary" className="text-[10px] px-1 py-0 h-4 font-normal gap-0.5">
                        <Sparkles className="h-2.5 w-2.5 text-primary" />
                        New
                    </Badge>
                )}
            </div>
        );
    }

    return (
        <div className={cn("flex items-center gap-1 text-xs text-muted-foreground", className)}>
            <MapPin className="h-3.5 w-3.5 shrink-0 opacity-60" />
            <span>{rootLabel}</span>
        </div>
    );
}

interface PRMoveTransitionProps {
    originPath?: string;
    originUrl?: string;
    destPath?: string;
    destUrl?: string;
    rootLabel?: string;
    originLabel?: string;
    destLabel?: string;
    className?: string;
}

export function PRMoveTransition({
    originPath,
    originUrl = "/browse",
    destPath,
    destUrl = "/browse",
    rootLabel = "Root",
    originLabel = "Origin",
    destLabel = "Destination",
    className,
}: PRMoveTransitionProps) {
    const finalOrigin = originPath || rootLabel;
    const finalDest = destPath || rootLabel;

    return (
        <div className={cn("flex flex-col sm:flex-row sm:items-center gap-2 p-2.5 rounded-lg border bg-muted/30 text-xs", className)}>
            {/* Origin */}
            <div className="flex-1 min-w-0 flex items-center gap-2">
                <span className="font-semibold text-[11px] uppercase tracking-wider text-muted-foreground shrink-0 w-20">
                    {originLabel}:
                </span>
                <div className="flex items-center gap-1 min-w-0 text-muted-foreground">
                    <Folder className="h-3.5 w-3.5 shrink-0 text-amber-500/70" />
                    {originUrl ? (
                        <Link href={originUrl} className="truncate hover:underline hover:text-foreground">
                            {finalOrigin}
                        </Link>
                    ) : (
                        <span className="truncate">{finalOrigin}</span>
                    )}
                </div>
            </div>

            {/* Arrow */}
            <div className="flex items-center justify-center shrink-0 px-1 text-muted-foreground">
                <ArrowRight className="h-4 w-4 hidden sm:block text-primary" />
                <CornerDownRight className="h-4 w-4 sm:hidden text-primary" />
            </div>

            {/* Destination */}
            <div className="flex-1 min-w-0 flex items-center gap-2">
                <span className="font-semibold text-[11px] uppercase tracking-wider text-primary shrink-0 w-20">
                    {destLabel}:
                </span>
                <div className="flex items-center gap-1 min-w-0 text-foreground font-medium">
                    <Folder className="h-3.5 w-3.5 shrink-0 text-green-500" />
                    {destUrl ? (
                        <Link href={destUrl} className="truncate hover:underline text-primary">
                            {finalDest}
                        </Link>
                    ) : (
                        <span className="truncate">{finalDest}</span>
                    )}
                </div>
            </div>
        </div>
    );
}
