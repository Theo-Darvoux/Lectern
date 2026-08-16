"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import {
    ChevronLeft,
    ChevronRight,
    ChevronRight as ChevronNav,
    GitPullRequest,
    FileText,
    FileImage,
    FileVideo,
    FileAudio,
    FileArchive,
    FileSpreadsheet,
    FileCode,
    File,
    MessageSquare,
    CheckCircle2,
    CircleDashed,
    Folder,
    XCircle,
    Lightbulb,
    ClipboardCheck,
    Video,
    ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";

/* ────────────────────────────────────────────────────────────────────────── */
/*  Types                                                                     */
/* ────────────────────────────────────────────────────────────────────────── */

interface ContributionItem {
    id: string;
    title?: string;
    body?: string;
    type?: string;
    status?: string;
    slug?: string;
    created_at?: string;
    material_id?: string;
    directory_id?: string;
    directory_path?: string;
}

interface PaginatedContributions {
    items: ContributionItem[];
    total: number;
    page: number;
    pages: number;
}

interface ContributionListProps {
    userId: string;
    type: "prs" | "materials" | "annotations";
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Material type → visual mapping                                            */
/* ────────────────────────────────────────────────────────────────────────── */

interface ItemVisuals {
    icon: React.ElementType;
    color: string;
    bg: string;
    label: string;
}

const MATERIAL_TYPES: Record<string, Omit<ItemVisuals, "label"> & { labelKey: string }> = {
    polycopie:  { icon: FileText,       color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    labelKey: "polycopie" },
    annal:      { icon: FileText,       color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", labelKey: "annal" },
    cheatsheet: { icon: FileText,       color: "text-green-600 dark:text-green-400",  bg: "bg-green-100 dark:bg-green-950/50",  labelKey: "cheatsheet" },
    tip:        { icon: Lightbulb,      color: "text-yellow-600 dark:text-yellow-400", bg: "bg-yellow-100 dark:bg-yellow-950/50", labelKey: "tip" },
    review:     { icon: ClipboardCheck, color: "text-purple-600 dark:text-purple-400", bg: "bg-purple-100 dark:bg-purple-950/50", labelKey: "review" },
    discussion: { icon: MessageSquare,  color: "text-pink-600 dark:text-pink-400",    bg: "bg-pink-100 dark:bg-pink-950/50",    labelKey: "discussion" },
    video:      { icon: Video,          color: "text-red-600 dark:text-red-400",      bg: "bg-red-100 dark:bg-red-950/50",      labelKey: "video" },
    link:       { icon: ExternalLink,   color: "text-sky-600 dark:text-sky-400",      bg: "bg-sky-100 dark:bg-sky-950/50",      labelKey: "link" },
};

const EXT_VISUALS: Record<string, ItemVisuals> = {
    pdf:  { icon: FileText,        color: "text-red-600 dark:text-red-400",      bg: "bg-red-100 dark:bg-red-950/50",      label: "PDF" },
    doc:  { icon: FileText,        color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    label: "DOC" },
    docx: { icon: FileText,        color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    label: "DOCX" },
    txt:  { icon: FileText,        color: "text-slate-600 dark:text-slate-400",  bg: "bg-slate-100 dark:bg-slate-800/50",  label: "TXT" },
    xls:  { icon: FileSpreadsheet, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-950/50", label: "XLS" },
    xlsx: { icon: FileSpreadsheet, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-950/50", label: "XLSX" },
    csv:  { icon: FileSpreadsheet, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-950/50", label: "CSV" },
    png:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "PNG" },
    jpg:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "JPG" },
    jpeg: { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "JPEG" },
    gif:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "GIF" },
    webp: { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "WEBP" },
    svg:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "SVG" },
    mp4:  { icon: FileVideo,       color: "text-pink-600 dark:text-pink-400",    bg: "bg-pink-100 dark:bg-pink-950/50",    label: "MP4" },
    webm: { icon: FileVideo,       color: "text-pink-600 dark:text-pink-400",    bg: "bg-pink-100 dark:bg-pink-950/50",    label: "WEBM" },
    mp3:  { icon: FileAudio,       color: "text-amber-600 dark:text-amber-400",  bg: "bg-amber-100 dark:bg-amber-950/50",  label: "MP3" },
    zip:  { icon: FileArchive,     color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", label: "ZIP" },
    rar:  { icon: FileArchive,     color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", label: "RAR" },
    py:   { icon: FileCode,        color: "text-sky-600 dark:text-sky-400",      bg: "bg-sky-100 dark:bg-sky-950/50",      label: "PY" },
    js:   { icon: FileCode,        color: "text-yellow-600 dark:text-yellow-400", bg: "bg-yellow-100 dark:bg-yellow-950/50", label: "JS" },
    ts:   { icon: FileCode,        color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    label: "TS" },
    tex:  { icon: FileCode,        color: "text-teal-600 dark:text-teal-400",    bg: "bg-teal-100 dark:bg-teal-950/50",    label: "LaTeX" },
    html: { icon: FileCode,        color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", label: "HTML" },
    json: { icon: FileCode,        color: "text-zinc-600 dark:text-zinc-400",    bg: "bg-zinc-100 dark:bg-zinc-800/50",    label: "JSON" },
    epub: { icon: FileText,        color: "text-teal-600 dark:text-teal-400",    bg: "bg-teal-100 dark:bg-teal-950/50",    label: "EPUB" },
};

const DEFAULT_MATERIAL: Omit<ItemVisuals, "label"> & { labelKey: string } = { icon: File, color: "text-slate-500 dark:text-slate-400", bg: "bg-slate-100 dark:bg-slate-800/50", labelKey: "file" };

function getMaterialVisuals(type?: string, slug?: string, title?: string): ItemVisuals | (Omit<ItemVisuals, "label"> & { labelKey: string }) {
    if (type && MATERIAL_TYPES[type]) return MATERIAL_TYPES[type];

    /* For "document" or unknown types, detect from slug or title extension */
    const source = slug || title || "";
    const extMatch = source.match(/\.([a-z0-9]+)$/i);
    if (extMatch) {
        const ext = extMatch[1].toLowerCase();
        if (EXT_VISUALS[ext]) return EXT_VISUALS[ext];
    }

    if (type && type !== "document" && type !== "other") {
        return { ...DEFAULT_MATERIAL, labelKey: "file", label: type.charAt(0).toUpperCase() + type.slice(1) } as any;
    }

    return DEFAULT_MATERIAL;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  PR status visuals                                                         */
/* ────────────────────────────────────────────────────────────────────────── */

function getPRVisuals(status: string | undefined, t: any) {
    const s = status?.toLowerCase();
    if (s === "open")
        return { icon: CircleDashed, color: "text-green-600 dark:text-green-400", bg: "bg-green-100 dark:bg-green-950/50", label: t("statusOpen"), pillClass: "bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400" };
    if (s === "merged" || s === "approved")
        return { icon: CheckCircle2, color: "text-purple-600 dark:text-purple-400", bg: "bg-purple-100 dark:bg-purple-950/50", label: t("statusMerged"), pillClass: "bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-400" };
    if (s === "closed" || s === "rejected")
        return { icon: XCircle, color: "text-red-600 dark:text-red-400", bg: "bg-red-100 dark:bg-red-950/50", label: t("statusClosed"), pillClass: "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400" };
    return { icon: GitPullRequest, color: "text-blue-600 dark:text-blue-400", bg: "bg-blue-100 dark:bg-blue-950/50", label: status ?? "", pillClass: "bg-muted text-muted-foreground" };
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Row components                                                            */
/* ────────────────────────────────────────────────────────────────────────── */

function MaterialRow({ item, index }: { item: ContributionItem; index: number }) {
    const t = useTranslations("MaterialTypes");
    const vis = getMaterialVisuals(item.type, item.slug, item.title);
    const Icon = vis.icon;
    const title = item.title || item.id;
    const date = item.created_at ? new Date(item.created_at) : null;
    const timeAgo = date ? formatDistanceToNow(date, { addSuffix: true }) : "";
    const href = item.directory_path && item.slug ? `/browse/${item.directory_path}/${item.slug}` : null;

    const content = (
        <div
            className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/40"
            style={{ animation: `pf-fade-up 0.35s ${0.03 * index}s both ease-out` }}
        >
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${vis.bg}`}>
                <Icon className={`h-4 w-4 ${vis.color}`} />
            </div>

            <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{title}</p>
                <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground truncate">
                    {item.directory_path && (
                        <>
                            <Folder className="h-3 w-3 shrink-0" />
                            <span className="truncate">{item.directory_path}</span>
                            <span className="shrink-0">&middot;</span>
                        </>
                    )}
                    {timeAgo && <span className="shrink-0">{timeAgo}</span>}
                </div>
            </div>

            <span className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${vis.color} ${vis.bg}`}>
                {"labelKey" in vis ? t(vis.labelKey as any) : (vis as any).label}
            </span>

            <ChevronNav className="h-4 w-4 shrink-0 text-muted-foreground/30 transition-all group-hover:text-muted-foreground/70 group-hover:translate-x-0.5" />
        </div>
    );

    if (href) return <Link href={href} className="block">{content}</Link>;
    return content;
}

function PRRow({ item, index }: { item: ContributionItem; index: number }) {
    const t = useTranslations("Profile");
    const vis = getPRVisuals(item.status, t);
    const Icon = vis.icon;
    const title = item.title || item.id;
    const date = item.created_at ? new Date(item.created_at) : null;
    const timeAgo = date ? formatDistanceToNow(date, { addSuffix: true }) : "";
    const shortId = item.id.split("-")[0];

    return (
        <Link href={`/pull-requests/${item.id}`} className="block">
            <div
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/40"
                style={{ animation: `pf-fade-up 0.35s ${0.03 * index}s both ease-out` }}
            >
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${vis.bg}`}>
                    <Icon className={`h-4 w-4 ${vis.color}`} />
                </div>

                <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{title}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        <span className="font-mono">#{shortId}</span>
                        {timeAgo && <> &middot; {timeAgo}</>}
                    </p>
                </div>

                <span className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold ${vis.pillClass}`}>
                    {vis.label}
                </span>

                <ChevronNav className="h-4 w-4 shrink-0 text-muted-foreground/30 transition-all group-hover:text-muted-foreground/70 group-hover:translate-x-0.5" />
            </div>
        </Link>
    );
}

function AnnotationRow({ item, index }: { item: ContributionItem; index: number }) {
    const body = item.body
        ? item.body.length > 120
            ? item.body.slice(0, 120) + "\u2026"
            : item.body
        : item.title ?? item.id;
    const date = item.created_at ? new Date(item.created_at) : null;
    const timeAgo = date ? formatDistanceToNow(date, { addSuffix: true }) : "";

    return (
        <div
            className="flex items-center gap-3 px-4 py-3"
            style={{ animation: `pf-fade-up 0.35s ${0.03 * index}s both ease-out` }}
        >
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-violet-100 dark:bg-violet-950/50">
                <MessageSquare className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>

            <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground/80 italic line-clamp-2 leading-snug">
                    &ldquo;{body}&rdquo;
                </p>
                {timeAgo && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{timeAgo}</p>
                )}
            </div>
        </div>
    );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Empty states                                                              */
/* ────────────────────────────────────────────────────────────────────────── */

const EMPTY_CONFIG = {
    prs: { icon: GitPullRequest, label: "No contributions yet" },
    materials: { icon: FileText, label: "No materials yet" },
    annotations: { icon: MessageSquare, label: "No annotations yet" },
};

/* ────────────────────────────────────────────────────────────────────────── */
/*  Main list                                                                 */
/* ────────────────────────────────────────────────────────────────────────── */

export function ContributionList({ userId, type }: ContributionListProps) {
    const t = useTranslations("Profile");
    const [items, setItems] = useState<ContributionItem[]>([]);
    const [page, setPage] = useState(1);
    const [pages, setPages] = useState(1);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);

    const fetchContributions = useCallback(
        async (p: number) => {
            setLoading(true);
            try {
                const params = new URLSearchParams({ page: String(p), limit: "8", type });
                const data = await apiFetch<PaginatedContributions>(
                    `/users/${userId}/contributions?${params}`
                );
                setItems(data.items);
                setPage(data.page);
                setPages(data.pages);
                setTotal(data.total);
            } catch {
                setItems([]);
            } finally {
                setLoading(false);
            }
        },
        [userId, type]
    );

    useEffect(() => {
        fetchContributions(1);
    }, [fetchContributions]);

    /* Loading skeleton */
    if (loading && items.length === 0) {
        return (
            <div className="rounded-xl border divide-y overflow-hidden">
                {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-3">
                        <Skeleton className="h-9 w-9 rounded-lg" />
                        <div className="flex-1 space-y-1.5">
                            <Skeleton className="h-4 w-3/5" />
                            <Skeleton className="h-3 w-2/5" />
                        </div>
                        <Skeleton className="h-5 w-10 rounded-md" />
                    </div>
                ))}
            </div>
        );
    }

    /* Empty state */
    if (items.length === 0) {
        const EMPTY_CONFIG: Record<string, { icon: any, label: string }> = {
            prs: { icon: GitPullRequest, label: t("noContributionsYet") },
            materials: { icon: FileText, label: t("noMaterialsYet") },
            annotations: { icon: MessageSquare, label: t("noAnnotationsYet") },
        };
        const empty = EMPTY_CONFIG[type];
        const EmptyIcon = empty.icon;
        return (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                    <EmptyIcon className="h-5 w-5 text-muted-foreground/50" />
                </div>
                <p className="text-sm font-medium text-muted-foreground">{empty.label}</p>
            </div>
        );
    }

    const RowComponent =
        type === "prs" ? PRRow : type === "annotations" ? AnnotationRow : MaterialRow;

    return (
        <div className="rounded-xl border overflow-hidden bg-card/50 dark:bg-card/30">
            <div className="divide-y">
                {items.map((item, i) => (
                    <RowComponent key={item.id} item={item} index={i} />
                ))}
            </div>

            {/* Pagination footer */}
            {pages > 1 && (
                <div className="flex items-center justify-between border-t bg-muted/30 px-4 py-2.5">
                    <p className="text-xs text-muted-foreground">
                        {type === "prs" ? t("totalContributions", { count: total }) : 
                         type === "annotations" ? t("totalAnnotations", { count: total }) : 
                         t("totalMaterials", { count: total })}
                    </p>
                    <div className="flex items-center gap-1">
                        <Button
                            variant="ghost"
                            size="icon"
                            disabled={page <= 1 || loading}
                            onClick={() => fetchContributions(page - 1)}
                            className="h-7 w-7"
                        >
                            <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <span className="min-w-[36px] text-center text-xs font-medium text-muted-foreground tabular-nums">
                            {page}/{pages}
                        </span>
                        <Button
                            variant="ghost"
                            size="icon"
                            disabled={page >= pages || loading}
                            onClick={() => fetchContributions(page + 1)}
                            className="h-7 w-7"
                        >
                            <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
