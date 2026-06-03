"use client";

import { useRef, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useIsMobile } from "@/hooks/use-media-query";
import { useAuth } from "@/hooks/use-auth";
import { useNotificationStore, useUIStore } from "@/lib/stores";
import { isGuest } from "@/lib/guest";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

const emptySubscribe = () => () => {};
import {
  Home,
  Folder,
  Send,
  Bell,
  User,
  type LucideIcon,
} from "lucide-react";

interface NavItem {
  href: string;
  labelKey: "home" | "browse" | "prs" | "inbox" | "profile";
  Icon: LucideIcon;
  match: (pathname: string) => boolean;
  hasBadge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    labelKey: "home",
    Icon: Home,
    match: (p) => p === "/",
  },
  {
    href: "/browse",
    labelKey: "browse",
    Icon: Folder,
    match: (p) => p.startsWith("/browse"),
  },
  {
    href: "/pull-requests",
    labelKey: "prs",
    Icon: Send,
    match: (p) => p.startsWith("/pull-requests"),
  },
  {
    href: "/notifications",
    labelKey: "inbox",
    Icon: Bell,
    match: (p) => p.startsWith("/notifications"),
    hasBadge: true,
  },
  {
    href: "/profile",
    labelKey: "profile",
    Icon: User,
    match: (p) => p.startsWith("/profile"),
  },
];

export function MobileBottomBar() {
  const t = useTranslations("Navigation");
  const isMobile = useIsMobile();
  const { isAuthenticated, user } = useAuth();
  const { unreadCount } = useNotificationStore();
  const { hideFooter, navbarVisible, setMaterialActionsOpen } = useUIStore();
  const pathname = usePathname();
  const mounted = useSyncExternalStore(emptySubscribe, () => true, () => false);
  const touchStartY = useRef<number | null>(null);

  const handleTouchStart = (e: React.TouchEvent) => {
    touchStartY.current = e.touches[0].clientY;
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (touchStartY.current === null) return;
    const touchEndY = e.changedTouches[0].clientY;
    const deltaY = touchStartY.current - touchEndY;
    const threshold = 30; // Min pixels for swipe up

    // We only trigger when hideFooter is true (viewer context)
    if (deltaY > threshold && hideFooter) {
      setMaterialActionsOpen(true);
    }
    touchStartY.current = null;
  };

  if (!mounted || !isMobile || !isAuthenticated) return null;

  // Guests have no PRs, notifications, or profile — only browsing tabs.
  const navItems = isGuest(user)
    ? NAV_ITEMS.filter((i) => i.labelKey === "home" || i.labelKey === "browse")
    : NAV_ITEMS;

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-[60] flex justify-center px-4 transition-transform duration-300 ease-in-out"
      style={{
        paddingBottom: "max(env(safe-area-inset-bottom, 0px), 14px)",
        transform: navbarVisible ? "translateZ(0)" : "translateY(100%) translateZ(0)",
        WebkitTransform: navbarVisible ? "translateZ(0)" : "translateY(100%) translateZ(0)",
      }}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <nav
        aria-label={t("mainNavigationAria")}
        className={cn(
          "flex w-full max-w-md items-center",
          "rounded-2xl px-1.5 py-1.5",
          "border border-border/40 dark:border-border/25",
          "bg-background/75 dark:bg-background/70",
          "backdrop-blur-2xl",
          "shadow-xl shadow-black/[0.07] dark:shadow-black/30",
          // Subtle inner glow on the top edge
          "ring-1 ring-inset ring-white/20 dark:ring-white/4",
        )}
      >
        {navItems.map(({ href, labelKey, Icon, match, hasBadge }) => {
          const isActive = match(pathname);
          const showBadge = !!hasBadge && unreadCount > 0;
          const label = t(labelKey);

          return (
            <Link
              key={href}
              href={href}
              aria-label={label}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                // Layout
                "relative flex flex-1 items-center justify-center gap-1.5",
                "rounded-xl py-2.5",
                // Transition — all properties
                "transition-all duration-300 ease-out",
                // Active pill: foreground bg, background text
                isActive
                  ? "bg-foreground text-background shadow-md shadow-black/12 dark:shadow-black/30"
                  : [
                      "text-muted-foreground",
                      "hover:text-foreground hover:bg-accent/70",
                      "active:scale-95 active:opacity-75",
                    ],
              )}
            >
              {/* ── Icon (with optional badge) ── */}
              <span className="relative shrink-0">
                <Icon
                  key={isActive ? "active" : "inactive"}
                  style={{ width: 19, height: 19 }}
                  strokeWidth={isActive ? 2.3 : 1.75}
                />

                {showBadge && (
                  <span
                    className={cn(
                      "absolute -right-1.75 -top-1.75",
                      "flex min-w-4 items-center justify-center",
                      "h-4 rounded-full px-0.75",
                      "text-[9px] font-bold tabular-nums leading-none",
                      "ring-[1.5px]",
                      isActive
                        ? "bg-background text-foreground ring-foreground"
                        : "bg-destructive text-white ring-background dark:ring-background",
                    )}
                  >
                    {unreadCount > 99 ? "99+" : unreadCount}
                  </span>
                )}
              </span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
