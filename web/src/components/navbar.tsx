"use client";

import Link from "next/link";
import Image from "next/image";
import { SiteName } from "@/components/site-name";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import {
  Bell,
  Inbox,
  Search,
  User,
  Settings,
  LogOut,
  LogIn,
  Folder,
} from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { SearchModal } from "@/components/search/search-modal";
import { SearchInline } from "@/components/search/search-inline";
import { useNotificationStore, useConfigStore, isGuest } from "@/lib/stores";
import { useSSE } from "@/hooks/use-sse";
import { usePathname } from "next/navigation";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { apiFetch, API_BASE } from "@/lib/api-client";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import { useTranslations } from "next-intl";

interface NavbarNotification {
  id: string;
  title: string;
  link?: string;
  read: boolean;
  created_at: string;
}

interface NotificationsResponse {
  items: NavbarNotification[];
  total: number;
}

export function Navbar() {
  const t = useTranslations("Navigation");
  const tCommon = useTranslations("Common");
  const { user, isAuthenticated, logout } = useAuth();
  const guest = isGuest(user);
  const [searchOpen, setSearchOpen] = useState(false);
  const { unreadCount, setUnreadCount } = useNotificationStore();
  const { config } = useConfigStore();
  const pathname = usePathname();

  const [popoverOpen, setPopoverOpen] = useState(false);
  const [recentNotifications, setRecentNotifications] = useState<
    NavbarNotification[]
  >([]);
  const [loadingNotifications, setLoadingNotifications] = useState(false);

  useSSE();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        // Only trigger modal on mobile. On desktop, SearchInline handles focusing the input.
        if (window.innerWidth < 1024) {
          e.preventDefault();
          setSearchOpen((open) => !open);
        }
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const fetchRecentNotifications = useCallback(async () => {
    setLoadingNotifications(true);
    try {
      // Fetch unread count first to sync badge
      const unreadData = await apiFetch<NotificationsResponse>(
        "/notifications?read=false&limit=1",
      );
      setUnreadCount(unreadData.total);

      // Fetch recent 5 for popover
      const data = await apiFetch<NotificationsResponse>(
        "/notifications?limit=5",
      );
      setRecentNotifications(data.items || []);
    } catch {
      // Ignore for popover
    } finally {
      setLoadingNotifications(false);
    }
  }, [setUnreadCount]);

  useEffect(() => {
    if (popoverOpen) {
      fetchRecentNotifications();
    }
  }, [popoverOpen, fetchRecentNotifications]);

  const initials = user?.display_name
    ? user.display_name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase()
    : user?.email?.slice(0, 2).toUpperCase() || "?";

  return (
    <nav className="sticky top-0 z-[60] h-14 border-b bg-background/80 backdrop-blur-md supports-backdrop-filter:bg-background/60">
      <div className="flex h-full w-full items-center justify-between px-4 sm:px-6 relative">
        {/* Left: Brand */}
        <div className="flex w-1/3 justify-start items-center gap-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-xl font-extrabold tracking-tight hover:opacity-80 transition-opacity"
          >
            {config?.site_logo_url && (
              <Image 
                src={config.site_logo_url} 
                alt={config?.site_name || "Logo"} 
                width={32}
                height={32}
                className="h-8 w-auto object-contain"
                unoptimized
              />
            )}
            <SiteName
              name={config?.site_name || ""}
              style={config?.site_name_style}
              gradientClassName="bg-linear-to-br from-foreground to-foreground/70 bg-clip-text text-transparent"
            />
          </Link>
          {isAuthenticated && (
            <Link href="/browse" className="hidden sm:block">
              <Button
                variant="ghost"
                size="sm"
                className={`gap-2 rounded-lg font-medium ${pathname.startsWith("/browse") ? "text-foreground bg-accent" : "text-muted-foreground hover:text-foreground"}`}
              >
                <Folder className="h-4 w-4" />
                <span>{t("browse")}</span>
              </Button>
            </Link>
          )}
        </div>

        {/* Center: Search */}
        {pathname !== "/login" && (
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md px-4 pointer-events-none lg:pointer-events-auto hidden lg:block">
            <SearchInline />
          </div>
        )}

        {/* Right: Actions */}
        <div className="flex w-1/3 justify-end items-center gap-1 sm:gap-2">
          {/* Search icon — mobile only (desktop uses the centred search bar) */}
          {pathname !== "/login" && (
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden h-9 w-9 text-muted-foreground"
              onClick={() => setSearchOpen(true)}
            >
              <Search className="h-4 w-4" />
            </Button>
          )}
          {guest ? (
            <div className="flex items-center gap-1 sm:gap-2">
              <span className="flex items-center rounded-full border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                <span>{t("guest")}</span>
              </span>
              <Button
                variant="outline"
                size="sm"
                className="rounded-full px-2 sm:px-3"
                onClick={logout}
              >
                <LogIn className="h-4 w-4 shrink-0" />
                <span className="hidden sm:inline sm:ml-2">{t("exitGuest")}</span>
              </Button>
            </div>
          ) : isAuthenticated && user ? (
            <>
              {/* Contributions — desktop only (bottom bar handles mobile nav) */}
              <Link href="/pull-requests" className="hidden md:block">
                <Button
                  variant="ghost"
                  size="icon"
                  title={t("contributions")}
                  className={`rounded-full ${pathname.startsWith("/pull-requests") ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"}`}
                >
                  <Inbox className="h-4 w-4" />
                </Button>
              </Link>

              <div className="hidden md:flex items-center">
                <Popover open={popoverOpen} onOpenChange={setPopoverOpen} modal={false}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className={`relative rounded-full ${pathname.startsWith("/notifications") || popoverOpen ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"}`}
                      title={t("notifications")}
                    >
                      <Bell className="h-4 w-4" />
                      {unreadCount > 0 && (
                        <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground border-2 border-background">
                          {unreadCount > 99 ? "99+" : unreadCount}
                        </span>
                      )}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80 p-0" align="end">
                    <div className="flex items-center justify-between border-b px-4 py-3">
                      <p className="text-sm font-semibold">{t("notifications")}</p>
                      <Link
                        href="/notifications"
                        className="text-xs text-primary hover:underline"
                        onClick={() => setPopoverOpen(false)}
                      >
                        {t("viewAll")}
                      </Link>
                    </div>
                    <div className="max-h-[300px] overflow-y-auto">
                      {loadingNotifications ? (
                        <div className="p-4 text-center text-sm text-muted-foreground">
                          {tCommon("loading")}
                        </div>
                      ) : recentNotifications.length === 0 ? (
                        <div className="p-4 text-center text-sm text-muted-foreground">
                          {t("noNewNotifications")}
                        </div>
                      ) : (
                        <div className="flex flex-col">
                          {recentNotifications.map((n) => (
                            <Link
                              key={n.id}
                              href={n.link || "/notifications"}
                              onClick={() => setPopoverOpen(false)}
                              className={`flex flex-col gap-1 border-b p-3 text-sm transition-colors hover:bg-muted/50 ${n.read ? "opacity-70" : "bg-muted/10 font-medium"}`}
                            >
                              <span className="line-clamp-2">{n.title}</span>
                              <span className="text-xs text-muted-foreground">
                                {formatDistanceToNow(new Date(n.created_at), {
                                  addSuffix: true,
                                })}
                              </span>
                            </Link>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="border-t p-2">
                      <Link
                        href="/notifications"
                        onClick={() => setPopoverOpen(false)}
                      >
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full text-xs"
                        >
                          {t("goToNotifications")}
                        </Button>
                      </Link>
                    </div>
                  </PopoverContent>
                </Popover>
              </div>

              <DropdownMenu modal={false}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="gap-2 rounded-full pl-2 pr-3"
                    title={t("profile")}
                  >
                    <Avatar size="sm" className="h-6 w-6 border border-border">
                      <AvatarImage
                        src={
                          user.avatar_url
                            ? `${API_BASE}/users/${user.id}/avatar?v=${encodeURIComponent(user.avatar_url)}`
                            : undefined
                        }
                      />
                      <AvatarFallback className="text-[10px]">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <span className="hidden sm:inline font-medium">
                      {user.display_name ?? user.email}
                    </span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <div className="flex items-center justify-start gap-2 p-2">
                    <div className="flex flex-col space-y-1 leading-none">
                      {user.display_name && (
                        <p className="font-medium">{user.display_name}</p>
                      )}
                      <p className="w-[200px] truncate text-xs text-muted-foreground">
                        {user.email}
                      </p>
                    </div>
                  </div>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem asChild className="cursor-pointer">
                    <Link href="/profile">
                      <User className="mr-2 h-4 w-4" />
                      <span>{t("profile")}</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild className="cursor-pointer">
                    <Link href="/settings">
                      <Settings className="mr-2 h-4 w-4" />
                      <span>{t("settings")}</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={logout}
                    className="cursor-pointer text-destructive focus:bg-destructive/10 focus:text-destructive"
                  >
                    <LogOut className="mr-2 h-4 w-4" />
                    <span>{t("logout")}</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          ) : (
            <Link href="/login">
              <Button variant="ghost" size="sm" className="rounded-full">
                {t("login")}
              </Button>
            </Link>
          )}
        </div>
      </div>
      <SearchModal open={searchOpen} onOpenChange={setSearchOpen} />
    </nav>
  );
}
