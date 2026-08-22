import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import {
  fetchNotifications,
  fetchUnreadCount,
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
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/lib/stores", () => {
  const store = { setUnreadCount: vi.fn(), decrement: vi.fn() };
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
  body: null,
  link: null,
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
      .find((button) => button.textContent === "filterUnread");
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
      .find((button) => button.textContent === "loadMore");
    await act(async () => {
      loadMoreButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const unreadButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "filterUnread");
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
});
