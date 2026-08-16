"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Star, CalendarRange, ExternalLink, Loader2, Pencil, Folder, FileText } from "lucide-react";
import { TYPE_ICONS, EXT_ICONS } from "@/lib/material-icons";
import { getFileExtension } from "@/lib/file-utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useTranslations } from "next-intl";
import { apiFetch } from "@/lib/api-client";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { toast } from "sonner";
import Link from "next/link";
import type { FeaturedItem } from "@/components/home/types";
import { AddFeaturedDialog } from "@/components/moderator/add-featured-dialog";

type FeaturedStatus = "active" | "scheduled" | "expired";

function getFeaturedStatus(item: FeaturedItem): FeaturedStatus {
  const now = new Date();
  const start = new Date(item.start_at);
  const end = new Date(item.end_at);
  if (now < start) return "scheduled";
  if (now > end) return "expired";
  return "active";
}

const STATUS_STYLES: Record<FeaturedStatus, string> = {
  active:
    "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  scheduled: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  expired: "bg-gray-100 text-gray-600 dark:bg-gray-800/40 dark:text-gray-400",
};

function formatDateRange(startAt: string, endAt: string): string {
  const fmt = (d: string) =>
    new Date(d).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  return `${fmt(startAt)} → ${fmt(endAt)}`;
}

