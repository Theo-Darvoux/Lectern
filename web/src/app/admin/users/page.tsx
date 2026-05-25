"use client";

import { useEffect, useState } from "react";
import { Trash2, Search, CheckCircle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api-client";
import { useAuth } from "@/hooks/use-auth";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

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

const ROLE_BADGE: Record<string, string> = {
    pending: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
    student: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    moderator: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
    bureau: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    vieux: "bg-gray-100 text-gray-800 dark:bg-gray-800/50 dark:text-gray-300",
    guest: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
};

export default function AdminUsersPage() {
    const t = useTranslations("Admin.Users");
    const { user } = useAuth();
    const canManageRoles = user?.role === "bureau" || user?.role === "vieux";
    const { show } = useConfirmDialog();

    const [users, setUsers] = useState<AdminUser[]>([]);
    const [total, setTotal] = useState(0);
    const [cursorStack, setCursorStack] = useState<string[]>([]);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [roleFilter, setRoleFilter] = useState("all");
    const [loading, setLoading] = useState(true);

    const fetchUsers = async (cursor: string | null = null) => {
        setLoading(true);
        try {
            const params = new URLSearchParams({ limit: "50" });
            if (cursor) params.append("cursor", cursor);
            if (search) params.append("search", search);
            if (roleFilter !== "all") params.append("role", roleFilter);

            const data = await apiFetch<PaginatedUsers>(`/admin/users?${params}`);
            setUsers(data.items);
            setTotal(data.total);
            setNextCursor(data.next_cursor);
        } catch {
            toast.error(t("state.loadError"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        setCursorStack([]);
        setNextCursor(null);
        const timer = setTimeout(() => fetchUsers(null), 300);
        return () => clearTimeout(timer);
    }, [search, roleFilter]);

    const handleNext = () => {
        if (!nextCursor) return;
        setCursorStack((prev) => {
            fetchUsers(nextCursor);
            return [...prev, nextCursor];
        });
    };

    const handlePrev = () => {
        setCursorStack((prev) => {
            const stack = [...prev];
            stack.pop();
            const prevCursor = stack.length > 0 ? stack[stack.length - 1] : null;
            fetchUsers(prevCursor);
            return stack;
        });
    };

    const handleRoleChange = async (userId: string, newRole: string) => {
        try {
            await apiFetch(`/admin/users/${userId}/role?role=${newRole}`, {
                method: "PATCH",
            });
            setUsers((prev) =>
                prev.map((u) => (u.id === userId ? { ...u, role: newRole } : u))
            );
            toast.success(t("actions.roleUpdate.success"));
        } catch {
            toast.error(t("actions.roleUpdate.error"));
        }
    };

    const handleApprove = async (userId: string, email: string) => {
        show(
            t("actions.approve.title"),
            t("actions.approve.description", { email }),
            async () => {
                try {
                    const updated = await apiFetch<AdminUser>(`/admin/users/${userId}/approve`, { method: "POST" });
                    setUsers((prev) =>
                        prev.map((u) => (u.id === userId ? { ...u, role: updated.role } : u))
                    );
                    toast.success(t("actions.approve.success"));
                } catch {
                    toast.error(t("actions.approve.error"));
                }
            }
        );
    };

    const handleReject = async (userId: string, email: string) => {
        show(
            t("actions.reject.title"),
            t("actions.reject.description", { email }),
            async () => {
                try {
                    await apiFetch(`/admin/users/${userId}/reject`, { method: "POST" });
                    setUsers((prev) => prev.filter((u) => u.id !== userId));
                    toast.success(t("actions.reject.success"));
                } catch {
                    toast.error(t("actions.reject.error"));
                }
            }
        );
    };

    const handleDelete = (userId: string, email: string) => {
        show(
            t("actions.delete.title"),
            t("actions.delete.description", { email }),
            async () => {
                try {
                    await apiFetch(`/admin/users/${userId}`, { method: "DELETE" });
                    setUsers((prev) => prev.filter((u) => u.id !== userId));
                    toast.success(t("actions.delete.success"));
                } catch {
                    toast.error(t("actions.delete.error"));
                }
            }
        );
    };

    const pendingCount = users.filter((u) => u.role === "pending").length;
    const hasPrev = cursorStack.length > 0;
    const hasNext = !!nextCursor;

    return (
        <div className="space-y-4">
            {/* Pending banner */}
            {roleFilter === "pending" || pendingCount > 0 ? (
                <div
                    role="alert"
                    className={cn(
                        "flex items-center gap-3 rounded-lg border px-4 py-3 text-sm",
                        "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300"
                    )}
                >
                    <span className="font-medium">
                        {t("pendingBanner", { count: pendingCount })}
                    </span>
                    {roleFilter !== "pending" && (
                        <button
                            onClick={() => setRoleFilter("pending")}
                            className="ml-auto text-xs underline underline-offset-2 hover:no-underline"
                        >
                            {t("showOnlyPending")}
                        </button>
                    )}
                </div>
            ) : null}

            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                <div className="relative flex-1">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder={t("searchPlaceholder")}
                        className="pl-9"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
                <Select value={roleFilter} onValueChange={setRoleFilter}>
                    <SelectTrigger className="w-full sm:w-[180px]">
                        <SelectValue placeholder={t("allRoles")} />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("allRoles")}</SelectItem>
                        <SelectItem value="pending">⏳ {t("roleLabels.pending")}</SelectItem>
                        <SelectItem value="student">{t("roleLabels.student")}</SelectItem>
                        <SelectItem value="moderator">{t("roleLabels.moderator")}</SelectItem>
                        <SelectItem value="bureau">{t("roleLabels.bureau")}</SelectItem>
                        <SelectItem value="vieux">{t("roleLabels.vieux")}</SelectItem>
                        <SelectItem value="guest">{t("roleLabels.guest")}</SelectItem>
                    </SelectContent>
                </Select>
                <span className="text-sm text-muted-foreground whitespace-nowrap">
                    {t("userCount", { count: total })}
                </span>
            </div>

            <div className="rounded-lg border bg-card">
                <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                        <thead className="border-b bg-muted/50 text-muted-foreground">
                            <tr>
                                <th className="p-4 font-medium">{t("table.email")}</th>
                                <th className="p-4 font-medium">{t("table.name")}</th>
                                <th className="p-4 font-medium">{t("table.role")}</th>
                                <th className="p-4 font-medium text-right">{t("table.actions")}</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y">
                            {users.map((u) => (
                                <tr
                                    key={u.id}
                                    className={cn(
                                        "transition-colors hover:bg-muted/30",
                                        u.role === "pending" && "bg-amber-50/50 dark:bg-amber-900/10"
                                    )}
                                >
                                    <td className="p-4 font-medium">{u.email}</td>
                                    <td className="p-4 text-muted-foreground">
                                        {u.display_name ?? "-"}
                                    </td>
                                    <td className="p-4">
                                        {canManageRoles && u.id !== user?.id && u.role !== "pending" && u.role !== "guest" ? (
                                            <Select
                                                value={u.role || "student"}
                                                onValueChange={(val) => handleRoleChange(u.id, val)}
                                            >
                                                <SelectTrigger className="h-8 max-w-[120px] text-xs">
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="student">{t("roleLabels.student")}</SelectItem>
                                                    <SelectItem value="moderator">{t("roleLabels.moderator")}</SelectItem>
                                                    <SelectItem value="bureau">{t("roleLabels.bureau")}</SelectItem>
                                                    <SelectItem value="vieux">{t("roleLabels.vieux")}</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <Badge
                                                className={cn(
                                                    "capitalize text-xs font-medium border-0",
                                                    ROLE_BADGE[u.role ?? "student"] ?? ROLE_BADGE.student
                                                )}
                                            >
                                                {u.role === "pending"
                                                    ? `⏳ ${t("roleLabels.pending")}`
                                                    : (u.role ? t(`roleLabels.${u.role}` as any) : t("roleLabels.student"))}
                                            </Badge>
                                        )}
                                    </td>
                                    <td className="p-4 text-right">
                                        <div className="flex items-center justify-end gap-1">
                                            {/* Pending user: approve + reject */}
                                            {canManageRoles && u.role === "pending" && (
                                                <>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => handleApprove(u.id, u.email)}
                                                        className="text-green-600 hover:bg-green-50 hover:text-green-700 dark:hover:bg-green-900/20"
                                                        title={t("actions.approve.label")}
                                                    >
                                                        <CheckCircle className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => handleReject(u.id, u.email)}
                                                        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                                        title={t("actions.reject.label")}
                                                    >
                                                        <XCircle className="h-4 w-4" />
                                                    </Button>
                                                </>
                                            )}
                                            {/* Non-pending, non-guest: delete */}
                                            {canManageRoles && u.id !== user?.id && u.role !== "pending" && u.role !== "guest" && (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => handleDelete(u.id, u.email)}
                                                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                                    title={t("actions.delete.label")}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                            {loading && users.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="p-8 text-center text-muted-foreground">
                                        {t("state.loading")}
                                    </td>
                                </tr>
                            )}
                            {!loading && users.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="p-8 text-center text-muted-foreground">
                                        {t("state.empty")}
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            {(hasPrev || hasNext) && (
                <div className="flex items-center justify-center gap-2">
                    <Button
                        variant="ghost"
                        size="sm"
                        disabled={!hasPrev}
                        onClick={handlePrev}
                    >
                        {t("pagination.previous")}
                    </Button>
                    <Button
                        variant="ghost"
                        size="sm"
                        disabled={!hasNext}
                        onClick={handleNext}
                    >
                        {t("pagination.next")}
                    </Button>
                </div>
            )}
        </div>
    );
}
