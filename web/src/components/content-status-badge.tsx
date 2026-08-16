"use client";

import { useEffect, useState } from "react";
import { Archive, CheckCircle2, ChevronDown, History, Loader2, Star } from "lucide-react";
import { useLocale } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/hooks/use-auth";
import { isStaff } from "@/lib/guest";
import { apiFetch } from "@/lib/api-client";
import { useBrowseRefreshStore, useUIStore } from "@/lib/stores";
import { cn } from "@/lib/utils";

export type ContentStatus = "important" | "current" | "deprecated" | "archived";

export const CONTENT_STATUSES: ContentStatus[] = [
  "important",
  "current",
  "deprecated",
  "archived",
];

export const CONTENT_STATUS_RANK: Record<ContentStatus, number> = {
  important: 0,
  current: 1,
  deprecated: 2,
  archived: 3,
};

export function normalizeContentStatus(value: unknown): ContentStatus {
  const status = String(value ?? "current").toLowerCase();
  return CONTENT_STATUSES.includes(status as ContentStatus)
    ? (status as ContentStatus)
    : "current";
}

export function getContentStatusRank(status: unknown): number {
  const s = normalizeContentStatus(status);
  return CONTENT_STATUS_RANK[s] ?? 1;
}

export function compareMaterialStatus(
  aStatus: unknown,
  bStatus: unknown,
): number {
  return getContentStatusRank(aStatus) - getContentStatusRank(bStatus);
}

const styles: Record<ContentStatus, string> = {
  important:
    "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20",
  current:
    "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20",
  deprecated:
    "border-stone-400/30 bg-stone-500/10 text-stone-700 dark:border-stone-600/30 dark:bg-stone-800/30 dark:text-stone-300 hover:bg-stone-500/20",
  archived:
    "border-slate-400/25 bg-slate-500/10 text-slate-600 dark:border-slate-700/30 dark:bg-slate-800/30 dark:text-slate-400 hover:bg-slate-500/20",
};

export const CONTENT_STATUS_ICONS = {
  important: Star,
  current: CheckCircle2,
  deprecated: History,
  archived: Archive,
};

export const CONTENT_STATUS_LABELS: Record<"en" | "fr", Record<ContentStatus, string>> = {
  fr: {
    important: "Important",
    current: "À jour",
    deprecated: "Obsolète",
    archived: "Archivé",
  },
  en: {
    important: "Important",
    current: "Current",
    deprecated: "Deprecated",
    archived: "Archived",
  },
};

export const CONTENT_STATUS_DESCRIPTIONS: Record<"en" | "fr", Record<ContentStatus, string>> = {
  fr: {
    important: "Ressource essentielle / clé",
    current: "Document actuel et valide",
    deprecated: "Programme ou version antérieur",
    archived: "Document historique archivé",
  },
  en: {
    important: "Essential key reference",
    current: "Current valid resource",
    deprecated: "Previous edition / curriculum",
    archived: "Historical archive",
  },
};

