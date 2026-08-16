"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Cpu,
  Database,
  HardDrive,
  Mail,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api-client";
import { cn, formatBytes } from "@/lib/utils";

// Types
interface ServiceStatus {
  status: "healthy" | "degraded" | "unhealthy";
  message?: string;
  latency_ms?: number;
  metadata?: Record<string, any>;
}

interface HealthData {
  status: string;
  timestamp: string;
  services: Record<string, ServiceStatus>;
  metrics: {
    total_users: number;
    total_materials: number;
    pending_jobs: number;
    max_upload_size_mb?: number;
    google_auth_enabled?: boolean;
  };
}

interface OrphanObject {
  key: string;
  size: number;
}

interface ReconciliationData {
  stats: {
    total_s3_bytes: number;
    total_s3_objects: number;
    orphaned_bytes: number;
  };
  orphans: {
    cas: OrphanObject[];
    thumbnails: OrphanObject[];
  };
  missing: {
    cas: string[];
    thumbnails: string[];
  };
}

export function StorageReconciliationModal() {
  const t = useTranslations("Admin.Dashboard.reconciliation");
  const [reconciling, setReconciling] = useState(false);
  const [data, setData] = useState<ReconciliationData | null>(null);
  const [pruning, setPruning] = useState(false);

  const runReconcile = async () => {
    setReconciling(true);
    try {
      const res = await apiFetch<ReconciliationData>("/admin/storage/reconcile");
      setData(res);
    } catch (err) {
      console.error(err);
      toast.error(t("failed"));
    } finally {
      setReconciling(false);
    }
  };

  const prune = async () => {
    if (!data?.orphans) return;
    const keys = [
      ...data.orphans.cas.map((o) => o.key),
      ...data.orphans.thumbnails.map((o) => o.key),
    ];

    if (keys.length === 0) {
      toast.info(t("noOrphans"));
      return;
    }

    setPruning(true);
    try {
      await apiFetch("/admin/storage/prune", {
        method: "POST",
        body: JSON.stringify(keys),
      });
      toast.success(t("success", { count: keys.length }));
      setData(null);
    } catch (err) {
      console.error(err);
      toast.error(t("pruneFailed"));
    } finally {
      setPruning(false);
    }
  };

  const totalOrphans =
    (data?.orphans?.cas?.length || 0) + (data?.orphans?.thumbnails?.length || 0);
  const totalMissing =
    (data?.missing?.cas?.length || 0) + (data?.missing?.thumbnails?.length || 0);

  return (
    <Dialog onOpenChange={(open) => !open && setData(null)}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="w-full mt-4 h-8 text-[11px] font-bold uppercase tracking-wider gap-2 hover:bg-primary/5 hover:text-primary transition-all"
        >
          <RefreshCw className={cn("h-3 w-3", reconciling && "animate-spin")} />
          {t("button")}
        </Button>
      </DialogTrigger>
      <DialogContent className="w-[calc(100vw-2rem)] sm:max-w-2xl max-h-[90vh] overflow-y-auto bg-card/95 backdrop-blur-xl border-muted shadow-2xl p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl sm:text-2xl font-black">
            <HardDrive className="h-5 w-5 sm:h-6 sm:w-6 text-primary shrink-0" />
            <span>{t("title")}</span>
          </DialogTitle>
          <DialogDescription className="text-xs sm:text-sm font-medium opacity-80">
            {t("description")}
          </DialogDescription>
        </DialogHeader>

        {!data ? (
          <div className="py-12 sm:py-16 flex flex-col items-center justify-center gap-6">
            <div className="relative">
              <div className="absolute inset-0 animate-ping rounded-full bg-primary/20" />
              <div className="relative h-14 w-14 sm:h-16 sm:w-16 rounded-full bg-primary/10 flex items-center justify-center border border-primary/20">
                <Search className="h-7 w-7 sm:h-8 sm:w-8 text-primary" />
              </div>
            </div>
            <div className="text-center space-y-1 px-4">
              <p className="text-base font-bold text-foreground">
                {t("ready.title")}
              </p>
              <p className="text-xs text-muted-foreground max-w-[280px] mx-auto">
                {t("ready.description")}
              </p>
            </div>
            <Button
              onClick={runReconcile}
              disabled={reconciling}
              className="gap-2 px-6 sm:px-8 h-10 sm:h-11 rounded-full font-bold shadow-lg shadow-primary/20"
            >
              {reconciling ? (
                <RefreshCw className="h-4 w-4 sm:h-5 sm:w-5 animate-spin" />
              ) : (
                <Activity className="h-4 w-4 sm:h-5 sm:w-5" />
              )}
              {reconciling ? t("ready.scanning") : t("ready.start")}
            </Button>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
              <div className="relative overflow-hidden p-3.5 sm:p-4 rounded-2xl bg-muted/40 border border-muted/50 group transition-all hover:bg-muted/60">
                <p className="text-[10px] font-black uppercase tracking-[0.15em] text-muted-foreground/60 mb-1">
                  {t("stats.s3Total")}
                </p>
                <p className="text-lg sm:text-xl font-black">
                  {formatBytes(data.stats.total_s3_bytes)}
                </p>
                <p className="text-[10px] font-bold text-muted-foreground opacity-70">
                  {t("stats.objects", { count: data.stats.total_s3_objects })}
                </p>
              </div>
              <div
                className={cn(
                  "relative overflow-hidden p-3.5 sm:p-4 rounded-2xl border transition-all group",
                  totalOrphans > 0
                    ? "bg-amber-500/10 border-amber-500/30 hover:bg-amber-500/15"
                    : "bg-muted/40 border-muted/50 hover:bg-muted/60",
                )}
              >
                <p className="text-[10px] font-black uppercase tracking-[0.15em] text-muted-foreground/60 mb-1">
                  {t("stats.orphans")}
                </p>
                <p
                  className={cn(
                    "text-lg sm:text-xl font-black",
                    totalOrphans > 0 && "text-amber-500",
                  )}
                >
                  {formatBytes(data.stats.orphaned_bytes)}
                </p>
                <p className="text-[10px] font-bold text-muted-foreground opacity-70">
                  {t("stats.unused", { count: totalOrphans })}
                </p>
              </div>
              <div
                className={cn(
                  "relative overflow-hidden p-3.5 sm:p-4 rounded-2xl border transition-all group",
                  totalMissing > 0
                    ? "bg-red-500/10 border-red-500/30 hover:bg-red-500/15"
                    : "bg-muted/40 border-muted/50 hover:bg-muted/60",
                )}
              >
                <p className="text-[10px] font-black uppercase tracking-[0.15em] text-muted-foreground/60 mb-1">
                  {t("stats.missing")}
                </p>
                <p
                  className={cn(
                    "text-lg sm:text-xl font-black",
                    totalMissing > 0 && "text-red-500",
                  )}
                >
                  {totalMissing}
                </p>
                <p className="text-[10px] font-bold text-muted-foreground opacity-70">
                  {t("stats.broken")}
                </p>
              </div>
            </div>

            {totalOrphans > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between px-1">
                  <p className="text-[11px] font-black uppercase tracking-[0.2em] text-muted-foreground/50">
                    {t("orphans.title")}
                  </p>
                  <Badge
                    variant="outline"
                    className="h-5 text-[9px] font-bold bg-amber-500/5 text-amber-500 border-amber-500/20 uppercase tracking-tighter"
                  >
                    {t("orphans.actionRequired")}
                  </Badge>
                </div>
                <ScrollArea className="h-[200px] sm:h-[240px] w-full rounded-2xl border border-muted/50 bg-muted/20 p-2 shadow-inner">
                  <div className="space-y-1 pr-3">
                    {data.orphans.cas.map((o) => (
                      <div
                        key={o.key}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 p-2.5 rounded-xl hover:bg-muted/40 transition-all border border-transparent hover:border-muted/50"
                      >
                        <div className="flex flex-col gap-0.5 flex-1 min-w-0 mr-0 sm:mr-4">
                          <span className="text-[11px] font-mono font-medium text-foreground/80 break-all">
                            {o.key}
                          </span>
                          <span className="text-[9px] font-bold uppercase tracking-wide text-muted-foreground/50">
                            {t("orphans.cas")}
                          </span>
                        </div>
                        <Badge
                          variant="outline"
                          className="h-5 sm:h-6 text-[10px] font-black bg-background/50 border-muted/50 tabular-nums self-start sm:self-auto shrink-0"
                        >
                          {formatBytes(o.size)}
                        </Badge>
                      </div>
                    ))}
                    {data.orphans.thumbnails.map((o) => (
                      <div
                        key={o.key}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 p-2.5 rounded-xl hover:bg-muted/40 transition-all border border-transparent hover:border-muted/50"
                      >
                        <div className="flex flex-col gap-0.5 flex-1 min-w-0 mr-0 sm:mr-4">
                          <span className="text-[11px] font-mono font-medium text-foreground/80 break-all">
                            {o.key}
                          </span>
                          <span className="text-[9px] font-bold uppercase tracking-wide text-muted-foreground/50">
                            {t("orphans.thumbnails")}
                          </span>
                        </div>
                        <Badge
                          variant="outline"
                          className="h-5 sm:h-6 text-[10px] font-black bg-background/50 border-muted/50 tabular-nums self-start sm:self-auto shrink-0"
                        >
                          {formatBytes(o.size)}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}

            {totalMissing > 0 && (
              <div className="relative overflow-hidden p-4 rounded-2xl bg-red-500/5 border border-red-500/20 group">
                <div className="absolute -right-4 -top-4 h-16 w-16 opacity-5 group-hover:scale-110 transition-transform">
                  <AlertTriangle className="h-full w-full text-red-500" />
                </div>
                <div className="flex items-center gap-3 text-red-500 mb-2">
                  <div className="h-8 w-8 rounded-full bg-red-500/10 flex items-center justify-center border border-red-500/20 shrink-0">
                    <AlertTriangle className="h-4 w-4" />
                  </div>
                  <p className="text-xs font-black uppercase tracking-widest">
                    {t("critical.title")}
                  </p>
                </div>
                <p className="text-[11px] text-foreground/70 leading-relaxed font-medium break-words">
                  {t("critical.description", { count: totalMissing })}
                </p>
              </div>
            )}
          </div>
        )}

        <DialogFooter className="flex flex-col-reverse sm:flex-row gap-2 sm:gap-3 pt-3">
          <Button
            variant="ghost"
            onClick={() => setData(null)}
            disabled={reconciling || pruning}
            className="h-10 px-6 rounded-full font-bold w-full sm:w-auto"
          >
            {t("reset")}
          </Button>
          {data && totalOrphans > 0 && (
            <Button
              variant="destructive"
              onClick={prune}
              disabled={pruning}
              className="h-10 px-6 rounded-full font-black uppercase tracking-tighter gap-2 shadow-lg shadow-destructive/20 w-full sm:w-auto"
            >
              {pruning ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <HardDrive className="h-4 w-4" />
              )}
              {pruning ? t("pruning") : t("prune")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function StaffSystemPage() {
  const t = useTranslations("Admin.Dashboard");
  const { user } = useAuth();
  const isAdmin = user?.role === "bureau" || user?.role === "vieux";

  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchHealth = async (showToast = false) => {
    if (!isAdmin) return;
    setRefreshing(true);
    try {
      const data = await apiFetch<HealthData>("/admin/health");
      setHealth(data);
      if (showToast) toast.success(t("status.updated"));
    } catch (err) {
      console.error("Failed to fetch health", err);
      if (showToast) toast.error(t("status.refreshFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(() => fetchHealth(false), 30000);
    return () => clearInterval(interval);
  }, []);

  if (!isAdmin) {
    return (
      <div className="flex items-center justify-center p-12 text-muted-foreground">
        Access restricted to administrators.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="h-[180px] w-full animate-pulse rounded-2xl bg-muted/50" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="h-[140px] animate-pulse rounded-xl bg-muted/40"
            />
          ))}
        </div>
      </div>
    );
  }

  const getStatusBg = (status: string) => {
    switch (status) {
      case "healthy":
        return "bg-green-500/5 border-green-500/20";
      case "degraded":
        return "bg-amber-500/5 border-amber-500/20";
      case "unhealthy":
        return "bg-red-500/5 border-red-500/20";
      default:
        return "bg-muted";
    }
  };

  return (
    <div className="space-y-8 pb-10">
      {/* Global Status Banner */}
      <div
        className={cn(
          "relative overflow-hidden rounded-2xl border p-6 sm:p-8 transition-all duration-500",
          getStatusBg(health?.status || "healthy"),
        )}
      >
        <div className="absolute -right-12 -top-12 h-64 w-64 opacity-[0.03] dark:opacity-[0.05]">
          <Activity className="h-full w-full" />
        </div>

        <div className="relative z-10 flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="relative flex h-3.5 w-3.5">
                <span
                  className={cn(
                    "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
                    health?.status === "healthy"
                      ? "bg-green-400"
                      : health?.status === "degraded"
                        ? "bg-amber-400"
                        : "bg-red-400",
                  )}
                />
                <span
                  className={cn(
                    "relative inline-flex h-3.5 w-3.5 rounded-full",
                    health?.status === "healthy"
                      ? "bg-green-500"
                      : health?.status === "degraded"
                        ? "bg-amber-500"
                        : "bg-red-500",
                  )}
                />
              </div>
              <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
                {health?.status === "healthy"
                  ? t("status.healthy")
                  : health?.status === "degraded"
                    ? t("status.degraded")
                    : t("status.unhealthy")}
              </h1>
            </div>
            <div className="flex items-center gap-4">
              <p className="text-muted-foreground font-medium text-xs sm:text-sm">
                {t("status.lastUpdated", {
                  time: new Date(health?.timestamp || "").toLocaleTimeString(),
                })}
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 gap-2 rounded-full px-3 text-xs font-bold hover:bg-background/50"
                onClick={() => fetchHealth(true)}
                disabled={refreshing}
              >
                <RefreshCw
                  className={cn("h-3 w-3", refreshing && "animate-spin")}
                />
                {refreshing ? t("status.refreshing") : t("status.refresh")}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-4 md:gap-8">
            <div className="space-y-1">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                {t("metrics.pendingJobs")}
              </p>
              <p className="text-2xl sm:text-3xl font-black tabular-nums">
                {health?.metrics.pending_jobs || 0}
              </p>
            </div>
            <div className="hidden h-12 w-px bg-border/50 md:block" />
            <div className="space-y-1">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                {t("metrics.totalUsers")}
              </p>
              <p className="text-2xl sm:text-3xl font-black tabular-nums">
                {health?.metrics.total_users || 0}
              </p>
            </div>
            <div className="hidden h-12 w-px bg-border/50 md:block" />
            <div className="space-y-1">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                {t("metrics.materials")}
              </p>
              <p className="text-2xl sm:text-3xl font-black tabular-nums">
                {health?.metrics.total_materials || 0}
              </p>
            </div>
            <div className="hidden h-12 w-px bg-border/50 md:block" />
            <div className="space-y-1">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                {t("metrics.maxUpload")}
              </p>
              <p className="text-2xl sm:text-3xl font-black tabular-nums">
                {health?.metrics.max_upload_size_mb || 0} MB
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Service Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <ServiceCard
          name={t("services.database.name")}
          icon={Database}
          data={health?.services.database}
          description={t("services.database.description")}
        />
        <ServiceCard
          name={t("services.redis.name")}
          icon={Activity}
          data={health?.services.redis}
          description={t("services.redis.description")}
        />
        <ServiceCard
          name={t("services.storage.name")}
          icon={HardDrive}
          data={health?.services.storage}
          description={t("services.storage.description")}
        >
          {health?.services.storage.metadata?.max_storage_bytes && (
            <div className="mt-4 space-y-1.5">
              <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-muted-foreground/70">
                <span>{t("services.storage.capacityUsed")}</span>
                <span>
                  {Math.round(
                    (((health.services.storage.metadata.usage_bytes as number) ||
                      0) /
                      (health.services.storage.metadata
                        .max_storage_bytes as number)) *
                      100,
                  )}
                  %
                </span>
              </div>
              <Progress
                value={
                  (((health.services.storage.metadata.usage_bytes as number) ||
                    0) /
                    (health.services.storage.metadata
                      .max_storage_bytes as number)) *
                  100
                }
                className="h-1.5 bg-muted"
              />
            </div>
          )}
          <StorageReconciliationModal />
        </ServiceCard>
        <ServiceCard
          name={t("services.email.name")}
          icon={Mail}
          data={health?.services.email}
          description={t("services.email.description")}
          metadata={[
            {
              label: t("services.email.host"),
              value: health?.services.email.metadata?.host,
            },
            {
              label: t("services.email.user"),
              value:
                health?.services.email.metadata?.user ||
                t("services.email.anonymous"),
            },
            {
              label: t("services.email.googleAuth"),
              value: health?.metrics.google_auth_enabled
                ? t("services.email.active")
                : t("services.email.disabled"),
            },
          ]}
        />
        <ServiceCard
          name={t("services.search.name")}
          icon={Search}
          data={health?.services.search}
          description={t("services.search.description")}
        />
        <ServiceCard
          name={t("services.workers.name")}
          icon={Cpu}
          data={health?.services.workers}
          description={t("services.workers.description")}
        />
        <ServiceCard
          name={t("services.scanner.name")}
          icon={ShieldCheck}
          data={health?.services.scanner}
          description={t("services.scanner.description")}
        />
      </div>
    </div>
  );
}

function ServiceCard({
  name,
  icon: Icon,
  data,
  description,
  metadata,
  children,
}: {
  name: string;
  icon: React.ComponentType<{ className?: string }>;
  data?: ServiceStatus;
  description: string;
  metadata?: { label: string; value: string | number | undefined }[];
  children?: React.ReactNode;
}) {
  const t = useTranslations("Admin.Dashboard.services.common");
  const tWorkers = useTranslations("Admin.Dashboard.services.workers");
  const tScanner = useTranslations("Admin.Dashboard.services.scanner");
  return (
    <Card className="group relative overflow-hidden bg-card/40 transition-all hover:bg-card/60">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-muted p-2 text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
              <Icon className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="text-base">{name}</CardTitle>
              <CardDescription className="text-[10px] uppercase tracking-wider">
                {description}
              </CardDescription>
            </div>
          </div>
          <div
            className={cn(
              "h-2 w-2 rounded-full",
              data?.status === "healthy"
                ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]"
                : data?.status === "degraded"
                  ? "bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.6)]"
                  : "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]",
            )}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
            {t("currentStatus")}
          </span>
          <Badge
            variant={
              data?.status === "healthy"
                ? "default"
                : data?.status === "degraded"
                  ? "secondary"
                  : "destructive"
            }
            className="h-5 text-[10px] font-black uppercase tracking-tighter"
          >
            {data?.status ? t(data.status as any) : t("unknown")}
          </Badge>
        </div>

        {typeof data?.latency_ms === "number" ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
                {t("responseTime")}
              </span>
              <span className="text-xs font-black tabular-nums">
                {data.latency_ms.toFixed(1)}ms
              </span>
            </div>
            <Progress
              value={Math.min(100, (data.latency_ms / 200) * 100)}
              className="h-1.5 bg-muted"
            />
          </div>
        ) : (
          <div className="h-[34px] flex items-center">
            <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/50 italic">
              {t("noMetrics")}
            </span>
          </div>
        )}

        {/* Metadata Details */}
        {data?.metadata && Object.keys(data.metadata).length > 0 && (
          <div className="grid grid-cols-2 gap-2 pt-2 border-t border-muted/50">
            {data.metadata.usage_bytes !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {t("storageUsage")}
                </p>
                <p className="text-xs font-bold">
                  {formatBytes(data.metadata.usage_bytes)}
                </p>
              </div>
            )}
            {data.metadata.active_queues !== undefined && (
              <div className="space-y-1.5 col-span-2">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {tWorkers("poolStatus")}
                </p>
                <div className="flex flex-col gap-1.5">
                  {(data.metadata.active_queues as string[]).map((q) => {
                    const counts = data.metadata?.queue_counts as
                      | Record<string, number>
                      | undefined;
                    const count = counts?.[q] || 0;
                    return (
                      <div
                        key={q}
                        className="flex items-center justify-between group/q"
                      >
                        <div className="flex items-center gap-1.5">
                          <div className="h-1 w-1 rounded-full bg-green-500" />
                          <span className="text-[11px] font-bold text-foreground/80">
                            {q.replace("arq:", "")}
                          </span>
                        </div>
                        <Badge
                          variant="outline"
                          className={cn(
                            "h-4 text-[9px] px-1.5 font-bold transition-colors",
                            count > 0
                              ? "bg-amber-500/10 text-amber-500 border-amber-500/20"
                              : "bg-muted text-muted-foreground border-transparent",
                          )}
                        >
                          {tWorkers("job", { count })}
                        </Badge>
                      </div>
                    );
                  })}
                  {(data.metadata.missing_queues as string[])?.map((q) => (
                    <div
                      key={q}
                      className="flex items-center justify-between opacity-60"
                    >
                      <div className="flex items-center gap-1.5">
                        <div className="h-1 w-1 rounded-full bg-red-500" />
                        <span className="text-[11px] font-bold text-red-500">
                          {q.replace("arq:", "")}
                        </span>
                      </div>
                      <span className="text-[9px] font-bold uppercase text-red-500/70">
                        {tWorkers("offline")}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {data.metadata.active_workers !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {tWorkers("activeWorkers")}
                </p>
                <p className="text-xs font-bold">{data.metadata.active_workers}</p>
              </div>
            )}
            {data.metadata.pending_scans !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {tScanner("pendingScans")}
                </p>
                <p
                  className={cn(
                    "text-xs font-bold",
                    data.metadata.pending_scans > 0 && "text-amber-500",
                  )}
                >
                  {data.metadata.pending_scans}
                </p>
              </div>
            )}
            {data.metadata.yara_enabled !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {tScanner("yaraEngine")}
                </p>
                <p className="text-xs font-bold text-green-500">
                  {tScanner("initialized")}
                </p>
              </div>
            )}
            {data.metadata.malwarebazaar_enabled !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {tScanner("bazaarApi")}
                </p>
                <p
                  className={cn(
                    "text-xs font-bold",
                    data.metadata.malwarebazaar_enabled
                      ? "text-green-500"
                      : "text-muted-foreground",
                  )}
                >
                  {data.metadata.malwarebazaar_enabled
                    ? t("enabled")
                    : t("disabled")}
                </p>
              </div>
            )}
            {data.metadata.max_storage_bytes !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {t("storageLimit")}
                </p>
                <p className="text-xs font-bold">
                  {(data.metadata.max_storage_bytes as number) /
                    (1024 * 1024 * 1024)}{" "}
                  GB
                </p>
              </div>
            )}
            {data.metadata.bucket && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {t("bucket")}
                </p>
                <p className="text-xs font-mono opacity-80 truncate">
                  {data.metadata.bucket}
                </p>
              </div>
            )}
            {data.metadata.ssl !== undefined && (
              <div className="space-y-0.5">
                <p className="text-[9px] font-bold uppercase text-muted-foreground/70">
                  {t("ssl")}
                </p>
                <p className="text-xs font-bold">
                  {data.metadata.ssl ? t("enabled") : t("disabled")}
                </p>
              </div>
            )}
          </div>
        )}

        {data?.status !== "healthy" && data?.message && (
          <div className="rounded-lg bg-destructive/5 p-2 border border-destructive/10">
            <p className="text-[10px] text-destructive font-bold leading-tight line-clamp-2">
              {data.message}
            </p>
          </div>
        )}

        {metadata && metadata.length > 0 && (
          <div className="grid grid-cols-2 gap-y-3 pt-2">
            {metadata.map((item, idx) => (
              <div key={idx} className="space-y-1">
                <span className="block text-[9px] font-bold uppercase tracking-wider text-muted-foreground">
                  {item.label}
                </span>
                <span
                  className="block text-[11px] font-black truncate pr-2"
                  title={String(item.value)}
                >
                  {item.value !== undefined && item.value !== null
                    ? String(item.value)
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        )}

        {children}

        {data?.status === "unhealthy" && data.message && (
          <div className="mt-2 rounded-md bg-red-500/10 p-2 text-[10px] font-medium text-red-500 border border-red-500/20">
            {data.message}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
