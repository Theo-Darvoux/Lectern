import { createElement, type ElementType } from "react";
import {
  CheckCircle2,
  Flag,
  GitPullRequest,
  History,
  MessageSquare,
  UserCheck,
} from "lucide-react";
import { apiFetch } from "@/lib/api-client";

/** Maps a notification type to its display icon. Shared by the navbar popover
 * and the full notifications page so the two stay visually consistent. */
const NOTIFICATION_ICONS: Record<string, ElementType> = {
  pr_approved: CheckCircle2,
  pr_rejected: GitPullRequest,
  pr_reverted: History,
  pr_comment_reply: MessageSquare,
  annotation_reply: MessageSquare,
  material_annotation: MessageSquare,
  material_comment: MessageSquare,
  flag_resolved: Flag,
  new_flag: Flag,
  pending_user: UserCheck,
  access_approved: UserCheck,
};

export function notificationIcon(type: string): ElementType {
  return NOTIFICATION_ICONS[type] ?? MessageSquare;
}

export function NotificationIcon({
  type,
  className,
}: {
  type: string;
  className?: string;
}) {
  const Icon = NOTIFICATION_ICONS[type] ?? MessageSquare;
  return createElement(Icon, { className });
}

export type NotificationCategory = "all" | "pr" | "comment" | "moderation" | "system";

export function getNotificationCategory(type: string): "pr" | "comment" | "moderation" | "system" {
  if (type.startsWith("pr_") || type.startsWith("pull_request")) {
    return "pr";
  }
  if (
    type.includes("comment") ||
    type.includes("annotation") ||
    type.includes("reply")
  ) {
    return "comment";
  }
  if (type.includes("flag")) {
    return "moderation";
  }
  if (
    type.includes("user") ||
    type.includes("access") ||
    type.includes("system")
  ) {
    return "system";
  }
  return "pr";
}

export function getNotificationStyle(type: string, isRead: boolean) {
  switch (type) {
    case "pr_approved":
    case "access_approved":
      return {
        bg: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
      };
    case "pr_rejected":
    case "new_flag":
      return {
        bg: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
        badge: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
      };
    case "pr_reverted":
      return {
        bg: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
        badge: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
      };
    case "flag_resolved":
      return {
        bg: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
        badge: "bg-teal-500/10 text-teal-600 dark:text-teal-400 border-teal-500/20",
      };
    case "pending_user":
      return {
        bg: "bg-purple-500/10 text-purple-600 dark:text-purple-400",
        badge: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
      };
    case "material_comment":
    case "material_annotation":
    case "annotation_reply":
    case "pr_comment_reply":
    default:
      return {
        bg: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
        badge: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
      };
  }
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  body: string | null;
  link: string | null;
  read: boolean;
  created_at: string;
}

export interface PaginatedNotifications {
  items: NotificationItem[];
  total: number;
  page: number;
  pages: number;
}

/** Authoritative unread count — single source of truth for the badge. */
export async function fetchUnreadCount(): Promise<number> {
  const data = await apiFetch<{ count: number }>(
    "/notifications/unread-count",
  );
  return data.count;
}

export async function fetchNotifications(params: {
  page?: number;
  limit?: number;
  read?: boolean;
} = {}): Promise<PaginatedNotifications> {
  const qs = new URLSearchParams();
  if (params.page != null) qs.set("page", String(params.page));
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.read != null) qs.set("read", String(params.read));
  const query = qs.toString();
  return apiFetch<PaginatedNotifications>(
    `/notifications${query ? `?${query}` : ""}`,
  );
}

export async function markNotificationRead(id: string): Promise<void> {
  await apiFetch(`/notifications/${id}/read`, { method: "PATCH" });
}

export async function markAllNotificationsRead(): Promise<number> {
  const res = await apiFetch<{ marked: number }>("/notifications/read-all", {
    method: "POST",
  });
  return res.marked;
}