export function ContentStatusBadge({
  status: rawStatus,
  materialId,
  directoryId,
  onStatusChange,
  interactive = true,
  hideIfCurrent = false,
  className,
}: {
  status: unknown;
  materialId?: string;
  directoryId?: string;
  onStatusChange?: (newStatus: ContentStatus) => void;
  interactive?: boolean;
  hideIfCurrent?: boolean;
  className?: string;
}) {
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const lang = fr ? "fr" : "en";
  const { user } = useAuth();
  const staff = isStaff(user);

  const targetId = directoryId || materialId;
  const isDirectory = Boolean(directoryId);

  const initialStatus = normalizeContentStatus(rawStatus);
  const [currentStatus, setCurrentStatus] = useState<ContentStatus>(initialStatus);
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    setCurrentStatus(initialStatus);
  }, [initialStatus]);

  const effectiveStatus = currentStatus;
  const Icon = CONTENT_STATUS_ICONS[effectiveStatus];
  const label = CONTENT_STATUS_LABELS[lang][effectiveStatus];

  if (hideIfCurrent && effectiveStatus === "current" && !staff) {
    return null;
  }

  const handleSelectStatus = async (nextStatus: ContentStatus) => {
    if (!targetId || nextStatus === effectiveStatus || updating) return;
    const prevStatus = effectiveStatus;
    setCurrentStatus(nextStatus);
    onStatusChange?.(nextStatus);
    useUIStore.getState().updateSidebarData({ status: nextStatus });
    setUpdating(true);

    try {
      const payload = isDirectory
        ? { directory_ids: [targetId], status: nextStatus }
        : { material_ids: [targetId], status: nextStatus };

      await apiFetch("/admin/content/status", {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      useBrowseRefreshStore.getState().triggerBrowseRefresh();
      toast.success(
        fr
          ? `Statut mis à jour : ${CONTENT_STATUS_LABELS.fr[nextStatus]}`
          : `Status updated to ${CONTENT_STATUS_LABELS.en[nextStatus]}`,
      );
    } catch {
      setCurrentStatus(prevStatus);
      onStatusChange?.(prevStatus);
      useUIStore.getState().updateSidebarData({ status: prevStatus });
      toast.error(
        fr
          ? "Impossible de modifier le statut"
          : "Failed to update status",
      );
    } finally {
      setUpdating(false);
    }
  };

  const badgeContent = (
    <Badge
      variant="outline"
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1 rounded-md px-1.5 text-[10px] font-semibold tracking-normal transition-all",
        styles[effectiveStatus],
        targetId && staff && interactive && "cursor-pointer select-none hover:opacity-90",
        className,
      )}
    >
      {updating ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
      ) : (
        <Icon
          className={cn(
            "h-3 w-3 shrink-0",
            effectiveStatus === "important" && "fill-amber-500/30",
          )}
        />
      )}
      <span>{label}</span>
      {targetId && staff && interactive && (
        <ChevronDown className="h-2.5 w-2.5 opacity-60 ml-0.5" />
      )}
    </Badge>
  );

  if (!targetId || !staff || !interactive) {
    return badgeContent;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        asChild
        onClick={(e) => {
          e.stopPropagation();
        }}
      >
        <button
          type="button"
          className="focus:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-md inline-flex items-center"
          title={
            fr
              ? isDirectory
                ? "Modifier le statut du dossier"
                : "Modifier le statut du document"
              : isDirectory
                ? "Change folder status"
                : "Change material status"
          }
          aria-label={
            fr
              ? isDirectory
                ? "Modifier le statut du dossier"
                : "Modifier le statut du document"
              : isDirectory
                ? "Change folder status"
                : "Change material status"
          }
        >
          {badgeContent}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-56 p-1.5"
        onClick={(e) => e.stopPropagation()}
      >
        <DropdownMenuLabel className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground px-2 py-1">
          {fr ? "Modifier le statut" : "Change status"}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {CONTENT_STATUSES.map((st) => {
          const StIcon = CONTENT_STATUS_ICONS[st];
          const isSelected = effectiveStatus === st;
          return (
            <DropdownMenuItem
              key={st}
              onClick={() => handleSelectStatus(st)}
              className={cn(
                "flex items-start gap-2 px-2 py-1.5 text-xs rounded-md cursor-pointer",
                isSelected && "bg-primary/10 font-semibold text-primary",
              )}
            >
              <div
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded",
                  st === "important" && "text-amber-600 bg-amber-500/10",
                  st === "current" && "text-emerald-600 bg-emerald-500/10",
                  st === "deprecated" && "text-stone-600 bg-stone-500/10",
                  st === "archived" && "text-slate-500 bg-slate-500/10",
                )}
              >
                <StIcon className="h-3 w-3" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="leading-tight">{CONTENT_STATUS_LABELS[lang][st]}</p>
                <p className="text-[10px] text-muted-foreground font-normal leading-tight mt-0.5">
                  {CONTENT_STATUS_DESCRIPTIONS[lang][st]}
                </p>
              </div>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
