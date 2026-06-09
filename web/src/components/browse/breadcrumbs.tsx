"use client";

import Link from "next/link";
import { ChevronRight, Home } from "lucide-react";

import { useTranslations } from "next-intl";

interface BreadcrumbItem {
    id: string;
    name: string;
    slug: string;
}

interface BreadcrumbsProps {
    items: BreadcrumbItem[];
    /** When set, appended as ?preview_pr= to preserve preview mode on breadcrumb navigation */
    previewPrId?: string;
    /** If true, the last item in the breadcrumbs will be rendered as a link */
    linkLast?: boolean;
    /** If true, renders with larger text for use as a primary header */
    large?: boolean;
    children?: React.ReactNode;
}

// Max items to show before collapsing to Home > … > last N
const COLLAPSE_THRESHOLD = 3;
const TAIL_COUNT = 2;

export function Breadcrumbs({ items, previewPrId, linkLast = false, large = false, children }: BreadcrumbsProps) {
    const t = useTranslations("Navigation");
    const buildPath = (index: number) => {
        const segments = items.slice(0, index + 1).map((item) => item.slug);
        const base = `/browse/${segments.join("/")}`;
        return previewPrId ? `${base}?preview_pr=${previewPrId}` : base;
    };

    const rootHref = previewPrId ? `/browse?preview_pr=${previewPrId}` : "/browse";

    const collapsed = items.length > COLLAPSE_THRESHOLD;
    // Which items to actually render: when collapsed, only the last TAIL_COUNT
    const visibleItems = collapsed ? items.slice(-TAIL_COUNT) : items;
    // Global index of the first visible item (needed for buildPath)
    const visibleOffset = collapsed ? items.length - TAIL_COUNT : 0;

    return (
        <nav data-tutorial="breadcrumb" className={`flex items-center gap-1 min-w-0 ${large ? "text-lg sm:text-xl" : "text-sm"}`}>
            <Link href={rootHref} className="flex items-center shrink-0 text-muted-foreground hover:text-foreground gap-1.5">
                <Home className={large ? "h-5 w-5" : "h-4 w-4"} />
                {large && items.length === 0 && <span className="font-bold tracking-tight text-foreground">{t("home")}</span>}
            </Link>

            {collapsed && (
                <span className="flex items-center gap-1 shrink-0">
                    <ChevronRight className={large ? "h-4 w-4 text-muted-foreground" : "h-3.5 w-3.5 text-muted-foreground"} />
                    <span
                        className="text-muted-foreground select-none"
                        title={items
                            .slice(0, items.length - TAIL_COUNT)
                            .map((i) => i.name)
                            .join(" › ")}
                    >
                        …
                    </span>
                </span>
            )}

            {visibleItems.map((item, localIndex) => {
                const globalIndex = visibleOffset + localIndex;
                const isLast = globalIndex === items.length - 1;
                const shouldLink = !isLast || linkLast;

                return (
                    <span key={item.id} className="flex items-center gap-1 min-w-0 max-w-full">
                        <ChevronRight className={large ? "h-4 w-4 shrink-0 text-muted-foreground" : "h-3.5 w-3.5 shrink-0 text-muted-foreground"} />
                        {shouldLink ? (
                            <Link
                                href={buildPath(globalIndex)}
                                className="truncate text-muted-foreground hover:text-foreground"
                            >
                                {item.name}
                            </Link>
                        ) : (
                            <span className="truncate font-bold tracking-tight">{item.name}</span>
                        )}
                        {isLast && children}
                    </span>
                );
            })}
        </nav>
    );
}
