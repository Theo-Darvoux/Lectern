"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ExternalLink, Search } from "lucide-react";
import { useLocale } from "next-intl";
import { toast } from "sonner";

import {
  CONTENT_STATUSES,
  ContentStatusBadge,
  type ContentStatus,
  normalizeContentStatus,
} from "@/components/content-status-badge";
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
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

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

export default function AdminContentPage() {
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const params = useSearchParams();
  const initialStatus = params.get("status");

  const copy = fr
    ? {
        title: "État du contenu",
        description: "Signalez clairement ce qui est essentiel, à jour, obsolète ou archivé.",
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
        title: "Content status",
        description: "Make it obvious what is essential, current, deprecated, or archived.",
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
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(
    initialStatus && CONTENT_STATUSES.includes(initialStatus as ContentStatus) ? initialStatus : "all",
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ page: String(page), limit: "50" });
      if (search.trim()) query.set("search", search.trim());
      if (status !== "all") query.set("status", status);
      const data = await apiFetch<ContentResponse>(`/admin/content?${query}`);
      setRows(data.items);
      setTotal(data.total);
      setPages(data.pages);
      setSelected(new Set());
    } catch {
      toast.error(copy.loadFailed);
    } finally {
      setLoading(false);
    }
  }, [copy.loadFailed, page, search, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => setPage(1), [search, status]);

  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id));
  const selectedRows = useMemo(() => rows.filter((row) => selected.has(row.id)), [rows, selected]);

  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.id)));
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
      const result = await apiFetch<{ updated_count: number }>("/admin/content/status", {
        method: "PATCH",
        body: JSON.stringify({ material_ids: ids, status: nextStatus }),
      });
      setRows((current) => current.map((row) => (ids.includes(row.id) ? { ...row, status: nextStatus } : row)));
      setSelected(new Set());
      toast.success(copy.updatedToast.replace("{count}", String(result.updated_count)));
    } catch {
      toast.error(copy.failed);
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">{copy.title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{copy.description}</p>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={copy.search} className="pl-9" />
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-full lg:w-52"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.all}</SelectItem>
            {CONTENT_STATUSES.map((value) => (
              <SelectItem key={value} value={value}><ContentStatusBadge status={value} /></SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Badge variant="secondary" className="justify-center whitespace-nowrap px-3 py-2">{total}</Badge>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur">
          <Badge>{copy.selected.replace("{count}", String(selected.size))}</Badge>
          <Select disabled={acting} onValueChange={(value) => void updateStatus(Array.from(selected), normalizeContentStatus(value))}>
            <SelectTrigger className="h-9 w-44"><SelectValue placeholder={copy.setStatus} /></SelectTrigger>
            <SelectContent>
              {CONTENT_STATUSES.map((value) => (
                <SelectItem key={value} value={value}><ContentStatusBadge status={value} /></SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSelected(new Set())}>{copy.clear}</Button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="w-12 p-3"><Checkbox checked={allSelected} onCheckedChange={toggleAll} /></th>
                <th className="p-3 font-medium">{copy.content}</th>
                <th className="p-3 font-medium">{copy.type}</th>
                <th className="p-3 font-medium">{copy.status}</th>
                <th className="p-3 font-medium">{copy.updated}</th>
                <th className="p-3 font-medium">{copy.signals}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className={cn(
                    "transition-colors hover:bg-muted/30",
                    selected.has(row.id) && "bg-primary/5",
                    row.status === "important" && "bg-red-50/40 dark:bg-red-950/10",
                    row.status === "deprecated" && "bg-amber-50/30 dark:bg-amber-950/10",
                    row.status === "archived" && "opacity-65",
                  )}
                >
                  <td className="p-3"><Checkbox checked={selected.has(row.id)} onCheckedChange={() => toggle(row.id)} /></td>
                  <td className="max-w-[360px] p-3">
                    <Link href={row.browse_path} className="group inline-flex max-w-full items-center gap-1.5 font-medium hover:text-primary">
                      <span className="truncate">{row.title}</span>
                      <ExternalLink className="h-3 w-3 shrink-0 opacity-0 group-hover:opacity-60" />
                    </Link>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">{row.type}</td>
                  <td className="p-3">
                    <Select disabled={acting} value={row.status} onValueChange={(value) => void updateStatus([row.id], normalizeContentStatus(value))}>
                      <SelectTrigger className="h-8 w-40 border-0 bg-transparent px-1 shadow-none"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {CONTENT_STATUSES.map((value) => (
                          <SelectItem key={value} value={value}><ContentStatusBadge status={value} /></SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">{new Date(row.updated_at).toLocaleDateString(locale)}</td>
                  <td className="p-3 text-xs text-muted-foreground">{row.total_views} {copy.views} · {row.like_count} {copy.likes}</td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={6} className="p-10 text-center text-muted-foreground">{copy.empty}</td></tr>
              )}
              {loading && rows.length === 0 && (
                <tr><td colSpan={6} className="p-10 text-center text-muted-foreground">…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>{copy.previous}</Button>
        <span className="text-xs text-muted-foreground">{page} / {pages}</span>
        <Button variant="outline" size="sm" disabled={page >= pages || loading} onClick={() => setPage((value) => value + 1)}>{copy.next}</Button>
      </div>
    </div>
  );
}
