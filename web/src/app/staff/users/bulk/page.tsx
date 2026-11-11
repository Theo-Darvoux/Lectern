"use client";

import { useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  Search,
  Shield,
  Trash2,
  XCircle,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
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
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { QueryError } from "@/components/query-error";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { isAdminRole } from "@/lib/guest";
import { AdminOnlyNotice } from "@/components/staff/admin-only-notice";
import { saveBlob, toCsv } from "@/lib/file-utils";

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

export default function StaffBulkUsersPage() {
  const locale = useLocale();
  const params = useSearchParams();
  const { user: actor } = useAuth();
  const isAdmin = isAdminRole(actor?.role);
  const { show } = useConfirmDialog();

  const t = useTranslations("Admin.BulkUsers");
  const tRoles = useTranslations("Admin.Users.roleLabels");

  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [role, setRole] = useState(params.get("role") || "all");
  // Cursors of the pages before the current one; the last entry is the current page.
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [acting, setActing] = useState(false);
  const debouncedSearch = useDebouncedValue(search.trim(), 250);
  const cursor = cursorStack.at(-1) ?? null;

  const usersQuery = useQuery({
    queryKey: ["admin", "users", { search: debouncedSearch, roleFilter: role, cursor, limit: 100 }],
    queryFn: ({ signal }) => {
      const query = new URLSearchParams({ limit: "100" });
      if (cursor) query.set("cursor", cursor);
      if (debouncedSearch) query.set("search", debouncedSearch);
      if (role !== "all") query.set("role", role);
      return apiFetch<PaginatedUsers>(`/admin/users?${query}`, { signal });
    },
    enabled: isAdmin,
    placeholderData: keepPreviousData,
  });
  const users = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;
  const nextCursor = usersQuery.data?.next_cursor ?? null;
  const loading = usersQuery.isFetching;

  // The selection only covers the rows on screen.
  const goToPage = (stack: string[]) => {
    setCursorStack(stack);
    setSelected(new Set());
  };

  const changeSearch = (value: string) => {
    setSearch(value);
    goToPage([]);
  };

  const changeRole = (value: string) => {
    setRole(value);
    goToPage([]);
  };

  if (!isAdmin) {
    return <AdminOnlyNotice />;
  }

  const selectable = users.filter((candidate) => candidate.id !== actor?.id);
  const selectedUsers = users.filter((candidate) => selected.has(candidate.id));
  const allPageSelected =
    selectable.length > 0 &&
    selectable.every((candidate) => selected.has(candidate.id));
  const pendingSelected = selectedUsers.filter(
    (candidate) => candidate.role === "pending",
  ).length;

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
      toast.success(
        t("updated", { count: result.updated_count }),
      );
      if (result.skipped_count > 0) {
        toast.warning(
          t("skipped", { count: result.skipped_count }),
          {
            description: result.skipped
              .slice(0, 3)
              .map((item) => item.reason)
              .join(" · "),
          },
        );
      }
      setSelected(new Set());
      await queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    } catch {
      toast.error(t("actionError"));
    } finally {
      setActing(false);
    }
  };

  const confirmAction = (action: "reject" | "delete") => {
    const description =
      action === "reject" ? t("confirmReject") : t("confirmDelete");
    show(t("confirmTitle"), description, async () => {
      await runAction(action);
    });
  };

  const exportCsv = () => {
    const rows = [
      ["email", "display_name", "role", "created_at"],
      ...selectedUsers.map((candidate) => [
        candidate.email,
        candidate.display_name ?? "",
        candidate.role ?? "",
        candidate.created_at,
      ]),
    ];
    const csv = toCsv(rows);
    saveBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "users-selection.csv");
  };

  const next = () => {
    if (nextCursor) goToPage([...cursorStack, nextCursor]);
  };

  const previous = () => goToPage(cursorStack.slice(0, -1));

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">{t("title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="gap-2 self-start">
          <Link href="/staff/users">
            <ArrowLeft className="h-4 w-4" />
            {t("back")}
          </Link>
        </Button>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => changeSearch(event.target.value)}
            placeholder={t("search")}
            className="pl-9"
          />
        </div>
        <Select value={role} onValueChange={changeRole}>
          <SelectTrigger className="w-full lg:w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("allRoles")}</SelectItem>
            <SelectItem value="pending">⏳ {tRoles("pending")}</SelectItem>
            <SelectItem value="student">{tRoles("student")}</SelectItem>
            <SelectItem value="moderator">{tRoles("moderator")}</SelectItem>
            <SelectItem value="bureau">{tRoles("bureau")}</SelectItem>
            <SelectItem value="vieux">{tRoles("vieux")}</SelectItem>
            <SelectItem value="guest">{tRoles("guest")}</SelectItem>
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
          <Badge className="mr-1">
            {t("selected", { count: selected.size })}
          </Badge>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            disabled={acting || pendingSelected === 0}
            onClick={() => void runAction("approve")}
          >
            <CheckCircle2 className="h-4 w-4" /> {t("approve")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 text-amber-700"
            disabled={acting || pendingSelected === 0}
            onClick={() => confirmAction("reject")}
          >
            <XCircle className="h-4 w-4" /> {t("reject")}
          </Button>
          <Select
            disabled={acting}
            onValueChange={(value) => void runAction("set_role", value)}
          >
            <SelectTrigger className="h-9 w-40">
              <Shield className="mr-2 h-4 w-4" />
              <SelectValue placeholder={t("setRole")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="student">{tRoles("student")}</SelectItem>
              <SelectItem value="moderator">{tRoles("moderator")}</SelectItem>
              <SelectItem value="bureau">{tRoles("bureau")}</SelectItem>
              <SelectItem value="vieux">{tRoles("vieux")}</SelectItem>
              <SelectItem value="guest">{tRoles("guest")}</SelectItem>
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            disabled={acting}
            onClick={exportCsv}
          >
            <Download className="h-4 w-4" /> {t("export")}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            className="gap-1.5"
            disabled={acting}
            onClick={() => confirmAction("delete")}
          >
            <Trash2 className="h-4 w-4" /> {t("delete")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => setSelected(new Set())}
          >
            {t("clear")}
          </Button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="w-12 p-3">
                  <Checkbox
                    checked={allPageSelected}
                    onCheckedChange={toggleAll}
                    aria-label={t("selectPage")}
                  />
                </th>
                <th className="p-3 font-medium">{t("email")}</th>
                <th className="p-3 font-medium">{t("name")}</th>
                <th className="p-3 font-medium">{t("role")}</th>
                <th className="p-3 font-medium">{t("joined")}</th>
              </tr>
            </thead>
            <tbody className="divide-y" aria-busy={loading}>
              {users.map((candidate) => {
                const isSelf = candidate.id === actor?.id;
                return (
                  <tr
                    key={candidate.id}
                    className={cn(
                      "hover:bg-muted/30",
                      selected.has(candidate.id) && "bg-primary/5",
                      candidate.role === "pending" &&
                        "bg-amber-50/40 dark:bg-amber-950/10",
                    )}
                  >
                    <td className="p-3">
                      <Checkbox
                        checked={selected.has(candidate.id)}
                        disabled={isSelf}
                        onCheckedChange={() => toggle(candidate.id)}
                      />
                    </td>
                    <td className="p-3 font-medium">{candidate.email}</td>
                    <td className="p-3 text-muted-foreground">
                      {candidate.display_name || "—"}
                    </td>
                    <td className="p-3">
                      <Badge
                        className={cn(
                          "border-0 text-xs capitalize",
                          ROLE_BADGE[candidate.role || "student"],
                        )}
                      >
                        {candidate.role === "pending"
                          ? `⏳ ${tRoles("pending")}`
                          : candidate.role === "student"
                            ? tRoles("student")
                            : candidate.role === "moderator"
                              ? tRoles("moderator")
                              : candidate.role === "bureau"
                                ? "Bureau"
                                : candidate.role === "vieux"
                                  ? "Vieux"
                                  : candidate.role === "guest"
                                    ? tRoles("guest")
                                    : (candidate.role || "student")}
                      </Badge>
                    </td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {new Date(candidate.created_at).toLocaleDateString(locale)}
                    </td>
                  </tr>
                );
              })}
              {usersQuery.isError && (
                <tr>
                  <td colSpan={5} className="p-10">
                    <QueryError message={t("loadError")} onRetry={() => void usersQuery.refetch()} />
                  </td>
                </tr>
              )}
              {usersQuery.isSuccess && users.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-10 text-center text-muted-foreground">
                    {t("empty")}
                  </td>
                </tr>
              )}
              {usersQuery.isPending && (
                <tr>
                  <td colSpan={5} className="p-10 text-center text-muted-foreground">
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
          disabled={cursorStack.length === 0 || loading}
          onClick={previous}
        >
          {t("previous")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!nextCursor || loading}
          onClick={next}
        >
          {t("next")}
        </Button>
      </div>
    </div>
  );
}
