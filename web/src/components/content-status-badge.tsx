"use client";

import { useEffect, useState } from "react";
import { Archive, CheckCircle2, ChevronDown, History, Loader2, Star } from "lucide-react";
import { useTranslations } from "next-intl";
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
import { useUIStore } from "@/lib/stores";
import { refreshBrowse } from "@/lib/browse-prefetch";
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

/** Translated label and description of each content status. */
export function useContentStatusText() {
  const t = useTranslations("ContentStatus");
  return {
    label: (status: ContentStatus) => t(`labels.${status}`),
    description: (status: ContentStatus) => t(`descriptions.${status}`),
  };
}

/**
 * Set the status of a folder or material, refresh the views showing it and
 * report the outcome. Resolves to whether the change was saved.
 */
export function useUpdateContentStatus() {
  const t = useTranslations("ContentStatus");
  return async (
    target: { type: "directory" | "material"; id: string },
    status: ContentStatus,
  ): Promise<boolean> => {
    try {
      await apiFetch("/admin/content/status", {
        method: "PATCH",
        body: JSON.stringify(
          target.type === "directory"
            ? { directory_ids: [target.id], status }
            : { material_ids: [target.id], status },
        ),
      });
      showStatusInSidebar(target.id, status);
      refreshBrowse();
      toast.success(t("updated", { status: t(`labels.${status}`) }));
      return true;
    } catch {
      toast.error(t("updateFailed"));
      return false;
    }
  };
}

/** Reflect a status in the sidebar, but only when it shows that same item. */
function showStatusInSidebar(targetId: string, status: ContentStatus): void {
  const { sidebarTarget, updateSidebarData } = useUIStore.getState();
  if (sidebarTarget?.id === targetId) updateSidebarData({ status });
}

const STATUS_ICON_COLORS: Record<ContentStatus, string> = {
  important: "text-amber-500",
  current: "text-emerald-500",
  deprecated: "text-stone-500",
  archived: "text-slate-400",
};

/** Staff grid of status buttons for the sidebar details of a folder or material. */
export function ContentStatusPicker({
  target,
  status: rawStatus,
  heading,
}: {
  target: { type: "directory" | "material"; id: string };
  status: unknown;
  heading: React.ReactNode;
}) {
  const t = useTranslations("ContentStatus");
  const { label } = useContentStatusText();
  const updateStatus = useUpdateContentStatus();
  const current = normalizeContentStatus(rawStatus);

  return (
    <>
      <div className="flex items-center justify-between">
        {heading}
        <span className="text-[10px] text-muted-foreground font-medium">
          {t(target.type === "directory" ? "folderStatus" : "documentStatus")}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        {CONTENT_STATUSES.map((st) => {
          const StIcon = CONTENT_STATUS_ICONS[st];
          const isSelected = current === st;
          return (
            <button
              key={st}
              type="button"
              onClick={() => {
                if (!isSelected) void updateStatus(target, st);
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs font-medium transition-all text-left",
                isSelected
                  ? "border-primary/50 bg-primary/10 font-bold text-foreground shadow-2xs"
                  : "border-border/60 bg-muted/20 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <StIcon className={cn("h-3.5 w-3.5 shrink-0", STATUS_ICON_COLORS[st])} />
              <span className="truncate">{label(st)}</span>
            </button>
          );
        })}
      </div>
    </>
  );
}

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
  const t = useTranslations("ContentStatus");
  const { label: statusLabel, description: statusDescription } = useContentStatusText();
  const updateStatus = useUpdateContentStatus();
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
  const label = statusLabel(effectiveStatus);

  if (hideIfCurrent && effectiveStatus === "current" && !staff) {
    return null;
  }

  const handleSelectStatus = async (nextStatus: ContentStatus) => {
    if (!targetId || nextStatus === effectiveStatus || updating) return;
    const prevStatus = effectiveStatus;
    // Optimistic: shown at once, rolled back if the server refuses.
    setCurrentStatus(nextStatus);
    onStatusChange?.(nextStatus);
    setUpdating(true);
    const saved = await updateStatus(
      { type: isDirectory ? "directory" : "material", id: targetId },
      nextStatus,
    );
    if (!saved) {
      setCurrentStatus(prevStatus);
      onStatusChange?.(prevStatus);
    }
    setUpdating(false);
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
          title={t(isDirectory ? "changeFolder" : "changeMaterial")}
          aria-label={t(isDirectory ? "changeFolder" : "changeMaterial")}
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
          {t("change")}
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
                <p className="leading-tight">{statusLabel(st)}</p>
                <p className="text-[10px] text-muted-foreground font-normal leading-tight mt-0.5">
                  {statusDescription(st)}
                </p>
              </div>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