export default function ModeratorFeaturedPage() {
  const t = useTranslations("Moderator.featured");
  const [items, setItems] = useState<FeaturedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<FeaturedItem | null>(null);
  const { show } = useConfirmDialog();

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch<FeaturedItem[]>("/moderator/featured");
      const order: Record<FeaturedStatus, number> = { active: 0, scheduled: 1, expired: 2 };
      data.sort((a, b) => {
        const statusDiff = order[getFeaturedStatus(a)] - order[getFeaturedStatus(b)];
        if (statusDiff !== 0) return statusDiff;
        return b.priority - a.priority;
      });
      setItems(data);
    } catch {
      toast.error(t("errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { fetchItems(); }, [fetchItems]);

  const handleDelete = (item: FeaturedItem) => {
    const itemTitle = item.title ?? (item.directory ? item.directory.name : (item.material?.title || "Untitled"));
    show(
      t("delete.confirmTitle"),
      t("delete.confirmDesc", { title: itemTitle }),
      async () => {
        try {
          await apiFetch(`/moderator/featured/${item.id}`, { method: "DELETE" });
          setItems((prev) => prev.filter((i) => i.id !== item.id));
          toast.success(t("delete.success"));
        } catch {
          toast.error(t("delete.error"));
        }
      },
    );
  };

  const handleEdit = (item: FeaturedItem) => {
    setEditItem(item);
    setDialogOpen(true);
  };

  const handleDialogOpenChange = (open: boolean) => {
    setDialogOpen(open);
    if (!open) {
      setEditItem(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          {t.rich("description", {
            featured: (chunks) => <strong className="font-medium text-foreground">{chunks}</strong>
          })}
        </p>
        <Button size="sm" className="shrink-0" onClick={() => setDialogOpen(true)}>
          <Plus className="h-4 w-4" />
          {t("addFeatured")}
        </Button>
      </div>

      <AddFeaturedDialog
        open={dialogOpen}
        onOpenChange={handleDialogOpenChange}
        onSuccess={fetchItems}
        editItem={editItem}
      />

      <div className="rounded-lg border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="p-4 font-medium">{t("table.material")}</th>
                <th className="p-4 font-medium">{t("table.titleOverride")}</th>
                <th className="p-4 font-medium">{t("table.status")}</th>
                <th className="p-4 font-medium">
                  <span className="flex items-center gap-1.5">
                    <CalendarRange className="h-3.5 w-3.5" />
                    {t("table.period")}
                  </span>
                </th>
                <th className="p-4 font-medium text-center">{t("table.priority")}</th>
                <th className="p-4 font-medium text-right">{t("table.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {loading && items.length === 0 && (
                <tr><td colSpan={6} className="p-10 text-center text-muted-foreground">{t("loading")}</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-10 text-center">
                    <Star className="mx-auto mb-3 h-9 w-9 text-muted-foreground/20" />
                    <p className="text-sm font-medium text-muted-foreground">{t("noItems")}</p>
                    <p className="mt-1 text-xs text-muted-foreground/70">{t("noItemsDesc")}</p>
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const status = getFeaturedStatus(item);
                const isDir = !!item.directory;
                const displayName = item.directory ? item.directory.name : (item.material?.title || "Untitled");
                const itemTypeLabel = isDir ? "Folder" : "File";
                const itemId = item.directory ? item.directory.id : item.material?.id;

                let browseUrl = "";
                if (item.directory) {
                  browseUrl = item.directory.full_path ? `/browse/${item.directory.full_path}` : `/browse`;
                } else if (item.material) {
                  browseUrl = item.material.directory_path
                    ? `/browse/${item.material.directory_path}/${item.material.slug}`
                    : `/browse/${item.material.slug}`;
                }

                const pathDisplay = browseUrl.replace(/^\/browse/, "") || "/";

                let Icon: React.ElementType = isDir ? Folder : FileText;
                if (!isDir && item.material) {
                  const extension = getFileExtension(item.material.title);
                  if (item.material.type && TYPE_ICONS[item.material.type]) {
                    Icon = TYPE_ICONS[item.material.type];
                  } else if (extension && EXT_ICONS[extension]) {
                    Icon = EXT_ICONS[extension];
                  }
                }

                const statusKey = `status.${status}` as const;
                return (
                  <tr key={item.id} className="transition-colors hover:bg-muted/30">
                    <td className="p-4">
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-1.5">
                          <Icon className={cn("h-4 w-4 shrink-0", isDir ? "text-primary" : "text-blue-500")} />
                          <p className="font-medium leading-snug">{displayName}</p>
                          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 font-normal">
                            {itemTypeLabel}
                          </Badge>
                        </div>
                        {itemId && (
                          <div className="flex items-center gap-1.5 pl-[22px]">
                            <span className="text-[11px] text-muted-foreground font-mono truncate max-w-xs" title={pathDisplay}>
                              {pathDisplay}
                            </span>
                            {browseUrl && (
                              <Link
                                href={browseUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 text-[11px] text-primary hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <ExternalLink className="h-2.5 w-2.5" />
                                {t("view")}
                              </Link>
                            )}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="p-4">
                      {item.title ? (
                        <span className="line-clamp-1 max-w-45">{item.title}</span>
                      ) : (
                        <span className="italic text-muted-foreground/50 text-xs">
                          {isDir ? "Uses folder name" : t("dialog.usesMaterialTitle")}
                        </span>
                      )}
                    </td>
                    <td className="p-4">
                      <span className={["inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", STATUS_STYLES[status]].join(" ")}>
                        {status === "active" && <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />}
                        {t(statusKey)}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-muted-foreground">{formatDateRange(item.start_at, item.end_at)}</td>
                    <td className="p-4 text-center">
                      <Badge variant="secondary" className="tabular-nums min-w-8 justify-center">{item.priority}</Badge>
                    </td>
                    <td className="p-4 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleEdit(item)}
                          className="text-muted-foreground hover:bg-muted"
                          title="Edit boost"
                          aria-label="Edit boost"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDelete(item)}
                          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          title={t("delete.confirmTitle")}
                          aria-label={t("delete.confirmTitle")}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {!loading && items.length > 0 && (
        <p className="text-xs text-muted-foreground text-right">
          {t("stats", {
            active: items.filter((i) => getFeaturedStatus(i) === "active").length,
            scheduled: items.filter((i) => getFeaturedStatus(i) === "scheduled").length,
            expired: items.filter((i) => getFeaturedStatus(i) === "expired").length,
          })}
        </p>
      )}
    </div>
  );
}
