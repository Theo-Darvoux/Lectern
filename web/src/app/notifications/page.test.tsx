import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import {
  fetchNotifications,
  fetchUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "@/lib/notifications";
import NotificationsPage from "./page";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));
vi.mock("next-intl", () => {
  const translate = (key: string) => key;
  return { useTranslations: () => translate };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/stores", () => {
  const store = { setUnreadCount: vi.fn(), decrement: vi.fn(), unreadCount: 1 };
  return {
    useNotificationStore: (selector: (state: typeof store) => unknown) => selector(store),
  };
});
vi.mock("@/lib/notifications", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/notifications")>();
  return {
    ...actual,
    fetchNotifications: vi.fn(),
    fetchUnreadCount: vi.fn(),
    markAllNotificationsRead: vi.fn(),
    markNotificationRead: vi.fn(),
    notificationIcon: () => () => <span />,
  };
});

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const notification: NotificationItem = {
  id: "notification-1",
  type: "pr_approved",
  title: "Your contribution was approved",
  body: "The pull request was merged successfully",
  link: "/pull-requests/1",
  read: false,
  created_at: "2026-08-22T00:00:00Z",
};

describe("NotificationsPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(fetchUnreadCount).mockResolvedValue(1);
    vi.mocked(markNotificationRead).mockResolvedValue();
    vi.mocked(markAllNotificationsRead).mockResolvedValue(1);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("keeps notifications visible while a different filter loads", async () => {
    let resolveUnread: ((value: { items: NotificationItem[]; total: number; page: number; pages: number }) => void) | undefined;
    const unread = new Promise<{ items: NotificationItem[]; total: number; page: number; pages: number }>((resolve) => {
      resolveUnread = resolve;
    });
    vi.mocked(fetchNotifications)
      .mockResolvedValueOnce({ items: [notification], total: 1, page: 1, pages: 1 })
      .mockReturnValueOnce(unread);

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const unreadButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("filterUnread"));
    await act(async () => {
      unreadButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain(notification.title);
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    await act(async () => {
      resolveUnread?.({ items: [], total: 0, page: 1, pages: 1 });
      await unread;
    });
    expect(container.textContent).toContain("noUnreadNotifications");
  });

  it("ignores a late pagination response after the filter changes", async () => {
    let resolveOldPage: ((value: { items: NotificationItem[]; total: number; page: number; pages: number }) => void) | undefined;
    const oldPage = new Promise<{ items: NotificationItem[]; total: number; page: number; pages: number }>((resolve) => {
      resolveOldPage = resolve;
    });
    vi.mocked(fetchNotifications)
      .mockResolvedValueOnce({ items: [notification], total: 2, page: 1, pages: 2 })
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce({ items: [], total: 0, page: 1, pages: 1 });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const loadMoreButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("loadMore"));
    await act(async () => {
      loadMoreButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const unreadButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("filterUnread"));
    await act(async () => {
      unreadButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.textContent).toContain("noUnreadNotifications");

    await act(async () => {
      resolveOldPage?.({ items: [{ ...notification, id: "late", title: "Late all notification" }], total: 2, page: 2, pages: 2 });
      await oldPage;
    });
    expect(container.textContent).not.toContain("Late all notification");
  });

  it("marks a notification as read when clicking markRead", async () => {
    vi.mocked(fetchNotifications).mockResolvedValueOnce({
      items: [notification],
      total: 1,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain(notification.title);

    const markReadBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.getAttribute("title") === "markRead");

    await act(async () => {
      markReadBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(markNotificationRead).toHaveBeenCalledWith("notification-1");
  });

  it("marks all notifications as read when clicking markAllRead", async () => {
    vi.mocked(fetchNotifications).mockResolvedValueOnce({
      items: [notification],
      total: 1,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const markAllReadBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("markAllRead"));

    await act(async () => {
      markAllReadBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(markAllNotificationsRead).toHaveBeenCalled();
  });

  it("filters notifications by search query", async () => {
    const notif2: NotificationItem = {
      id: "notification-2",
      type: "material_comment",
      title: "New comment on algebra notes",
      body: "Check out this solution",
      link: null,
      read: false,
      created_at: "2026-08-22T00:00:00Z",
    };

    vi.mocked(fetchNotifications).mockResolvedValueOnce({
      items: [notification, notif2],
      total: 2,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Your contribution was approved");
    expect(container.textContent).toContain("New comment on algebra notes");

    const searchInput = container.querySelector("input") as HTMLInputElement;

    // React controlled input simulation
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    )?.set;
    nativeInputValueSetter?.call(searchInput, "algebra");

    await act(async () => {
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      searchInput.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(container.textContent).not.toContain("Your contribution was approved");
    expect(container.textContent).toContain("New comment on algebra notes");
  });

  it("selects and deselects all notifications via toolbar button without duplicate select-all buttons", async () => {
    const notif2: NotificationItem = {
      id: "notification-2",
      type: "material_comment",
      title: "New comment on algebra notes",
      body: "Check out this solution",
      link: null,
      read: false,
      created_at: "2026-08-22T00:00:00Z",
    };

    vi.mocked(fetchNotifications).mockResolvedValueOnce({
      items: [notification, notif2],
      total: 2,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // Find toolbar select all button
    const selectAllBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("selectAll") || b.getAttribute("title") === "selectAll");
    expect(selectAllBtn).toBeDefined();

    // Click select all
    await act(async () => {
      selectAllBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Verify all items selected count shown in batch bar
    expect(container.textContent).toContain("selectedCount");

    // Verify there is only ONE deselectAll / selectAll button in the DOM (the toolbar button)
    const deselectButtons = Array.from(container.querySelectorAll("button"))
      .filter((b) => b.textContent?.includes("deselectAll") || b.textContent?.includes("selectAll"));
    expect(deselectButtons.length).toBe(1);

    // Click deselect all on toolbar button
    await act(async () => {
      deselectButtons[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Batch bar should disappear
    expect(container.textContent).not.toContain("selectedCount");
  });

  it("marks selected notifications as read via batch action", async () => {
    const notif2: NotificationItem = {
      id: "notification-2",
      type: "material_comment",
      title: "New comment on algebra notes",
      body: "Check out this solution",
      link: null,
      read: false,
      created_at: "2026-08-22T00:00:00Z",
    };

    vi.mocked(fetchNotifications).mockResolvedValueOnce({
      items: [notification, notif2],
      total: 2,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<NotificationsPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // Select all
    const selectAllBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("selectAll"));
    await act(async () => {
      selectAllBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Click batch mark as read
    const markSelectedBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("markSelectedRead"));
    expect(markSelectedBtn).toBeDefined();

    await act(async () => {
      markSelectedBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(markNotificationRead).toHaveBeenCalledWith("notification-1");
    expect(markNotificationRead).toHaveBeenCalledWith("notification-2");
  });
});

