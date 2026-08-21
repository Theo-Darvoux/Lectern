"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Sparkles, Trophy } from "lucide-react";
import { useTranslations } from "next-intl";
import { apiFetch } from "@/lib/api-client";
import type { LeaderboardEntry, LeaderboardResponse } from "./types";

export function ProfileLeaderboardRank() {
  const t = useTranslations("Leaderboard");
  const [standing, setStanding] = useState<LeaderboardEntry | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<LeaderboardResponse>("/leaderboard?period=month&limit=1&page=1", { signal: controller.signal })
      .then((response) => setStanding(response.current_user))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <Link href="/leaderboard" className="mt-4 flex items-center gap-3 rounded-xl border bg-background/70 p-3 transition-colors hover:bg-accent/50" data-profile-leaderboard>
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/12 text-amber-600 dark:text-amber-300"><Trophy className="h-4 w-4" /></div>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{t("monthlyRank")}</p>
        <div className="mt-0.5 flex items-center gap-2">
          <span className="font-bold tabular-nums">{standing ? `#${standing.rank}` : "—"}</span>
          {standing && <span className="inline-flex items-center gap-1 text-xs text-primary"><Sparkles className="h-3 w-3" />{standing.score}</span>}
        </div>
      </div>
      <ArrowRight className="h-4 w-4 text-muted-foreground" />
    </Link>
  );
}
