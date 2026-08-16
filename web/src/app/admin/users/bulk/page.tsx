"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  Search,
  Shield,
  Trash2,
  UserMinus,
  XCircle,
} from "lucide-react";
import { useLocale } from "next-intl";
import { toast } from "sonner";

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
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  role: string | null;
  onboarded: boolean;
  created_at: string;
}

interface PaginatedUsers {
  items: AdminUser[];
  total: number;
  next_cursor: string | null;
  has_more: boolean;
}

interface BulkResult {
  action: string;
  updated: string[];
  updated_count: number;
  skipped: Array<{ id: string; reason: string }>;
  skipped_count: number;
}

const ROLE_BADGE: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  student: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  moderator: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
  bureau: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  vieux: "bg-gray-100 text-gray-800 dark:bg-gray-800/50 dark:text-gray-300",
  guest: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
};

export default function BulkUsersPage() {
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const params = useSearchParams();
  const { user: actor } = useAuth();
  const { show } = useConfirmDialog();

  const copy = fr
    ? {
        title: "Gestion des utilisateurs en masse",
        description: "Sélectionnez plusieurs comptes puis appliquez une action cohérente en une fois.",
        back: "Gestion classique",
        search: "Rechercher par nom ou e-mail…",
        allRoles: "Tous les rôles",
        selected: "{count} sélectionné(s)",
        selectPage: "Sélectionner la page",
        clear: "Effacer",
        approve: "Approuver",
        reject: "Rejeter",
        setRole: "Définir le rôle",
        delete: "Supprimer",
        export: "Exporter CSV",
        confirmReject: "Rejeter les comptes sélectionnés ? Les comptes en attente seront supprimés.",
        confirmDelete: "Supprimer les comptes sélectionnés ? Cette action est définitive.",
        confirmTitle: "Confirmer l'action en masse",
        updated: "{count} compte(s) mis à jour",
        skipped: "{count} compte(s) ignoré(s)",
        loadError: "Impossible de charger les utilisateurs",
        actionError: "L'action en masse a échoué",
        email: "E-mail",
        name: "Nom",
        role: "Rôle",
        joined: "Créé",
        empty: "Aucun utilisateur ne correspond aux filtres.",
        previous: "Précédent",
        next: "Suivant",
      }
    : {
        title: "Bulk user administration",
        description: "Select accounts and apply one consistent administrative action at a time.",
        back: "Standard user management",
        search: "Search by name or email…",
        allRoles: "All roles",
        selected: "{count} selected",
        selectPage: "Select page",
        clear: "Clear",
        approve: "Approve",
        reject: "Reject",
        setRole: "Set role",
        delete: "Delete",
        export: "Export CSV",
        confirmReject: "Reject selected accounts? Pending accounts will be deleted.",
        confirmDelete: "Delete selected accounts? This action is permanent.",
        confirmTitle: "Confirm bulk action",
        updated: "{count} account(s) updated",
        skipped: "{count} account(s) skipped",
        loadError: "Could not load users",
        actionError: "Bulk action failed",
        email: "Email",
        name: "Name",
        role: "Role",
        joined: "Created",
        empty: "No users match these filters.",
        previous: "Previous",
        next: "Next",
      };

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState(params.get("role") || "all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);

  const fetchUsers = useCallback(async (cursor: string | null = null) => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (cursor) query.set("cursor", cursor);
      if (search.trim()) query.set("search", search.trim());
      if (role !== "all") query.set("role", role);
      const result = await apiFetch<PaginatedUsers>(`/admin/users?${query}`);
      setUsers(result.items);
      setTotal(result.total);
      setNextCursor(result.next_cursor);
      setSelected(new Set());
    } catch {
      toast.error(copy.loadError);
    } finally {
      setLoading(false);
    }
  }, [copy.loadError, role, search]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setCursorStack([]);
      void fetchUsers(null);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [fetchUsers]);

  const selectable = useMemo(
    () => users.filter((candidate) => candidate.id !== actor?.id),
    [actor?.id, users],
  );
  const selectedUsers = useMemo(
    () => users.filter((candidate) => selected.has(candidate.id)),
    [selected, users],
  );
  const allPageSelected = selectable.length > 0 && selectable.every((candidate) => selected.has(candidate.id));
  const pendingSelected = selectedUsers.filter((candidate) => candidate.role === "pending").length;

  const toggleAll = () => {
    if (allPageSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(selectable.map((candidate) => candidate.id)));
    }
  };

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runAction = async (
    action: "approve" | "reject" | "set_role" | "delete",
    roleValue?: string,
  ) => {
    if (selected.size === 0) return;
    setActing(true);
    try {
      const result = await apiFetch<BulkResult>("/admin/users/bulk", {
        method: "POST",
        body: JSON.stringify({
          user_ids: Array.from(selected),
          action,
          ...(roleValue ? { role: roleValue } : {}),
        }),
      });
      toast.success(copy.updated.replace("{count}", String(result.updated_count)));
      if (result.skipped_count > 0) {
        toast.warning(copy.skipped.replace("{count}", String(result.skipped_count)), {
          description: result.skipped.slice(0, 3).map((item) => item.reason).join(" · "),
        });
      }
      await fetchUsers(cursorStack.length ? cursorStack[cursorStack.length - 1] : null);
    } catch {
      toast.error(copy.actionError);
    } finally {
      setActing(false);
    }
  };

  const confirmAction = (action: "reject" | "delete") => {
    const description = action === "reject" ? copy.confirmReject : copy.confirmDelete;
    show(copy.confirmTitle, description, async () => {
      await runAction(action);
    });
  };

  const exportCsv = () => {
    const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
    const rows = [
      ["email", "display_name", "role", "created_at"],
      ...selectedUsers.map((candidate) => [
        candidate.email,
        candidate.display_name ?? "",
        candidate.role ?? "",
        candidate.created_at,
      ]),
    ];
    const csv = rows.map((row) => row.map((value) => quote(String(value))).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "users-selection.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const next = () => {
    if (!nextCursor) return;
    setCursorStack((current) => [...current, nextCursor]);
    void fetchUsers(nextCursor);
  };

  const previous = () => {
    setCursorStack((current) => {
      const nextStack = [...current];
      nextStack.pop();
      const cursor = nextStack.length ? nextStack[nextStack.length - 1] : null;
      void fetchUsers(cursor);
      return nextStack;
    });
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">{copy.title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{copy.description}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="gap-2 self-start">
          <Link href="/admin/users">
            <ArrowLeft className="h-4 w-4" />
            {copy.back}
          </Link>
        </Button>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={copy.search} className="pl-9" />
        </div>
        <Select value={role} onValueChange={setRole}>
          <SelectTrigger className="w-full lg:w-48"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.allRoles}</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="student">Student</SelectItem>
            <SelectItem value="moderator">Moderator</SelectItem>
            <SelectItem value="bureau">Bureau</SelectItem>
            <SelectItem value="vieux">Vieux</SelectItem>
            <SelectItem value="guest">Guest</SelectItem>
          </SelectContent>
        </Select>
        <Badge variant="secondary" className="justify-center whitespace-nowrap px-3 py-2">{total}</Badge>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur">
          <Badge className="mr-1">{copy.selected.replace("{count}", String(selected.size))}</Badge>
          <Button size="sm" variant="outline" className="gap-1.5" disabled={acting || pendingSelected === 0} onClick={() => void runAction("approve")}>
            <CheckCircle2 className="h-4 w-4" /> {copy.approve}
          </Button>
          <Button size="sm" variant="outline" className="gap-1.5 text-amber-700" disabled={acting || pendingSelected === 0} onClick={() => confirmAction("reject")}>
            <XCircle className="h-4 w-4" /> {copy.reject}
          </Button>
          <Select disabled={acting} onValueChange={(value) => void runAction("set_role", value)}>
            <SelectTrigger className="h-9 w-40"><Shield className="mr-2 h-4 w-4" /><SelectValue placeholder={copy.setRole} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="student">Student</SelectItem>
              <SelectItem value="moderator">Moderator</SelectItem>
              <SelectItem value="bureau">Bureau</SelectItem>
              <SelectItem value="vieux">Vieux</SelectItem>
              <SelectItem value="guest">Guest</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" className="gap-1.5" disabled={acting} onClick={exportCsv}>
            <Download className="h-4 w-4" /> {copy.export}
          </Button>
          <Button size="sm" variant="destructive" className="gap-1.5" disabled={acting} onClick={() => confirmAction("delete")}>
            <Trash2 className="h-4 w-4" /> {copy.delete}
          </Button>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())}>{copy.clear}</Button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="w-12 p-3">
                  <Checkbox checked={allPageSelected} onCheckedChange={toggleAll} aria-label={copy.selectPage} />
                </th>
                <th className="p-3 font-medium">{copy.email}</th>
                <th className="p-3 font-medium">{copy.name}</th>
                <th className="p-3 font-medium">{copy.role}</th>
                <th className="p-3 font-medium">{copy.joined}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {users.map((candidate) => {
                const isSelf = candidate.id === actor?.id;
                return (
                  <tr key={candidate.id} className={cn("hover:bg-muted/30", selected.has(candidate.id) && "bg-primary/5", candidate.role === "pending" && "bg-amber-50/40 dark:bg-amber-950/10")}>
                    <td className="p-3">
                      <Checkbox checked={selected.has(candidate.id)} disabled={isSelf} onCheckedChange={() => toggle(candidate.id)} />
                    </td>
                    <td className="p-3 font-medium">{candidate.email}</td>
                    <td className="p-3 text-muted-foreground">{candidate.display_name || "—"}</td>
                    <td className="p-3">
                      <Badge className={cn("border-0 text-xs capitalize", ROLE_BADGE[candidate.role || "student"])}>{candidate.role || "student"}</Badge>
                    </td>
                    <td className="p-3 text-xs text-muted-foreground">{new Date(candidate.created_at).toLocaleDateString(locale)}</td>
                  </tr>
                );
              })}
              {!loading && users.length === 0 && (
                <tr><td colSpan={5} className="p-10 text-center text-muted-foreground">{copy.empty}</td></tr>
              )}
              {loading && users.length === 0 && (
                <tr><td colSpan={5} className="p-10 text-center text-muted-foreground">…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <Button variant="outline" size="sm" disabled={cursorStack.length === 0 || loading} onClick={previous}>{copy.previous}</Button>
        <Button variant="outline" size="sm" disabled={!nextCursor || loading} onClick={next}>{copy.next}</Button>
      </div>
    </div>
  );
}
