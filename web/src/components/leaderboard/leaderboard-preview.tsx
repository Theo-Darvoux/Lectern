"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Sparkles, Trophy } from "lucide-react";
import { useTranslations } from "next-intl";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionHeader } from "@/components/home/section-header";
import { useAuth } from "@/hooks/use-auth";
import { API_BASE, apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { LeaderboardResponse } from "./types";

export function LeaderboardPreview() {
  const t = useTranslations("Leaderboard");
  const { user } = useAuth();
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<LeaderboardResponse>("/leaderboard?period=month&limit=5&page=1", { signal: controller.signal })
      .then(setData)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  return (
    <section aria-label={t("homeTitle")}>
      <SectionHeader title={t("homeTitle")} icon={<Trophy className="h-4 w-4" />} seeAllHref="/leaderboard" />
      <div className="mt-3 overflow-hidden rounded-2xl border bg-card shadow-sm">
        {loading ? (
          <div className="space-y-1 p-2">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-12 rounded-xl" />)}</div>
        ) : failed ? (
          <p className="p-6 text-center text-xs text-muted-foreground">{t("loadError")}</p>
        ) : !data || data.items.length === 0 ? (
          <p className="p-6 text-center text-xs text-muted-foreground">{t("empty")}</p>
        ) : (
          <div className="divide-y">
            {data.items.map((entry) => {
              const initials = (entry.display_name ?? "?").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
              return (
                <Link key={entry.user_id} href={`/profile/${entry.user_id}`} className={cn("flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-muted/50", entry.user_id === user?.id && "bg-primary/5")}>
                  <span className={cn("w-6 text-center text-xs font-bold tabular-nums", entry.rank <= 3 ? "text-amber-600 dark:text-amber-300" : "text-muted-foreground")}>#{entry.rank}</span>
                  <Avatar className="h-8 w-8 border">
                    <AvatarImage src={entry.avatar_url ? `${API_BASE}/users/${entry.user_id}/avatar?v=${encodeURIComponent(entry.avatar_url)}` : undefined} alt="" />
                    <AvatarFallback className="text-[10px]">{initials}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{entry.display_name ?? t("anonymous")}</p><p className="text-[10px] text-muted-foreground">{t("approvedCount", { count: entry.approved_contributions })}</p></div>
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-primary tabular-nums"><Sparkles className="h-3 w-3" />{entry.score}</span>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
