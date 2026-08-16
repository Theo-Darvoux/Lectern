"use client";

import { Archive, BadgeAlert, CheckCircle2, TriangleAlert } from "lucide-react";
import { useLocale } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type ContentStatus = "important" | "current" | "deprecated" | "archived";

export const CONTENT_STATUSES: ContentStatus[] = [
  "important",
  "current",
  "deprecated",
  "archived",
];

export function normalizeContentStatus(value: unknown): ContentStatus {
  const status = String(value ?? "current").toLowerCase();
  return CONTENT_STATUSES.includes(status as ContentStatus)
    ? (status as ContentStatus)
    : "current";
}

const styles: Record<ContentStatus, string> = {
  important:
    "border-red-300 bg-red-100 text-red-800 dark:border-red-800 dark:bg-red-950/60 dark:text-red-300",
  current:
    "border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300",
  deprecated:
    "border-amber-300 bg-amber-100 text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300",
  archived:
    "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300",
};

const icons = {
  important: BadgeAlert,
  current: CheckCircle2,
  deprecated: TriangleAlert,
  archived: Archive,
};

export function ContentStatusBadge({
  status: rawStatus,
  className,
}: {
  status: unknown;
  className?: string;
}) {
  const locale = useLocale();
  const status = normalizeContentStatus(rawStatus);
  const Icon = icons[status];
  const fr = locale.toLowerCase().startsWith("fr");
  const labels: Record<ContentStatus, string> = fr
    ? {
        important: "Important",
        current: "Actuel",
        deprecated: "Obsolète",
        archived: "Archivé",
      }
    : {
        important: "Important",
        current: "Current",
        deprecated: "Deprecated",
        archived: "Archived",
      };

  return (
    <Badge
      variant="outline"
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1 px-1.5 text-[10px] font-bold uppercase tracking-wide",
        styles[status],
        className,
      )}
    >
      <Icon className="h-3 w-3" />
      {labels[status]}
    </Badge>
  );
}
