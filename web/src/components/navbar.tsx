"use client";

import Link from "next/link";
import Image from "next/image";
import { SiteName } from "@/components/site-name";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import {
  Bell,
  Bookmark,
  Inbox,
  Search,
  User,
  Settings,
  LogOut,
  LogIn,
  Folder,
  HelpCircle,
  Shield,
  Trophy,
} from "lucide-react";
import { useState, useEffect } from "react";
import { SearchModal } from "@/components/search/search-modal";
import { SearchInline } from "@/components/search/search-inline";
import { useNotificationStore, useConfigStore, usePRStore, useUIStore } from "@/lib/stores";
import { useTutorialMenuOpen } from "@/lib/tutorials/tutorial-store";
import { tutorialsEnabled } from "@/lib/tutorials/use-tutorial";
import { isGuest } from "@/lib/guest";
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
import { API_BASE } from "@/lib/api-client";
import { fetchUnreadCount } from "@/lib/notifications";
import { fetchOpenPRCount } from "@/lib/pr-client";
import { useTranslations } from "next-intl";

export function Navbar() {
  const t = useTranslations("Navigation");
  const tCommon = useTranslations("Common");
  const tSaved = useTranslations("Sidebar");
  const tStaff = useTranslations("Staff");
  const tHelp = useTranslations("Tutorials.helpCenter");
  const { user, isAuthenticated, logout } = useAuth();
  const guest = isGuest(user);
  const searchOpen = useUIStore((state) => state.searchOpen);
  const setSearchOpen = useUIStore((state) => state.setSearchOpen);
  const unreadCount = useNotificationStore((state) => state.unreadCount);
  const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
  const openPRCount = usePRStore((state) => state.openPRCount);
  const setOpenPRCount = usePRStore((state) => state.setOpenPRCount);
  const config = useConfigStore((state) => state.config);
  const pathname = usePathname();

  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const tutorialProfileOpen = useTutorialMenuOpen("profile-menu");
  // Re-render when the runtime toggle loads so the Help center link hides.
  const tutorialsDisabled = useConfigStore((s) => s.config?.tutorials_enabled === false);
  const showHelp = !tutorialsDisabled && tutorialsEnabled();

  useSSE();

  useEffect(() => {
    if (isAuthenticated && user && !guest) {
      fetchOpenPRCount()
        .then((count) => setOpenPRCount(count))
        .catch(() => {});
      fetchUnreadCount()
        .then((count) => setUnreadCount(count))
        .catch(() => {});
    }
  }, [pathname, isAuthenticated, user, guest, setOpenPRCount, setUnreadCount]);

  useEffect(() => {
    if (!isAuthenticated) return;
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSearchOpen(!useUIStore.getState().searchOpen);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [isAuthenticated, setSearchOpen]);

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
            data-tutorial="nav-home"
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
        {isAuthenticated && (
          <div
            data-tutorial="nav-search"
            className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md px-4 pointer-events-none lg:pointer-events-auto hidden lg:block"
          >
            <SearchInline />
          </div>
        )}

        {/* Right: Actions */}
        <div className="flex w-1/3 justify-end items-center gap-1 sm:gap-2">
          {/* Search icon — mobile only (desktop uses the centred search bar) */}
          {isAuthenticated && (
            <Button
              variant="ghost"
              size="icon"
              data-tutorial="nav-search"
              className="lg:hidden h-9 w-9 text-muted-foreground"
              onClick={() => setSearchOpen(true)}
              aria-label={tCommon("commandSearchPlaceholder")}
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
                  data-tutorial="nav-contributions"
                  title={t("contributions")}
                  className={`relative rounded-full ${pathname.startsWith("/pull-requests") ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"}`}
                >
                  <Inbox className="h-4 w-4" />
                  {openPRCount > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground border-2 border-background">
                      {openPRCount > 99 ? "99+" : openPRCount}
                    </span>
                  )}
                </Button>
              </Link>

              {/* Notifications — desktop only */}
              <Link href="/notifications" className="hidden md:block">
                <Button
                  variant="ghost"
                  size="icon"
                  data-tutorial="nav-notifications"
                  className={`relative rounded-full ${pathname.startsWith("/notifications") ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"}`}
                  title={t("notifications")}
                >
                  <Bell className="h-4 w-4" />
                  {unreadCount > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground border-2 border-background">
                      {unreadCount > 99 ? "99+" : unreadCount}
                    </span>
                  )}
                </Button>
              </Link>

              <DropdownMenu
                modal={false}
                open={tutorialProfileOpen || profileMenuOpen}
                onOpenChange={(o) => {
                  if (!tutorialProfileOpen) setProfileMenuOpen(o);
                }}
              >
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-tutorial="nav-profile"
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
                <DropdownMenuContent
                  align="end"
                  className={`w-56 ${tutorialProfileOpen ? "z-[1001] pointer-events-none" : ""}`}
                  onInteractOutside={(e) => {
                    if (tutorialProfileOpen) e.preventDefault();
                  }}
                >
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
                    <Link href="/saved">
                      <Bookmark className="mr-2 h-4 w-4" />
                      <span>{tSaved("saved")}</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild className="cursor-pointer">
                    <Link href="/leaderboard">
                      <Trophy className="mr-2 h-4 w-4" />
                      <span>{t("leaderboard")}</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild className="cursor-pointer">
                    <Link href="/settings">
                      <Settings className="mr-2 h-4 w-4" />
                      <span>{t("settings")}</span>
                    </Link>
                  </DropdownMenuItem>
                  {showHelp && (
                    <DropdownMenuItem asChild className="cursor-pointer">
                      <Link href="/help">
                        <HelpCircle className="mr-2 h-4 w-4" />
                        <span>{tHelp("open")}</span>
                      </Link>
                    </DropdownMenuItem>
                  )}
                  {(user.role === "moderator" || user.role === "bureau" || user.role === "vieux") && (
                    <DropdownMenuItem asChild className="cursor-pointer">
                      <Link href="/staff">
                        <Shield className="mr-2 h-4 w-4 text-purple-600 dark:text-purple-400" />
                        <span>{tStaff("title")}</span>
                      </Link>
                    </DropdownMenuItem>
                  )}
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
      {isAuthenticated && <SearchModal open={searchOpen} onOpenChange={setSearchOpen} />}
    </nav>
  );
}
