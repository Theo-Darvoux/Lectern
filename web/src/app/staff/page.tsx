"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  CheckCircle2,
  Crown,
  FileBox,
  Flag,
  FolderTree,
  GitPullRequest,
  HardDrive,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  Star,
  Users,
  Wrench,
  Activity,
  FileText,
  BadgeAlert,
  Archive,
  History,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/hooks/use-auth";
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

interface ModeratorStats {
  user_count: number;
  material_count: number;
  open_pr_count: number;
  open_flag_count: number;
}

interface HealthSummary {
  status: string;
  timestamp: string;
  metrics: {
    total_users: number;
    total_materials: number;
    pending_jobs: number;
    max_upload_size_mb?: number;
  };
}

export default function StaffDashboard() {
  const t = useTranslations("Staff.dashboard");
  const tStaff = useTranslations("Staff");
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const { user } = useAuth();
  const isAdmin = user?.role === "bureau" || user?.role === "vieux";

  const [overview, setOverview] = useState<Overview | null>(null);
  const [modStats, setModStats] = useState<ModeratorStats | null>(null);
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const hasLoadedRef = useRef(false);

  const loadData = useCallback(
    async (showToast = false) => {
      setRefreshing(true);
      try {
        if (isAdmin) {
          const [ovData, healthData] = await Promise.all([
            apiFetch<Overview>("/admin/overview"),
            apiFetch<HealthSummary>("/admin/health").catch(() => null),
          ]);
          setOverview(ovData);
          if (healthData) setHealth(healthData);
        } else {
          // Moderator role
          const [ovData, statsData] = await Promise.all([
            apiFetch<Overview>("/admin/overview").catch(() => null),
            apiFetch<ModeratorStats>("/moderator/stats").catch(() => null),
          ]);
          if (ovData) setOverview(ovData);
          if (statsData) setModStats(statsData);
        }
        if (showToast) toast.success(t("updated"));
      } catch {
        if (showToast || !hasLoadedRef.current) toast.error(t("failed"));
      } finally {
        hasLoadedRef.current = true;
        setLoading(false);
        setRefreshing(false);
      }
    },
    [isAdmin, t],
  );

  useEffect(() => {
    void loadData(false);
    const interval = setInterval(() => void loadData(false), 30_000);
    return () => clearInterval(interval);
  }, [loadData]);

  if (loading && !overview && !modStats) {
    return (
      <div className="space-y-6">
        <div className="h-28 animate-pulse rounded-2xl bg-muted/60" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-muted/50" />
          ))}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-48 animate-pulse rounded-xl bg-muted/40" />
          ))}
        </div>
      </div>
    );
  }

  // Attention queues calculation
  const openPrs =
    overview?.attention.open_pull_requests ?? modStats?.open_pr_count ?? 0;
  const openFlags =
    overview?.attention.moderation_flags ?? modStats?.open_flag_count ?? 0;
  const pendingUsers = isAdmin ? (overview?.attention.pending_users ?? 0) : 0;
  const failedJobs = isAdmin ? (overview?.attention.failed_jobs ?? 0) : 0;

  const totalAttention = isAdmin
    ? pendingUsers + openPrs + openFlags + failedJobs
    : openPrs + openFlags;

  const attentionCards = [
    ...(isAdmin
      ? [
          {
            label: t("pendingUsers"),
            count: pendingUsers,
            href: "/staff/users/bulk?role=pending",
            Icon: Users,
            className:
              "border-amber-300/60 bg-amber-500/5 text-amber-700 dark:text-amber-300 hover:border-amber-400/80",
          },
        ]
      : []),
    {
      label: t("openPrs"),
      count: openPrs,
      href: "/staff/pull-requests",
      Icon: GitPullRequest,
      className:
        "border-blue-300/60 bg-blue-500/5 text-blue-700 dark:text-blue-300 hover:border-blue-400/80",
    },
    {
      label: t("openFlags"),
      count: openFlags,
      href: "/staff/flags",
      Icon: Flag,
      className:
        "border-red-300/60 bg-red-500/5 text-red-700 dark:text-red-300 hover:border-red-400/80",
    },
    ...(isAdmin
      ? [
          {
            label: t("failedJobs"),
            count: failedJobs,
            href: "/staff/dlq",
            Icon: AlertTriangle,
            className:
              "border-orange-300/60 bg-orange-500/5 text-orange-700 dark:text-orange-300 hover:border-orange-400/80",
          },
        ]
      : []),
  ];

  // Metric stats
  const totalUsersCount =
    health?.metrics.total_users ?? modStats?.user_count ?? 0;
  const totalMaterialsCount =
    health?.metrics.total_materials ??
    modStats?.material_count ??
    overview?.content.total ??
    0;

  // Content lifecycle statuses
  const contentStatuses = [
    {
      key: "important" as const,
      label: fr ? "Important" : "Important",
      Icon: Star,
      iconColor: "text-amber-500 dark:text-amber-400",
      iconBg: "bg-amber-500/10",
      className:
        "border-border/60 bg-card/60 hover:bg-amber-500/5 hover:border-amber-500/30",
    },
    {
      key: "current" as const,
      label: fr ? "À jour" : "Current",
      Icon: CheckCircle2,
      iconColor: "text-emerald-500 dark:text-emerald-400",
      iconBg: "bg-emerald-500/10",
      className:
        "border-border/60 bg-card/60 hover:bg-emerald-500/5 hover:border-emerald-500/30",
    },
    {
      key: "deprecated" as const,
      label: fr ? "Obsolète" : "Deprecated",
      Icon: History,
      iconColor: "text-stone-500 dark:text-stone-400",
      iconBg: "bg-stone-500/10",
      className:
        "border-border/60 bg-card/60 hover:bg-stone-500/5 hover:border-stone-500/30",
    },
    {
      key: "archived" as const,
      label: fr ? "Archivé" : "Archived",
      Icon: Archive,
      iconColor: "text-slate-400 dark:text-slate-500",
      iconBg: "bg-slate-500/10",
      className:
        "border-border/60 bg-card/60 hover:bg-slate-500/5 hover:border-slate-500/30 opacity-80 hover:opacity-100",
    },
  ];

  return (
    <div className="space-y-8 pb-10">
      {/* Top Banner Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-bold tracking-tight">{t("title")}</h2>
            {totalAttention > 0 && (
              <Badge variant="destructive" className="rounded-full px-2.5">
                {totalAttention}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-2 self-start rounded-full"
          onClick={() => void loadData(true)}
          disabled={refreshing}
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          {t("refresh")}
        </Button>
      </div>

      {/* Attention / All Clear Section */}
      {totalAttention === 0 ? (
        <div className="flex items-center gap-3 rounded-2xl border border-emerald-300/60 bg-emerald-500/5 p-4 text-emerald-800 dark:text-emerald-300">
          <CheckCircle2 className="h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold text-sm">{t("healthy")}</p>
            <p className="text-xs opacity-80">{t("healthyDesc")}</p>
          </div>
        </div>
      ) : (
        <section className="space-y-3">
          <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
            {t("attention")}
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {attentionCards.map(({ label, count, href, Icon, className }) => (
              <Link key={href} href={href} className="group">
                <Card
                  className={cn(
                    "h-full transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
                    className,
                  )}
                >
                  <CardContent className="flex items-center gap-4 p-4">
                    <div className="rounded-xl bg-background/80 p-2.5 shadow-xs">
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
      )}

      {/* Metrics Row */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-card/50">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("metrics.totalUsers")}
            </CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tabular-nums">{totalUsersCount}</div>
          </CardContent>
        </Card>

        <Card className="bg-card/50">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("metrics.totalMaterials")}
            </CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tabular-nums">{totalMaterialsCount}</div>
          </CardContent>
        </Card>

        <Card className="bg-card/50">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("metrics.openPrs")}
            </CardTitle>
            <GitPullRequest className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tabular-nums">{openPrs}</div>
          </CardContent>
        </Card>

        <Card className="bg-card/50">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("metrics.openFlags")}
            </CardTitle>
            <Flag className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tabular-nums">{openFlags}</div>
          </CardContent>
        </Card>
      </div>

      {/* Active Queues & Workflows */}
      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">{t("recent.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2">
            <Queue
              title={t("recent.reviews")}
              href="/staff/pull-requests"
              empty={t("recent.empty")}
              openQueue={t("recent.openQueue")}
              items={(overview?.recent.open_pull_requests ?? []).map((item) => ({
                id: item.id,
                primary: item.title,
                secondary: new Date(item.updated_at).toLocaleDateString(locale),
                link: `/pull-requests/${item.id}`,
              }))}
            />

            <Queue
              title={t("recent.reports")}
              href="/staff/flags"
              empty={t("recent.empty")}
              openQueue={t("recent.openQueue")}
              items={(overview?.recent.moderation_flags ?? []).map((item) => ({
                id: item.id,
                primary: item.reason,
                secondary: `${item.target_type} · ${new Date(item.created_at).toLocaleDateString(locale)}`,
                link: "/staff/flags",
              }))}
            />

            {isAdmin && (
              <div className="sm:col-span-2 pt-2 border-t">
                <Queue
                  title={t("recent.registrations")}
                  href="/staff/users/bulk?role=pending"
                  empty={t("recent.empty")}
                  openQueue={t("recent.openQueue")}
                  items={(overview?.recent.pending_users ?? []).map((item) => ({
                    id: item.id,
                    primary: item.display_name || item.email,
                    secondary: item.display_name
                      ? item.email
                      : new Date(item.created_at).toLocaleDateString(locale),
                    link: "/staff/users/bulk?role=pending",
                  }))}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {/* Content Lifecycle / System Glance Side Card */}
        <div className="space-y-4">
          {isAdmin && overview?.content && (
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">
                      {tStaff("nav.content")}
                    </CardTitle>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {fr ? "Cycle de vie" : "Lifecycle overview"}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-xl font-black tabular-nums">
                      {overview.content.total}
                    </p>
                    <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      {fr ? "Total" : "Total"}
                    </p>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {contentStatuses.map(
                  ({ key, label, Icon, iconColor, iconBg, className }) => (
                    <Link
                      key={key}
                      href={`/staff/content?status=${key}`}
                      className={cn(
                        "group flex items-center gap-3 rounded-xl border p-2 text-xs font-medium transition-all duration-150",
                        className,
                      )}
                    >
                      <div
                        className={cn(
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors",
                          iconBg,
                          iconColor,
                        )}
                      >
                        <Icon className="h-3.5 w-3.5" />
                      </div>
                      <span className="flex-1 font-medium text-foreground/90 group-hover:text-foreground">
                        {label}
                      </span>
                      <span className="text-xs font-bold tabular-nums text-muted-foreground group-hover:text-foreground">
                        {overview.content[key] ?? 0}
                      </span>
                    </Link>
                  ),
                )}
              </CardContent>
            </Card>
          )}

          {isAdmin && health && (
            <Card className="bg-card/50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <ServerCog className="h-4 w-4 text-primary" />
                    <CardTitle className="text-base">
                      {t("systemGlance.title")}
                    </CardTitle>
                  </div>
                  <Badge
                    variant={
                      health.status === "healthy"
                        ? "default"
                        : health.status === "degraded"
                          ? "secondary"
                          : "destructive"
                    }
                    className="text-[10px] uppercase font-bold"
                  >
                    {health.status === "healthy"
                      ? (fr ? "Opérationnel" : "Healthy")
                      : health.status === "degraded"
                        ? (fr ? "Dégradé" : "Degraded")
                        : (fr ? "Critique" : "Unhealthy")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  {t("systemGlance.allHealthy")}
                </p>
                <Button asChild variant="outline" size="sm" className="w-full gap-2 text-xs">
                  <Link href="/staff/system">
                    {t("systemGlance.viewSystem")}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Quick Action Navigation Grid */}
      <section className="space-y-4 pt-2">
        <h3 className="text-lg font-bold tracking-tight">
          {t("quickActions.title")}
        </h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <QuickActionCard
            title={t("quickActions.content.title")}
            description={t("quickActions.content.description")}
            href="/staff/content"
            icon={BookOpenCheck}
          />
          <QuickActionCard
            title={t("quickActions.pullRequests.title")}
            description={t("quickActions.pullRequests.description")}
            href="/staff/pull-requests"
            icon={GitPullRequest}
          />
          <QuickActionCard
            title={t("quickActions.flags.title")}
            description={t("quickActions.flags.description")}
            href="/staff/flags"
            icon={Flag}
          />
          <QuickActionCard
            title={t("quickActions.featured.title")}
            description={t("quickActions.featured.description")}
            href="/staff/content?tab=featured"
            icon={Star}
          />
          <QuickActionCard
            title={t("quickActions.directories.title")}
            description={t("quickActions.directories.description")}
            href="/staff/content?tab=directories"
            icon={FolderTree}
          />

          {isAdmin && (
            <>
              <QuickActionCard
                title={t("quickActions.users.title")}
                description={t("quickActions.users.description")}
                href="/staff/users"
                icon={Users}
              />
              <QuickActionCard
                title={t("quickActions.system.title")}
                description={t("quickActions.system.description")}
                href="/staff/system"
                icon={ServerCog}
              />
              <QuickActionCard
                title={t("quickActions.tools.title")}
                description={t("quickActions.tools.description")}
                href="/staff/tools"
                icon={Wrench}
              />
              <QuickActionCard
                title={t("quickActions.backup.title")}
                description={t("quickActions.backup.description")}
                href="/staff/backup"
                icon={Archive}
              />
              <QuickActionCard
                title={t("quickActions.dlq.title")}
                description={t("quickActions.dlq.description")}
                href="/staff/dlq"
                icon={AlertTriangle}
              />
            </>
          )}
        </div>
      </section>
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
  items: Array<{ id: string; primary: string; secondary: string; link?: string }>;
  empty: string;
  openQueue: string;
}) {
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
        <Link
          href={href}
          className="text-[11px] font-semibold text-primary hover:underline"
        >
          {openQueue}
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="rounded-lg bg-muted/30 px-3 py-4 text-center text-xs text-muted-foreground">
          {empty}
        </p>
      ) : (
        <div className="divide-y rounded-lg border bg-card/40">
          {items.map((item) => (
            <div key={item.id} className="p-2.5 hover:bg-muted/30 transition-colors">
              {item.link ? (
                <Link href={item.link} className="block group">
                  <p className="truncate text-sm font-medium group-hover:text-primary transition-colors">
                    {item.primary}
                  </p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {item.secondary}
                  </p>
                </Link>
              ) : (
                <>
                  <p className="truncate text-sm font-medium">{item.primary}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {item.secondary}
                  </p>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QuickActionCard({
  title,
  description,
  href,
  icon: Icon,
}: {
  title: string;
  description: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Link href={href} className="group">
      <Card className="h-full border transition-all duration-200 hover:border-primary/30 hover:bg-primary/[0.02] hover:shadow-md">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-muted/60 transition-colors group-hover:bg-primary/10 group-hover:text-primary">
              <Icon className="h-4.5 w-4.5" />
            </div>
            <ArrowRight className="h-4 w-4 opacity-0 transition-all -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 text-primary" />
          </div>
          <CardTitle className="mt-3 text-base font-bold transition-colors group-hover:text-primary">
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <CardDescription className="text-xs font-normal leading-relaxed text-muted-foreground group-hover:text-foreground/80">
            {description}
          </CardDescription>
        </CardContent>
      </Card>
    </Link>
  );
}
