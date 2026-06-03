import { apiFetch } from "@/lib/api-client";

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
