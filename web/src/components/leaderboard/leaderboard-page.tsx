"use client";

import { useCallback, useEffect, useState } from "react";
import { Award, ChevronLeft, ChevronRight, Highlighter, RefreshCw, ShieldCheck, Sparkles, Trophy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useAuth } from "@/hooks/use-auth";
import { LeaderboardList } from "./leaderboard-list";
import type { LeaderboardPeriod, LeaderboardResponse } from "./types";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const PERIODS: LeaderboardPeriod[] = ["month", "semester", "all_time"];
const PAGE_SIZE = 20;

export function LeaderboardPage() {
  const t = useTranslations("Leaderboard");
  const { user } = useAuth();
  const [period, setPeriod] = useState<LeaderboardPeriod>("month");
  const [academicYear, setAcademicYear] = useState("all");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const load = useCallback((signal: AbortSignal) => {
    setLoading(true);
    setError(false);
    const params = new URLSearchParams({
      period,
      page: String(page),
      limit: String(PAGE_SIZE),
    });
    if (academicYear !== "all") params.set("academic_year", academicYear);

    apiFetch<LeaderboardResponse>(`/leaderboard?${params}`, { signal })
      .then(setData)
      .catch(() => {
        if (!signal.aborted) setError(true);
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false);
      });
  }, [academicYear, page, period]);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, reloadToken]);

  useEffect(() => {
    document.title = t("pageTitle");
  }, [t]);

  const changePeriod = (next: LeaderboardPeriod) => {
    if (next === period) return;
    setLoading(true);
    setError(false);
    setPeriod(next);
    setPage(1);
  };

  const changeYear = (next: string) => {
    if (next === academicYear) return;
    setLoading(true);
    setError(false);
    setAcademicYear(next);
    setPage(1);
  };

  const changePage = (next: number) => {
    setLoading(true);
    setError(false);
    setPage(next);
  };

  const retry = () => {
    setLoading(true);
    setError(false);
    setReloadToken((value) => value + 1);
  };

  return (
    <main className="min-h-full bg-background px-4 py-6 pb-24 sm:px-6 sm:py-8 sm:pb-10 lg:px-8">
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <section className="relative overflow-hidden rounded-3xl border bg-card p-5 shadow-sm sm:p-8">
          <div className="pointer-events-none absolute inset-0 bg-linear-to-br from-amber-500/14 via-transparent to-violet-500/8" />
          <div className="relative grid gap-6 md:grid-cols-[minmax(0,1fr)_17rem] md:items-center">
            <div>
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-500/15 text-amber-600 dark:text-amber-300">
                <Trophy className="h-6 w-6" />
              </div>
              <p className="mt-5 text-xs font-semibold uppercase tracking-[0.18em] text-amber-700 dark:text-amber-300">{t("eyebrow")}</p>
              <h1 className="mt-1 text-3xl font-bold tracking-tight sm:text-4xl">{t("title")}</h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">{t("description")}</p>
            </div>

            <div
              className={cn(
                "rounded-2xl border bg-background/75 p-4 backdrop-blur-sm",
                loading && data && "opacity-60 transition-opacity",
              )}
              aria-busy={loading}
            >
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("yourStanding")}</p>
              {loading && !data ? (
                <div className="mt-4 space-y-2"><Skeleton className="h-10 w-24" /><Skeleton className="h-4 w-36" /></div>
              ) : data?.current_user ? (
                <>
                  <div className="mt-3 flex items-end justify-between gap-4">
                    <span className="text-4xl font-bold tracking-tight tabular-nums">#{data.current_user.rank}</span>
                    <span className="inline-flex items-center gap-1 text-xl font-bold text-primary tabular-nums"><Sparkles className="h-4 w-4" />{data.current_user.score}</span>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">{t("periodStanding", { period: t(`periods.${data.period}`) })}</p>
                </>
              ) : (
                <p className="mt-3 text-sm leading-6 text-muted-foreground">{t("notRanked")}</p>
              )}
            </div>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-2" aria-label={t("scoringTitle")}>
          <div className="flex items-center gap-4 rounded-2xl border bg-card p-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"><ShieldCheck className="h-5 w-5" /></div>
            <div><p className="font-semibold">{t("approvedPoints")}</p><p className="mt-0.5 text-xs text-muted-foreground">{t("approvedPointsDesc")}</p></div>
          </div>
          <div className="flex items-center gap-4 rounded-2xl border bg-card p-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400"><Highlighter className="h-5 w-5" /></div>
            <div><p className="font-semibold">{t("annotationPoints")}</p><p className="mt-0.5 text-xs text-muted-foreground">{t("annotationPointsDesc")}</p></div>
          </div>
        </section>

        <section className="space-y-4" aria-busy={loading}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="flex items-center gap-2 text-xl font-semibold"><Award className="h-5 w-5 text-primary" />{t("rankings")}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t("rankingsDescription")}</p>
            </div>
            <Select value={academicYear} onValueChange={changeYear}>
              <SelectTrigger className="w-full sm:w-44" aria-label={t("academicYearFilter")}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allYears")}</SelectItem>
                <SelectItem value="1A">1A</SelectItem>
                <SelectItem value="2A">2A</SelectItem>
                <SelectItem value="3A+">3A+</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl border bg-muted/40 p-1" data-leaderboard-periods>
            {PERIODS.map((value) => (
              <Button
                key={value}
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => changePeriod(value)}
                className={cn("min-w-fit flex-1", value === period && "bg-background text-foreground shadow-sm")}
                aria-pressed={value === period}
              >
                {t(`periods.${value}`)}
              </Button>
            ))}
          </div>

          {error && data && (
            <div className="flex flex-col gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive sm:flex-row sm:items-center" role="alert">
              <p className="min-w-0 flex-1">{t("loadError")}</p>
              <Button variant="outline" size="sm" className="shrink-0 gap-2" onClick={retry}><RefreshCw className="h-4 w-4" />{t("retry")}</Button>
            </div>
          )}

          {error && !data ? (
            <div className="flex flex-col items-center rounded-2xl border border-dashed py-16 text-center">
              <p className="text-sm font-medium">{t("loadError")}</p>
              <Button variant="outline" size="sm" className="mt-4 gap-2" onClick={retry}><RefreshCw className="h-4 w-4" />{t("retry")}</Button>
            </div>
          ) : loading && !data ? (
            <div className="space-y-3">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-28 rounded-2xl sm:h-24" />)}</div>
          ) : data && data.items.length > 0 ? (
            <div className={cn(loading && "opacity-60")} aria-busy={loading}>
              <LeaderboardList entries={data.items} currentUserId={user?.id} />
              <div className="mt-5 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-muted-foreground">{t("contributorsCount", { count: data.total })}</p>
                {data.pages > 1 && (
                  <div className="flex items-center gap-2 self-end sm:self-auto">
                    <Button variant="outline" size="icon" className="h-9 w-9" disabled={page <= 1 || loading} onClick={() => changePage(page - 1)} aria-label={t("previousPage")}><ChevronLeft className="h-4 w-4" /></Button>
                    <span className="min-w-16 text-center text-xs font-medium tabular-nums text-muted-foreground">{data.page} / {data.pages}</span>
                    <Button variant="outline" size="icon" className="h-9 w-9" disabled={page >= data.pages || loading} onClick={() => changePage(page + 1)} aria-label={t("nextPage")}><ChevronRight className="h-4 w-4" /></Button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center rounded-2xl border border-dashed py-16 text-center"><Trophy className="h-8 w-8 text-muted-foreground/40" /><p className="mt-3 text-sm font-medium">{t("empty")}</p><p className="mt-1 text-xs text-muted-foreground">{t("emptyDescription")}</p></div>
          )}
        </section>
      </div>
    </main>
  );
}
