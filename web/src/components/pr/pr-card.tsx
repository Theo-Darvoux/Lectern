"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  GitPullRequest,
  FilePlus,
  FilePenLine,
  FileX,
  FolderPlus,
  FolderPen,
  FolderX,
  ArrowRightLeft,
  ChevronRight,
  Undo2,
  Layers,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { type PullRequestOut } from "@/components/home/types";
import { useTranslations, useLocale } from "next-intl";
import { fr, enUS } from "date-fns/locale";
import { cn } from "@/lib/utils";

const OP_ICONS: Record<string, React.ElementType> = {
  create_material: FilePlus,
  new: FilePlus,
  edit_material: FilePenLine,
  update: FilePenLine,
  delete_material: FileX,
  delete: FileX,
  create_directory: FolderPlus,
  edit_directory: FolderPen,
  delete_directory: FolderX,
  move_item: ArrowRightLeft,
  batch: Layers,
  revert: Undo2,
};

interface PullRequestProps {
  pr: PullRequestOut;
}

export function PRCard({ pr }: PullRequestProps) {
  const t = useTranslations("PRs");
  const locale = useLocale();
  const dateLocale = locale === "fr" ? fr : enUS;
  const router = useRouter();

  const isApproved = pr.status === "approved";
  const isOpen = pr.status === "open";
  const isRejected = pr.status === "rejected";
  const isCancelled = pr.status === "cancelled";

  // Extract distinct operations from payload if present, or fallback to summary_types
  const rawPayload = (pr as unknown as { payload?: Array<Record<string, unknown>> }).payload;
  const derivedOpTypes: string[] = Array.isArray(rawPayload) && rawPayload.length > 0
    ? Array.from(new Set(rawPayload.map((op) => String(op.op || op.pr_type || "batch"))))
    : pr.summary_types && pr.summary_types.length > 0
      ? pr.summary_types
      : [pr.type];

  const totalOpsCount = Array.isArray(rawPayload) && rawPayload.length > 0
    ? rawPayload.length
    : derivedOpTypes.length > 1
      ? derivedOpTypes.length
      : 1;

  const initials = pr.author?.display_name
    ? pr.author.display_name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase()
    : "?";

  const statusConfig = isOpen
    ? {
        labelKey: "pending",
        iconColor: "text-emerald-500",
        pill: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
      }
    : isApproved
      ? {
          labelKey: "approved",
          iconColor: "text-purple-500",
          pill: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
        }
      : isRejected
        ? {
            labelKey: "rejected",
            iconColor: "text-rose-500",
            pill: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
          }
        : {
            labelKey: "cancelled",
            iconColor: "text-muted-foreground",
            pill: "bg-muted text-muted-foreground border-border",
          };

  return (
    <article className="group relative flex items-center justify-between gap-3 sm:gap-4 rounded-xl border border-border/70 bg-card p-3 sm:px-4 sm:py-3.5 transition-colors hover:bg-muted/30 hover:border-border">
      {/* ── Left Status Icon & Main Info ───────────────────── */}
      <div className="flex items-start sm:items-center gap-3 min-w-0 flex-1">
        {/* Status Icon */}
        <div className="flex shrink-0 items-center justify-center pt-0.5 sm:pt-0">
          <GitPullRequest className={cn("h-4 w-4 sm:h-4.5 sm:w-4.5", statusConfig.iconColor)} />
        </div>

        {/* Content Details */}
        <div className="min-w-0 flex-1 space-y-1">
          {/* Top Line: Title & Badges */}
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm sm:text-base font-semibold tracking-tight text-foreground group-hover:text-primary transition-colors">
              <Link
                href={`/pull-requests/${pr.id}`}
                className="focus:outline-none after:absolute after:inset-0"
              >
                {pr.title}
              </Link>
            </h3>

            {/* Status Badge */}
            <span
              className={cn(
                "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium border leading-none",
                statusConfig.pill,
              )}
            >
              {t(statusConfig.labelKey as any)}
            </span>

            {/* Revert Badges */}
            {pr.reverted_by_pr_id && (
              <Badge
                variant="outline"
                className="h-4.5 px-1.5 text-[10px] font-medium border-amber-500/30 text-amber-600 bg-amber-500/10 dark:text-amber-400"
              >
                <Undo2 className="h-2.5 w-2.5 mr-0.5" />
                {t("reverted")}
              </Badge>
            )}
            {pr.type === "revert" && (
              <Badge
                variant="outline"
                className="h-4.5 px-1.5 text-[10px] font-medium border-blue-500/30 text-blue-600 bg-blue-500/10 dark:text-blue-400"
              >
                <Undo2 className="h-2.5 w-2.5 mr-0.5" />
                {t("revert")}
              </Badge>
            )}

            {/* Operation Chips */}
            {derivedOpTypes.map((st) => {
              const Icon = OP_ICONS[st] ?? FilePlus;
              const hasKey = typeof (t as unknown as { has?: (k: string) => boolean }).has === "function"
                ? (t as unknown as { has: (k: string) => boolean }).has(`operations.${st}`)
                : true;
              const label = hasKey
                ? t(`operations.${st}` as any)
                : st.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
              return (
                <span
                  key={st}
                  className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground border border-border/40"
                >
                  <Icon className="h-2.5 w-2.5 opacity-70" />
                  <span>{label}</span>
                </span>
              );
            })}

            {totalOpsCount > 1 && derivedOpTypes.length === 1 && (
              <span className="text-[10px] text-muted-foreground font-medium bg-muted/40 px-1.5 py-0.5 rounded">
                {t("operationsCount", { count: totalOpsCount })}
              </span>
            )}
          </div>

          {/* Bottom Subtitle Line: #id · Author · Timestamp · Optional snippet */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground flex-wrap">
            <span className="font-mono text-[11px] opacity-75">#{pr.id.slice(0, 7)}</span>
            <span>·</span>
            <span>
              {pr.author?.id ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    router.push(`/profile/${pr.author!.id}`);
                  }}
                  className="relative z-10 font-medium text-foreground hover:underline cursor-pointer"
                >
                  {pr.author.display_name || t("anonymous")}
                </button>
              ) : (
                <span>{pr.author?.display_name || t("anonymous")}</span>
              )}
            </span>
            <span>·</span>
            <span>
              {formatDistanceToNow(new Date(pr.created_at), {
                addSuffix: true,
                locale: dateLocale,
              })}
            </span>

            {pr.description && (
              <>
                <span className="hidden md:inline">·</span>
                <span className="hidden md:inline truncate max-w-sm opacity-70">
                  {pr.description}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Right Section: Author Avatar & Chevron ─────────── */}
      <div className="flex items-center gap-2.5 shrink-0">
        {pr.author?.id ? (
          <Avatar size="sm" className="h-6 w-6">
            <AvatarFallback className="text-[10px] bg-muted text-muted-foreground font-medium">
              {initials}
            </AvatarFallback>
          </Avatar>
        ) : null}

        <ChevronRight className="h-4 w-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
      </div>
    </article>
  );
}
