"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Archive,
  ArrowRight,
  BadgeAlert,
  CheckCircle2,
  Flag,
  GitPullRequest,
  RefreshCw,
  Users,
} from "lucide-react";
import { useLocale } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

interface Overview {
  attention: {
    pending_users: number;
    open_pull_requests: number;
    moderation_flags: number;
    failed_jobs: number;
  };
  content: {
    total: number;
    important: number;
    current: number;
    deprecated: number;
    archived: number;
  };
  recent: {
    pending_users: Array<{
      id: string;
      email: string;
      display_name: string | null;
      created_at: string;
    }>;
    open_pull_requests: Array<{
      id: string;
      title: string;
      created_at: string;
      updated_at: string;
    }>;
    moderation_flags: Array<{
      id: string;
      reason: string;
      target_type: string;
      created_at: string;
    }>;
  };
}

export function OperationalCommandCenter() {
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const hasLoadedRef = useRef(false);

  const copy = useMemo(
    () =>
      fr
        ? {
            title: "Centre de commande",
            description: "Les actions qui demandent votre attention, au même endroit.",
            refresh: "Actualiser",
            updated: "Vue opérationnelle actualisée",
            failed: "Impossible de charger la vue opérationnelle",
            attention: "À traiter",
            healthy: "Aucune action urgente",
            healthyDesc: "Les files de modération et d'administration sont à jour.",
            pending: "Comptes en attente",
            prs: "Contributions à relire",
            flags: "Signalements ouverts",
            jobs: "Tâches en échec",
            recent: "Files récentes",
            registrations: "Inscriptions",
            reviews: "Relectures",
            reports: "Signalements",
            empty: "Rien en attente",
            openQueue: "Ouvrir la file",
            lifecycle: "Cycle de vie du contenu",
            lifecycleDesc: "Rendez l'état de la documentation explicite et exploitable.",
            total: "Total",
            important: "Important",
            current: "Actuel",
            deprecated: "Obsolète",
            archived: "Archivé",
            manage: "Gérer le contenu",
          }
        : {
            title: "Operations command center",
            description: "Everything that needs administrative attention, in one place.",
            refresh: "Refresh",
            updated: "Operational view refreshed",
            failed: "Could not load the operational view",
            attention: "Needs attention",
            healthy: "Nothing urgent",
            healthyDesc: "Moderation and administration queues are currently clear.",
            pending: "Pending accounts",
            prs: "Contributions to review",
            flags: "Open reports",
            jobs: "Failed jobs",
            recent: "Recent queues",
            registrations: "Registrations",
            reviews: "Reviews",
            reports: "Reports",
            empty: "Nothing waiting",
            openQueue: "Open queue",
            lifecycle: "Content lifecycle",
            lifecycleDesc: "Keep the knowledge base visibly relevant and maintainable.",
            total: "Total",
            important: "Important",
            current: "Current",
            deprecated: "Deprecated",
            archived: "Archived",
            manage: "Manage content",
          },
    [fr],
  );

  const load = useCallback(async (notify = false) => {
    setRefreshing(true);
    try {
      const overview = await apiFetch<Overview>("/admin/overview");
      setData(overview);
      if (notify) toast.success(copy.updated);
    } catch {
      if (notify || !hasLoadedRef.current) toast.error(copy.failed);
    } finally {
      hasLoadedRef.current = true;
      setLoading(false);
      setRefreshing(false);
    }
  }, [copy.failed, copy.updated]);

  useEffect(() => {
    void load(false);
    const id = window.setInterval(() => void load(false), 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  if (loading && !data) {
    return (
      <div className="space-y-5">
        <div className="h-20 animate-pulse rounded-2xl bg-muted" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="h-28 animate-pulse rounded-xl bg-muted" />
          ))}
        </div>
        <div className="h-72 animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }

  const attention = data?.attention ?? {
    pending_users: 0,
    open_pull_requests: 0,
    moderation_flags: 0,
    failed_jobs: 0,
  };
  const attentionTotal = Object.values(attention).reduce((sum, value) => sum + value, 0);

  const cards = [
    {
      label: copy.pending,
      count: attention.pending_users,
      href: "/admin/users/bulk?role=pending",
      Icon: Users,
      className: "border-amber-300/60 bg-amber-500/5 text-amber-700 dark:text-amber-300",
    },
    {
      label: copy.prs,
      count: attention.open_pull_requests,
      href: "/admin/pull-requests",
      Icon: GitPullRequest,
      className: "border-blue-300/60 bg-blue-500/5 text-blue-700 dark:text-blue-300",
    },
    {
      label: copy.flags,
      count: attention.moderation_flags,
      href: "/admin/flags",
      Icon: Flag,
      className: "border-red-300/60 bg-red-500/5 text-red-700 dark:text-red-300",
    },
    {
      label: copy.jobs,
      count: attention.failed_jobs,
      href: "/admin/dlq",
      Icon: AlertTriangle,
      className: "border-orange-300/60 bg-orange-500/5 text-orange-700 dark:text-orange-300",
    },
  ];

  const statuses = [
    {
      key: "important" as const,
      label: copy.important,
      Icon: BadgeAlert,
      className: "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300",
    },
    {
      key: "current" as const,
      label: copy.current,
      Icon: CheckCircle2,
      className: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    },
    {
      key: "deprecated" as const,
      label: copy.deprecated,
      Icon: AlertTriangle,
      className: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    },
    {
      key: "archived" as const,
      label: copy.archived,
      Icon: Archive,
      className: "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-300",
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-bold tracking-tight">{copy.title}</h2>
            {attentionTotal > 0 && (
              <Badge variant="destructive" className="rounded-full">
                {attentionTotal}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{copy.description}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-2 self-start"
          onClick={() => void load(true)}
          disabled={refreshing}
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          {copy.refresh}
        </Button>
      </div>

      {attentionTotal === 0 && (
        <div className="flex items-center gap-3 rounded-xl border border-emerald-300/60 bg-emerald-500/5 p-4 text-emerald-800 dark:text-emerald-300">
          <CheckCircle2 className="h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">{copy.healthy}</p>
            <p className="text-xs opacity-80">{copy.healthyDesc}</p>
          </div>
        </div>
      )}

      <section className="space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
          {copy.attention}
        </h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {cards.map(({ label, count, href, Icon, className }) => (
            <Link key={href} href={href} className="group">
              <Card className={cn("h-full transition-all hover:-translate-y-0.5 hover:shadow-md", className)}>
                <CardContent className="flex items-center gap-4 p-4">
                  <div className="rounded-xl bg-background/70 p-2.5 shadow-sm">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-2xl font-black tabular-nums">{count}</p>
                    <p className="truncate text-xs font-semibold">{label}</p>
                  </div>
                  <ArrowRight className="h-4 w-4 opacity-40 transition-transform group-hover:translate-x-1" />
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[1.35fr_0.65fr]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">{copy.recent}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 md:grid-cols-3">
            <Queue
              title={copy.registrations}
              href="/admin/users/bulk?role=pending"
              empty={copy.empty}
              openQueue={copy.openQueue}
              items={(data?.recent.pending_users ?? []).map((item) => ({
                id: item.id,
                primary: item.display_name || item.email,
                secondary: item.display_name ? item.email : new Date(item.created_at).toLocaleDateString(locale),
              }))}
            />
            <Queue
              title={copy.reviews}
              href="/admin/pull-requests"
              empty={copy.empty}
              openQueue={copy.openQueue}
              items={(data?.recent.open_pull_requests ?? []).map((item) => ({
                id: item.id,
                primary: item.title,
                secondary: new Date(item.updated_at).toLocaleDateString(locale),
              }))}
            />
            <Queue
              title={copy.reports}
              href="/admin/flags"
              empty={copy.empty}
              openQueue={copy.openQueue}
              items={(data?.recent.moderation_flags ?? []).map((item) => ({
                id: item.id,
                primary: item.reason,
                secondary: `${item.target_type} · ${new Date(item.created_at).toLocaleDateString(locale)}`,
              }))}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle className="text-lg">{copy.lifecycle}</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">{copy.lifecycleDesc}</p>
              </div>
              <div className="text-right">
                <p className="text-2xl font-black tabular-nums">{data?.content.total ?? 0}</p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                  {copy.total}
                </p>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {statuses.map(({ key, label, Icon, className }) => (
              <Link
                key={key}
                href={`/admin/content?status=${key}`}
                className={cn(
                  "flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-transform hover:translate-x-0.5",
                  className,
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1 text-sm font-semibold">{label}</span>
                <span className="text-lg font-black tabular-nums">{data?.content[key] ?? 0}</span>
              </Link>
            ))}
            <Button asChild variant="outline" className="mt-3 w-full gap-2">
              <Link href="/admin/content">
                {copy.manage}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Queue({
  title,
  href,
  items,
  empty,
  openQueue,
}: {
  title: string;
  href: string;
  items: Array<{ id: string; primary: string; secondary: string }>;
  empty: string;
  openQueue: string;
}) {
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{title}</p>
        <Link href={href} className="text-[11px] font-semibold text-primary hover:underline">
          {openQueue}
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="rounded-lg bg-muted/40 px-3 py-4 text-center text-xs text-muted-foreground">
          {empty}
        </p>
      ) : (
        <div className="divide-y rounded-lg border">
          {items.map((item) => (
            <div key={item.id} className="px-3 py-2.5">
              <p className="truncate text-sm font-medium">{item.primary}</p>
              <p className="truncate text-[11px] text-muted-foreground">{item.secondary}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
