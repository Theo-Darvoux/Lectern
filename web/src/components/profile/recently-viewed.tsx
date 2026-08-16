"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    Clock,
    Folder,
    FileText,
    FileImage,
    FileVideo,
    FileAudio,
    FileArchive,
    FileSpreadsheet,
    FileCode,
    File,
    Lightbulb,
    ClipboardCheck,
    MessageSquare,
    Video,
    ExternalLink,
    ChevronRight,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import type React from "react";
import { useTranslations } from "next-intl";

interface RecentMaterial {
    id: string;
    title: string;
    slug: string;
    type: string;
    directory_id: string;
    directory_path?: string;
}

/* ── Reuse same visual mapping as contribution-list ── */

interface Visuals {
    icon: React.ElementType;
    color: string;
    bg: string;
    label: string;
}

const MATERIAL_TYPES: Record<string, Omit<Visuals, "label"> & { labelKey: string }> = {
    polycopie:  { icon: FileText,       color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    labelKey: "polycopie" },
    annal:      { icon: FileText,       color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", labelKey: "annal" },
    cheatsheet: { icon: FileText,       color: "text-green-600 dark:text-green-400",  bg: "bg-green-100 dark:bg-green-950/50",  labelKey: "cheatsheet" },
    tip:        { icon: Lightbulb,      color: "text-yellow-600 dark:text-yellow-400", bg: "bg-yellow-100 dark:bg-yellow-950/50", labelKey: "tip" },
    review:     { icon: ClipboardCheck, color: "text-purple-600 dark:text-purple-400", bg: "bg-purple-100 dark:bg-purple-950/50", labelKey: "review" },
    discussion: { icon: MessageSquare,  color: "text-pink-600 dark:text-pink-400",    bg: "bg-pink-100 dark:bg-pink-950/50",    labelKey: "discussion" },
    video:      { icon: Video,          color: "text-red-600 dark:text-red-400",      bg: "bg-red-100 dark:bg-red-950/50",      labelKey: "video" },
    link:       { icon: ExternalLink,   color: "text-sky-600 dark:text-sky-400",      bg: "bg-sky-100 dark:bg-sky-950/50",      labelKey: "link" },
};

const EXT_VISUALS: Record<string, Visuals> = {
    pdf:  { icon: FileText,        color: "text-red-600 dark:text-red-400",      bg: "bg-red-100 dark:bg-red-950/50",      label: "PDF" },
    doc:  { icon: FileText,        color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    label: "DOC" },
    docx: { icon: FileText,        color: "text-blue-600 dark:text-blue-400",    bg: "bg-blue-100 dark:bg-blue-950/50",    label: "DOCX" },
    xls:  { icon: FileSpreadsheet, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-950/50", label: "XLS" },
    xlsx: { icon: FileSpreadsheet, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-950/50", label: "XLSX" },
    png:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "PNG" },
    jpg:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "JPG" },
    svg:  { icon: FileImage,       color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-950/50", label: "SVG" },
    mp4:  { icon: FileVideo,       color: "text-pink-600 dark:text-pink-400",    bg: "bg-pink-100 dark:bg-pink-950/50",    label: "MP4" },
    mp3:  { icon: FileAudio,       color: "text-amber-600 dark:text-amber-400",  bg: "bg-amber-100 dark:bg-amber-950/50",  label: "MP3" },
    zip:  { icon: FileArchive,     color: "text-orange-600 dark:text-orange-400", bg: "bg-orange-100 dark:bg-orange-950/50", label: "ZIP" },
    py:   { icon: FileCode,        color: "text-sky-600 dark:text-sky-400",      bg: "bg-sky-100 dark:bg-sky-950/50",      label: "PY" },
    tex:  { icon: FileCode,        color: "text-teal-600 dark:text-teal-400",    bg: "bg-teal-100 dark:bg-teal-950/50",    label: "LaTeX" },
    epub: { icon: FileText,        color: "text-teal-600 dark:text-teal-400",    bg: "bg-teal-100 dark:bg-teal-950/50",    label: "EPUB" },
};

const DEFAULT_VIS: Omit<Visuals, "label"> & { labelKey: string } = { icon: File, color: "text-slate-500 dark:text-slate-400", bg: "bg-slate-100 dark:bg-slate-800/50", labelKey: "file" };

function getVisuals(type: string, slug: string): Visuals | (Omit<Visuals, "label"> & { labelKey: string }) {
    if (MATERIAL_TYPES[type]) return MATERIAL_TYPES[type];
    const ext = slug.split(".").pop()?.toLowerCase();
    if (ext && EXT_VISUALS[ext]) return EXT_VISUALS[ext];
    if (type && type !== "document" && type !== "other")
        return { ...DEFAULT_VIS, labelKey: "file", label: type.charAt(0).toUpperCase() + type.slice(1) } as any;
    return DEFAULT_VIS;
}

/* ── Component ── */

export function RecentlyViewed() {
    const t = useTranslations("Profile");
    const tMat = useTranslations("MaterialTypes");
    const [materials, setMaterials] = useState<RecentMaterial[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        apiFetch<RecentMaterial[]>("/users/me/recently-viewed")
            .then(setMaterials)
            .catch(() => setMaterials([]))
            .finally(() => setLoading(false));
    }, []);

    if (loading) {
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

    if (materials.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                    <Clock className="h-5 w-5 text-muted-foreground/50" />
                </div>
                <p className="text-sm font-medium text-muted-foreground">{t("noRecentHistory")}</p>
                <p className="mt-1 text-xs text-muted-foreground/80">
                    {t("recentHistoryDesc")}
                </p>
            </div>
        );
    }

    return (
        <div className="rounded-xl border overflow-hidden bg-card/50 dark:bg-card/30">
            <div className="divide-y">
                {materials.map((m, i) => {
                    const vis = getVisuals(m.type, m.slug);
                    const Icon = vis.icon;

                    return (
                        <Link
                            key={m.id}
                            href={`/browse/${m.directory_path || m.directory_id}/${m.slug}`}
                            className="block"
                        >
                            <div
                                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/40"
                                style={{ animation: `pf-fade-up 0.35s ${0.03 * i}s both ease-out` }}
                            >
                                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${vis.bg}`}>
                                    <Icon className={`h-4 w-4 ${vis.color}`} />
                                </div>

                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{m.title}</p>
                                    {m.directory_path && (
                                        <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground truncate">
                                            <Folder className="h-3 w-3 shrink-0" />
                                            <span className="truncate">{m.directory_path}</span>
                                        </div>
                                    )}
                                </div>

                                <span className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${vis.color} ${vis.bg}`}>
                                    {"labelKey" in vis ? tMat(vis.labelKey as any) : (vis as any).label}
                                </span>

                                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/30 transition-all group-hover:text-muted-foreground/70 group-hover:translate-x-0.5" />
                            </div>
                        </Link>
                    );
                })}
            </div>
        </div>
    );
}
