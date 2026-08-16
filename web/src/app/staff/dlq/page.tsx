"use client";

import { useEffect, useState } from "react";
import { RotateCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api-client";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

interface FailedJob {
  id: string;
  job_name: string;
  upload_id: string | null;
  payload: Record<string, unknown> | null;
  error_detail: string | null;
  attempts: number;
  created_at: string;
  resolved_at: string | null;
}

interface PaginatedJobs {
  items: FailedJob[];
  total: number;
  page: number;
  pages: number;
}

export default function StaffDLQPage() {
  const t = useTranslations("Admin.DLQ");
  const { user } = useAuth();
  const isAdmin = user?.role === "bureau" || user?.role === "vieux";
  const { show } = useConfirmDialog();

  const [jobs, setJobs] = useState<FailedJob[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [showResolved, setShowResolved] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchJobs = async (p = page) => {
    if (!isAdmin) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), limit: "50" });
      if (showResolved) params.append("resolved", "true");

      const data = await apiFetch<PaginatedJobs>(`/admin/dlq?${params}`);
      setJobs(data.items);
      setPage(data.page);
      setPages(data.pages);
      setTotal(data.total);
    } catch {
      toast.error(t("errors.load"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs(1);
  }, [showResolved]);

  if (!isAdmin) {
    return (
      <div className="flex items-center justify-center p-12 text-muted-foreground">
        Access restricted to administrators.
      </div>
    );
  }

  const handleRetry = async (job: FailedJob) => {
    show(
      t("actions.retry.title"),
      t("actions.retry.description", { name: job.job_name }),
      async () => {
        try {
          await apiFetch(`/admin/dlq/${job.id}/retry`, { method: "POST" });
          setJobs((prev) => prev.filter((j) => j.id !== job.id));
          toast.success(t("actions.retry.success"));
        } catch {
          toast.error(t("actions.retry.error"));
        }
      },
    );
  };

  const handleDismiss = async (job: FailedJob) => {
    show(
      t("actions.dismiss.title"),
      t("actions.dismiss.description", { name: job.job_name }),
      async () => {
        try {
          await apiFetch(`/admin/dlq/${job.id}/dismiss`, { method: "POST" });
          setJobs((prev) =>
            prev.map((j) =>
              j.id === job.id
                ? { ...j, resolved_at: new Date().toISOString() }
                : j,
            ),
          );
          toast.success(t("actions.dismiss.success"));
        } catch {
          toast.error(t("actions.dismiss.error"));
        }
      },
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("jobCount", { count: total })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="show-resolved" className="text-sm cursor-pointer">
            {t("showResolved")}
          </Label>
          <Switch
            id="show-resolved"
            checked={showResolved}
            onCheckedChange={setShowResolved}
          />
        </div>
      </div>

      <div className="rounded-lg border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="p-4 font-medium">{t("table.job")}</th>
                <th className="p-4 font-medium">{t("table.attempts")}</th>
                <th className="p-4 font-medium">{t("table.error")}</th>
                <th className="p-4 font-medium">{t("table.created")}</th>
                <th className="p-4 font-medium text-right">{t("table.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {loading && jobs.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-muted-foreground">
                    {t("state.loading")}
                  </td>
                </tr>
              )}
              {!loading && jobs.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-muted-foreground">
                    {showResolved ? t("state.noFound") : t("state.empty")}
                  </td>
                </tr>
              )}
              {jobs.map((job) => (
                <tr key={job.id} className="transition-colors hover:bg-muted/30">
                  <td className="p-4">
                    <div className="flex flex-col">
                      <span className="font-medium font-mono text-xs">
                        {job.job_name}
                      </span>
                      <span className="text-[10px] font-mono opacity-50">
                        {job.id}
                      </span>
                      {job.resolved_at && (
                        <Badge
                          variant="outline"
                          className="mt-1 w-fit text-[9px] h-4 px-1 bg-green-500/5 text-green-600 border-green-200"
                        >
                          {t("state.resolved")}
                        </Badge>
                      )}
                    </div>
                  </td>
                  <td className="p-4 hidden sm:table-cell">
                    <Badge variant={job.attempts >= 3 ? "destructive" : "outline"}>
                      {job.attempts}
                    </Badge>
                  </td>
                  <td className="p-4 hidden md:table-cell max-w-xs">
                    {job.error_detail ? (
                      <p
                        className="truncate text-xs text-destructive font-mono"
                        title={job.error_detail}
                      >
                        {job.error_detail}
                      </p>
                    ) : (
                      <span className="text-muted-foreground text-xs">—</span>
                    )}
                  </td>
                  <td className="p-4 text-muted-foreground text-xs hidden sm:table-cell">
                    {job.created_at
                      ? new Date(job.created_at).toLocaleString()
                      : "—"}
                  </td>
                  <td className="p-4 text-right">
                    {!job.resolved_at && (
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 text-xs gap-1.5"
                          onClick={() => handleRetry(job)}
                        >
                          <RotateCw className="h-3 w-3" />
                          {t("actions.retry.label")}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 text-xs gap-1.5 text-muted-foreground"
                          onClick={() => handleDismiss(job)}
                        >
                          <XCircle className="h-3 w-3" />
                          {t("actions.dismiss.label")}
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={page <= 1}
            onClick={() => fetchJobs(page - 1)}
          >
            {t("pagination.previous")}
          </Button>
          <span className="text-sm text-muted-foreground">
            {page} {t("pagination.of")} {pages}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={page >= pages}
            onClick={() => fetchJobs(page + 1)}
          >
            {t("pagination.next")}
          </Button>
        </div>
      )}
    </div>
  );
}
