"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Archive,
  BookOpenCheck,
  CalendarRange,
  ExternalLink,
  FileBox,
  FileText,
  Folder,
  FolderTree,
  Loader2,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  CONTENT_STATUSES,
  ContentStatusBadge,
  type ContentStatus,
  normalizeContentStatus,
} from "@/components/content-status-badge";
import { AddFeaturedDialog } from "@/components/moderator/add-featured-dialog";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getFileExtension } from "@/lib/file-utils";
import { TYPE_ICONS, EXT_ICONS } from "@/lib/material-icons";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { FeaturedItem } from "@/components/home/types";

interface ContentRow {
  id: string;
  title: string;
  type: string;
  status: ContentStatus;
  updated_at: string;
  total_views: number;
  like_count: number;
  browse_path: string;
}

interface ContentResponse {
  items: ContentRow[];
  total: number;
  page: number;
  pages: number;
}

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

interface DirectoryItem {
  id: string;
  parent_id: string | null;
  name: string;
  slug: string;
  type: "module" | "folder";
  is_system: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. Lifecycle & Status Tab
// ─────────────────────────────────────────────────────────────────────────────

function ContentLifecycleTab({
  initialStatus,
}: {
  initialStatus: string | null;
}) {
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");

  const copy = fr
    ? {
        title: "Cycle de vie & Statuts",
        description:
          "Signalez clairement ce qui est essentiel, à jour, obsolète ou archivé.",
        search: "Rechercher un contenu…",
        all: "Tous les états",
        selected: "{count} sélectionné(s)",
        setStatus: "Définir l'état",
        clear: "Effacer",
        content: "Contenu",
        type: "Type",
        status: "État",
        updated: "Mis à jour",
        signals: "Signaux",
        views: "vues",
        likes: "j'aime",
        empty: "Aucun contenu ne correspond aux filtres.",
        previous: "Précédent",
        next: "Suivant",
        updatedToast: "{count} contenu(s) mis à jour",
        failed: "Impossible de mettre à jour l'état du contenu",
        loadFailed: "Impossible de charger le contenu",
      }
    : {
        title: "Lifecycle & Status",
        description:
          "Make it obvious what is essential, current, deprecated, or archived.",
        search: "Search content…",
        all: "All statuses",
        selected: "{count} selected",
        setStatus: "Set status",
        clear: "Clear",
        content: "Content",
        type: "Type",
        status: "Status",
        updated: "Updated",
        signals: "Signals",
        views: "views",
        likes: "likes",
        empty: "No content matches these filters.",
        previous: "Previous",
        next: "Next",
        updatedToast: "{count} item(s) updated",
        failed: "Could not update content status",
        loadFailed: "Could not load content",
      };

  const [rows, setRows] = useState<ContentRow[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(
    initialStatus && CONTENT_STATUSES.includes(initialStatus as ContentStatus)
      ? initialStatus
      : "all",
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const loadRequestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    try {
      const query = new URLSearchParams({ page: String(page), limit: "50" });
      if (search.trim()) query.set("search", search.trim());
      if (status !== "all") query.set("status", status);
      const data = await apiFetch<ContentResponse>(`/admin/content?${query}`);
      if (requestId !== loadRequestRef.current) return;
      setRows(data.items);
      setHasLoaded(true);
      setTotal(data.total);
      setPages(data.pages);
      setSelected(new Set());
    } catch {
      if (requestId !== loadRequestRef.current) return;
      toast.error(copy.loadFailed);
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  }, [copy.loadFailed, page, search, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  const changeSearch = (value: string) => {
    loadRequestRef.current += 1;
    setLoading(true);
    setPage(1);
    setSearch(value);
  };

  const changeStatus = (value: string) => {
    loadRequestRef.current += 1;
    setLoading(true);
    setPage(1);
    setStatus(value);
  };

  const changePage = (value: number) => {
    loadRequestRef.current += 1;
    setLoading(true);
    setPage(value);
  };

  const allSelected =
    rows.length > 0 && rows.every((row) => selected.has(row.id));

  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.id)));

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const updateStatus = async (ids: string[], nextStatus: ContentStatus) => {
    if (ids.length === 0) return;
    setActing(true);
    try {
      const result = await apiFetch<{ updated_count: number }>(
        "/admin/content/status",
        {
          method: "PATCH",
          body: JSON.stringify({ material_ids: ids, status: nextStatus }),
        },
      );
      setRows((current) =>
        current.map((row) =>
          ids.includes(row.id) ? { ...row, status: nextStatus } : row,
        ),
      );
      setSelected(new Set());
      toast.success(
        copy.updatedToast.replace("{count}", String(result.updated_count)),
      );
    } catch {
      toast.error(copy.failed);
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => changeSearch(event.target.value)}
            placeholder={copy.search}
            className="pl-9"
          />
        </div>
        <Select value={status} onValueChange={changeStatus}>
          <SelectTrigger className="w-full lg:w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.all}</SelectItem>
            {CONTENT_STATUSES.map((value) => (
              <SelectItem key={value} value={value}>
                <ContentStatusBadge status={value} />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Badge
          variant="secondary"
          className="justify-center whitespace-nowrap px-3 py-2"
        >
          {total}
        </Badge>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur">
          <Badge>{copy.selected.replace("{count}", String(selected.size))}</Badge>
          <Select
            disabled={acting}
            onValueChange={(value) =>
              void updateStatus(
                Array.from(selected),
                normalizeContentStatus(value),
              )
            }
          >
            <SelectTrigger className="h-9 w-44">
              <SelectValue placeholder={copy.setStatus} />
            </SelectTrigger>
            <SelectContent>
              {CONTENT_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  <ContentStatusBadge status={value} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => setSelected(new Set())}
          >
            {copy.clear}
          </Button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="w-12 p-3">
                  <Checkbox checked={allSelected} onCheckedChange={toggleAll} />
                </th>
                <th className="p-3 font-medium">{copy.content}</th>
                <th className="p-3 font-medium">{copy.type}</th>
                <th className="p-3 font-medium">{copy.status}</th>
                <th className="p-3 font-medium">{copy.updated}</th>
                <th className="p-3 font-medium">{copy.signals}</th>
              </tr>
            </thead>
            <tbody className="divide-y" aria-busy={loading}>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className={cn(
                    "transition-colors hover:bg-muted/30",
                    selected.has(row.id) && "bg-primary/5",
                    row.status === "important" &&
                      "bg-amber-50/30 dark:bg-amber-950/10",
                    row.status === "deprecated" &&
                      "bg-stone-50/30 dark:bg-stone-950/10",
                    row.status === "archived" && "opacity-65",
                  )}
                >
                  <td className="p-3">
                    <Checkbox
                      checked={selected.has(row.id)}
                      onCheckedChange={() => toggle(row.id)}
                    />
                  </td>
                  <td className="max-w-[360px] p-3">
                    <Link
                      href={row.browse_path}
                      className="group inline-flex max-w-full items-center gap-1.5 font-medium hover:text-primary"
                    >
                      <span className="truncate">{row.title}</span>
                      <ExternalLink className="h-3 w-3 shrink-0 opacity-0 group-hover:opacity-60" />
                    </Link>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {row.type}
                  </td>
                  <td className="p-3">
                    <Select
                      disabled={acting}
                      value={row.status}
                      onValueChange={(value) =>
                        void updateStatus([row.id], normalizeContentStatus(value))
                      }
                    >
                      <SelectTrigger className="h-8 w-40 border-0 bg-transparent px-1 shadow-none">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CONTENT_STATUSES.map((value) => (
                          <SelectItem key={value} value={value}>
                            <ContentStatusBadge status={value} />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(row.updated_at).toLocaleDateString(locale)}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {row.total_views} {copy.views} · {row.like_count}{" "}
                    {copy.likes}
                  </td>
                </tr>
              ))}
              {hasLoaded && rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-10 text-center text-muted-foreground">
                    {copy.empty}
                  </td>
                </tr>
              )}
              {loading && !hasLoaded && (
                <tr>
                  <td colSpan={6} className="p-10 text-center text-muted-foreground">
                    …
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1 || loading}
          onClick={() => changePage(Math.max(1, page - 1))}
        >
          {copy.previous}
        </Button>
        <span className="text-xs text-muted-foreground">
          {page} / {pages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages || loading}
          onClick={() => changePage(page + 1)}
        >
          {copy.next}
        </Button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Directories Tab
// ─────────────────────────────────────────────────────────────────────────────

function ContentDirectoriesTab() {
  const t = useTranslations("Moderator.directories");
  const [directories, setDirectories] = useState<DirectoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchDirectories = useCallback(() => {
    setLoading(true);
    apiFetch<DirectoryItem[]>("/moderator/directories")
      .then(setDirectories)
      .catch(() => toast.error(t("loadError")))
      .finally(() => setLoading(false));
  }, [t]);

  useEffect(() => {
    fetchDirectories();
  }, [fetchDirectories]);

  const buildTree = (
    items: DirectoryItem[],
    parentId: string | null = null,
  ): React.ReactNode[] => {
    const children = items.filter((i) => i.parent_id === parentId);
    if (children.length === 0) return [];

    return children.map((child) => (
      <div key={child.id} className="ml-6 mt-2 border-l pl-4 border-muted">
        <div className="flex items-center gap-2 group">
          {child.type === "module" ? (
            <FileBox className="h-4 w-4 text-primary" />
          ) : (
            <Folder className="h-4 w-4 text-blue-500" />
          )}
          <span className="text-sm font-medium">{child.name}</span>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            ({child.slug})
          </span>
          {child.is_system && (
            <span className="text-[10px] font-medium bg-muted px-1.5 py-0.5 rounded text-muted-foreground uppercase">
              {t("system")}
            </span>
          )}
        </div>
        {buildTree(items, child.id)}
      </div>
    ));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">{t("loading")}</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-muted/20 p-3.5 text-xs text-muted-foreground flex items-center gap-2.5">
        <FolderTree className="h-4 w-4 text-primary shrink-0" />
        <span>{t("readOnly")}</span>
      </div>

      <div className="rounded-xl border bg-card p-4 overflow-x-auto min-h-[300px]">
        {directories.length === 0 ? (
          <div className="text-center text-muted-foreground py-12">
            {t("empty")}
          </div>
        ) : (
          <div className="-ml-6">{buildTree(directories, null)}</div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Featured Items Tab
// ─────────────────────────────────────────────────────────────────────────────

function ContentFeaturedTab() {
  const t = useTranslations("Moderator.featured");
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const [items, setItems] = useState<FeaturedItem[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<FeaturedItem | null>(null);
  const { show } = useConfirmDialog();

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch<FeaturedItem[]>("/moderator/featured");
      const order: Record<FeaturedStatus, number> = {
        active: 0,
        scheduled: 1,
        expired: 2,
      };
      data.sort((a, b) => {
        const statusDiff =
          order[getFeaturedStatus(a)] - order[getFeaturedStatus(b)];
        if (statusDiff !== 0) return statusDiff;
        return b.priority - a.priority;
      });
      setItems(data);
      setHasLoaded(true);
    } catch {
      toast.error(t("errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const handleDelete = (item: FeaturedItem) => {
    const itemTitle =
      item.title ??
      (item.directory ? item.directory.name : item.material?.title || "Untitled");
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
            featured: (chunks) => (
              <strong className="font-medium text-foreground">{chunks}</strong>
            ),
          })}
        </p>
        <Button size="sm" className="shrink-0 gap-1.5" onClick={() => setDialogOpen(true)}>
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

      <div className="rounded-xl border bg-card">
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
            <tbody className="divide-y" aria-busy={loading}>
              {loading && !hasLoaded && (
                <tr>
                  <td colSpan={6} className="p-10 text-center text-muted-foreground">
                    {t("loading")}
                  </td>
                </tr>
              )}
              {hasLoaded && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-10 text-center">
                    <Star className="mx-auto mb-3 h-9 w-9 text-muted-foreground/20" />
                    <p className="text-sm font-medium text-muted-foreground">
                      {t("noItems")}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground/70">
                      {t("noItemsDesc")}
                    </p>
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const status = getFeaturedStatus(item);
                const isDir = !!item.directory;
                const displayName = item.directory
                  ? item.directory.name
                  : item.material?.title || "Untitled";
                const itemTypeLabel = isDir
                  ? (fr ? "Dossier" : "Folder")
                  : (fr ? "Fichier" : "File");
                const itemId = item.directory ? item.directory.id : item.material?.id;

                let browseUrl = "";
                if (item.directory) {
                  browseUrl = item.directory.full_path
                    ? `/browse/${item.directory.full_path}`
                    : `/browse`;
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
                          <Icon
                            className={cn(
                              "h-4 w-4 shrink-0",
                              isDir ? "text-primary" : "text-blue-500",
                            )}
                          />
                          <p className="font-medium leading-snug">{displayName}</p>
                          <Badge
                            variant="outline"
                            className="text-[10px] px-1.5 py-0 h-4 font-normal"
                          >
                            {itemTypeLabel}
                          </Badge>
                        </div>
                        {itemId && (
                          <div className="flex items-center gap-1.5 pl-[22px]">
                            <span
                              className="text-[11px] text-muted-foreground font-mono truncate max-w-xs"
                              title={pathDisplay}
                            >
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
                          {isDir
                            ? (fr ? "Utilise le nom du dossier" : "Uses folder name")
                            : t("dialog.usesMaterialTitle")}
                        </span>
                      )}
                    </td>
                    <td className="p-4">
                      <span
                        className={[
                          "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                          STATUS_STYLES[status],
                        ].join(" ")}
                      >
                        {status === "active" && (
                          <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
                        )}
                        {t(statusKey)}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-muted-foreground">
                      {formatDateRange(item.start_at, item.end_at)}
                    </td>
                    <td className="p-4 text-center">
                      <Badge
                        variant="secondary"
                        className="tabular-nums min-w-8 justify-center"
                      >
                        {item.priority}
                      </Badge>
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
            scheduled: items.filter((i) => getFeaturedStatus(i) === "scheduled")
              .length,
            expired: items.filter((i) => getFeaturedStatus(i) === "expired").length,
          })}
        </p>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Unified Staff Content Page
// ─────────────────────────────────────────────────────────────────────────────

export default function StaffContentPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");

  const tabParam = searchParams.get("tab");
  const initialStatus = searchParams.get("status");

  // If a ?status parameter is passed, default to lifecycle tab
  const defaultTab =
    initialStatus || !tabParam
      ? "lifecycle"
      : tabParam === "directories"
        ? "directories"
        : tabParam === "featured"
          ? "featured"
          : "lifecycle";

  const [currentTab, setCurrentTab] = useState(defaultTab);

  useEffect(() => {
    if (initialStatus) {
      setCurrentTab("lifecycle");
    } else if (tabParam === "directories") {
      setCurrentTab("directories");
    } else if (tabParam === "featured") {
      setCurrentTab("featured");
    } else if (tabParam === "lifecycle") {
      setCurrentTab("lifecycle");
    }
  }, [tabParam, initialStatus]);

  const handleTabChange = (value: string) => {
    setCurrentTab(value);
    const newParams = new URLSearchParams(searchParams.toString());
    if (value === "lifecycle") {
      newParams.delete("tab");
    } else {
      newParams.set("tab", value);
    }
    const query = newParams.toString();
    router.replace(query ? `/staff/content?${query}` : "/staff/content", {
      scroll: false,
    });
  };

  return (
    <div className="space-y-6 pb-12">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">
          {fr ? "Gestion du contenu" : "Content Management"}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {fr
            ? "Supervisez le cycle de vie des ressources, explorez les dossiers et mettez en avant les documents clés."
            : "Oversee material lifecycle statuses, explore directories, and spotlight essential resources."}
        </p>
      </div>

      <Tabs value={currentTab} onValueChange={handleTabChange} className="space-y-6">
        <TabsList className="grid w-full grid-cols-3 max-w-lg">
          <TabsTrigger value="lifecycle" className="gap-2">
            <BookOpenCheck className="h-4 w-4" />
            {fr ? "Cycle de vie" : "Lifecycle"}
          </TabsTrigger>
          <TabsTrigger value="directories" className="gap-2">
            <FolderTree className="h-4 w-4" />
            {fr ? "Dossiers" : "Directories"}
          </TabsTrigger>
          <TabsTrigger value="featured" className="gap-2">
            <Star className="h-4 w-4" />
            {fr ? "Mis en avant" : "Featured"}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="lifecycle" className="space-y-4">
          <ContentLifecycleTab initialStatus={initialStatus} />
        </TabsContent>

        <TabsContent value="directories" className="space-y-4">
          <ContentDirectoriesTab />
        </TabsContent>

        <TabsContent value="featured" className="space-y-4">
          <ContentFeaturedTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
