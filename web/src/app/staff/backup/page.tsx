"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  HardDrive,
  RotateCw,
  Trash2,
  Upload,
  Archive,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch, apiFetchBlob, apiRequest } from "@/lib/api-client";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { toast } from "sonner";
import { useLocale, useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { isAdminRole } from "@/lib/guest";
import { AdminOnlyNotice } from "@/components/staff/admin-only-notice";
import { QueryError } from "@/components/query-error";
import { formatFileSize, saveBlob } from "@/lib/file-utils";

interface BackupEntry {
  id: string;
  filename: string;
  created_at: string;
  size_bytes: number;
}

interface BackupManifest {
  version: string;
  created_at: string;
  s3_object_count: number;
  db_row_counts: Record<string, number>;
}

export default function StaffBackupPage() {
  const t = useTranslations("Admin.Backup");
  const { user } = useAuth();
  const isAdmin = isAdminRole(user?.role);
  const { show } = useConfirmDialog();

  const locale = useLocale();
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const backupsQuery = useQuery({
    queryKey: ["admin", "backups"],
    queryFn: ({ signal }) => apiFetch<BackupEntry[]>("/admin/backup", { signal }),
    select: (data) => [...data].reverse(), // newest first in UI
    enabled: isAdmin,
  });
  const backups = backupsQuery.data ?? [];
  const loading = backupsQuery.isFetching;
  const refreshBackups = () => queryClient.invalidateQueries({ queryKey: ["admin", "backups"] });
  const formatDate = (iso: string) =>
    new Date(iso).toLocaleString(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });

  if (!isAdmin) {
    return <AdminOnlyNotice />;
  }

  const handleSave = async () => {
    show(
      t("save.confirmTitle"),
      t("save.confirmDescription"),
      async () => {
        setSaving(true);
        try {
          const data = await apiFetch<{
            status: string;
            backup: BackupEntry;
            manifest: BackupManifest;
            rotated: string[];
          }>("/admin/backup/save", { method: "POST" });
          toast.success(t("save.success", { id: data.backup.id }));
          if (data.rotated.length > 0) {
            toast.info(t("save.rotated", { count: data.rotated.length }));
          }
          await refreshBackups();
        } catch {
          toast.error(t("errors.save"));
        } finally {
          setSaving(false);
        }
      },
    );
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const response = await apiRequest("/admin/backup/export", {
        headers: { Accept: "application/zip" },
      });
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] ?? "backup.zip";
      saveBlob(blob, filename);
      toast.success(t("export.success"));
    } catch {
      toast.error(t("errors.export"));
    } finally {
      setExporting(false);
    }
  };

  const handleDownload = async (backup: BackupEntry) => {
    try {
      const blob = await apiFetchBlob(`/admin/backup/${backup.id}/download`);
      saveBlob(blob, backup.filename);
    } catch {
      toast.error(t("errors.download"));
    }
  };

  const handleDelete = async (backup: BackupEntry) => {
    show(
      t("delete.confirmTitle"),
      t("delete.confirmDescription", { id: backup.id }),
      async () => {
        try {
          await apiFetch(`/admin/backup/${backup.id}`, { method: "DELETE" });
          toast.success(t("delete.success"));
          await refreshBackups();
        } catch {
          toast.error(t("errors.delete"));
        }
      },
    );
  };

  const handleRestoreLocal = async (backup: BackupEntry) => {
    show(
      t("restore.confirmTitle"),
      t("restore.confirmDescription"),
      async () => {
        setRestoring(backup.id);
        try {
          await apiFetch(`/admin/backup/${backup.id}/restore`, { method: "POST" });
          toast.success(t("restore.success"));
          void queryClient.invalidateQueries();
        } catch {
          toast.error(t("errors.restore"));
        } finally {
          setRestoring(null);
        }
      },
    );
  };

  const handleUploadRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Clear now so picking the same file again (e.g. after cancelling) fires onChange.
    e.target.value = "";
    if (!file) return;

    show(
      t("restore.confirmTitle"),
      t("restore.confirmDescription"),
      async () => {
        setRestoring("upload");
        try {
          const formData = new FormData();
          formData.append("file", file);
          await apiRequest("/admin/backup/restore/upload", {
            method: "POST",
            body: formData,
          });
          toast.success(t("restore.success"));
          void queryClient.invalidateQueries();
        } catch (err: unknown) {
          const message =
            err instanceof Error ? err.message : t("errors.restore");
          toast.error(message);
        } finally {
          setRestoring(null);
        }
      },
    );
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold mb-1">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>

      {/* Actions */}
      <div className="rounded-lg border p-6 space-y-4 bg-card/40">
        <h3 className="font-medium">{t("actions.title")}</h3>
        <div className="flex flex-wrap gap-3">
          <Button onClick={handleSave} disabled={saving} className="gap-2">
            <HardDrive className="h-4 w-4" />
            {saving ? t("actions.saving") : t("actions.saveToServer")}
          </Button>
          <Button
            variant="outline"
            onClick={handleExport}
            disabled={exporting}
            className="gap-2"
          >
            <Download className="h-4 w-4" />
            {exporting ? t("actions.exporting") : t("actions.exportToComputer")}
          </Button>
          <Button
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
            disabled={restoring !== null}
            className="gap-2"
          >
            <Upload className="h-4 w-4" />
            {restoring === "upload"
              ? t("actions.restoring")
              : t("actions.restoreFromFile")}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={handleUploadRestore}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {t("actions.rotationNote", { max: 3 })}
        </p>
      </div>

      {/* Server-local backups */}
      <div className="space-y-3">
        <h3 className="font-medium">{t("list.title")}</h3>

        {backupsQuery.isPending ? (
          <p className="text-sm text-muted-foreground">{t("list.loading")}</p>
        ) : backupsQuery.isError ? (
          <QueryError message={t("errors.load")} onRetry={() => void backupsQuery.refetch()} />
        ) : backups.length === 0 ? (
          <div
            className={cn(
              "rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground",
              loading && "opacity-60 transition-opacity",
            )}
            aria-busy={loading}
          >
            <Archive className="mx-auto mb-3 h-8 w-8 opacity-40" />
            {t("list.empty")}
          </div>
        ) : (
          <div
            className={cn(
              "divide-y rounded-lg border bg-card/40",
              loading && "opacity-60 transition-opacity",
            )}
            aria-busy={loading}
          >
            {backups.map((backup) => (
              <div
                key={backup.id}
                className="flex items-center justify-between gap-4 p-4"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-mono font-medium">
                    {backup.filename}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatDate(backup.created_at)} · {formatFileSize(backup.size_bytes)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={() => handleDownload(backup)}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {t("list.download")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    disabled={restoring !== null}
                    onClick={() => handleRestoreLocal(backup)}
                  >
                    <RotateCw className="h-3.5 w-3.5" />
                    {restoring === backup.id
                      ? t("actions.restoring")
                      : t("list.restore")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="gap-1 text-destructive hover:text-destructive"
                    onClick={() => handleDelete(backup)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("list.delete")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20">
        <p className="text-sm text-amber-800 dark:text-amber-300">
          <strong>{t("warning.title")}</strong> {t("warning.body")}
        </p>
      </div>
    </div>
  );
}
