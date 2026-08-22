"use client";

import Link from "next/link";
import { CheckCircle2, Crown, Highlighter, Medal, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { API_BASE } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { LeaderboardEntry } from "./types";

function getRankStyle(rank: number) {
  if (rank === 1) {
    return {
      icon: Crown,
      rankClass: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
      rowClass: "border-amber-500/30 bg-linear-to-r from-amber-500/8 via-card to-card",
    };
  }
  if (rank === 2) {
    return {
      icon: Medal,
      rankClass: "bg-slate-400/15 text-slate-600 dark:text-slate-300",
      rowClass: "border-slate-400/25 bg-linear-to-r from-slate-400/7 via-card to-card",
    };
  }
  if (rank === 3) {
    return {
      icon: Medal,
      rankClass: "bg-orange-700/12 text-orange-700 dark:text-orange-300",
      rowClass: "border-orange-700/20 bg-linear-to-r from-orange-700/7 via-card to-card",
    };
  }
  return {
    icon: null,
    rankClass: "bg-muted text-muted-foreground",
    rowClass: "bg-card",
  };
}

export function LeaderboardList({
  entries,
  currentUserId,
}: {
  entries: LeaderboardEntry[];
  currentUserId?: string;
}) {
  const t = useTranslations("Leaderboard");

  return (
    <div className="space-y-3" data-leaderboard-list>
      {entries.map((entry) => {
        const style = getRankStyle(entry.rank);
        const RankIcon = style.icon;
        const isCurrent = entry.user_id === currentUserId;
        const initials = (entry.display_name ?? "?")
          .split(" ")
          .map((part) => part[0])
          .join("")
          .slice(0, 2)
          .toUpperCase();

        return (
          <article
            key={entry.user_id}
            className={cn(
              "grid grid-cols-[2.5rem_minmax(0,1fr)] items-center gap-3 rounded-2xl border p-3 shadow-sm transition-[border-color,box-shadow] hover:border-primary/25 hover:shadow-md sm:grid-cols-[3rem_minmax(0,1fr)_auto] sm:gap-4 sm:p-4",
              style.rowClass,
              isCurrent && "ring-2 ring-primary/20",
            )}
            data-leaderboard-entry
            data-current-user={isCurrent}
          >
            <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl text-sm font-bold tabular-nums sm:h-12 sm:w-12", style.rankClass)}>
              {RankIcon ? <RankIcon className="h-5 w-5" aria-label={t("rank", { rank: entry.rank })} /> : `#${entry.rank}`}
            </div>

            <Link href={`/profile/${entry.user_id}`} className="group flex min-w-0 items-center gap-3 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Avatar className="h-10 w-10 shrink-0 border sm:h-11 sm:w-11">
                <AvatarImage
                  src={entry.avatar_url ? `${API_BASE}/users/${entry.user_id}/avatar?v=${encodeURIComponent(entry.avatar_url)}` : undefined}
                  alt=""
                />
                <AvatarFallback className="text-xs font-semibold">{initials}</AvatarFallback>
              </Avatar>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate font-semibold group-hover:underline">{entry.display_name ?? t("anonymous")}</p>
                  {isCurrent && <Badge variant="secondary" className="text-[10px]">{t("you")}</Badge>}
                  {entry.academic_year && <Badge variant="outline" className="text-[10px]">{entry.academic_year}</Badge>}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground sm:text-xs">
                  <span className="inline-flex items-center gap-1">
                    <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
                    {t("approvedCount", { count: entry.approved_contributions })}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Highlighter className="h-3 w-3 text-amber-600 dark:text-amber-400" />
                    {t("annotationCount", { count: entry.annotations })}
                  </span>
                </div>
              </div>
            </Link>

            <div className="col-start-2 flex items-baseline justify-between gap-3 border-t pt-3 sm:col-start-auto sm:block sm:border-0 sm:pt-0 sm:text-right">
              <span className="text-xs font-medium text-muted-foreground sm:hidden">{t("contributionScore")}</span>
              <div className="flex items-center gap-1.5 text-primary sm:justify-end">
                <Sparkles className="h-4 w-4" />
                <span className="text-xl font-bold tabular-nums sm:text-2xl">{entry.score.toLocaleString()}</span>
              </div>
              <p className="mt-0.5 hidden text-[11px] text-muted-foreground sm:block">{t("points")}</p>
            </div>
          </article>
        );
      })}
    </div>
  );
}
