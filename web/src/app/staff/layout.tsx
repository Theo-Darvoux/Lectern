"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import {
  LayoutDashboard,
  GitPullRequest,
  Flag,
  Star,
  FolderTree,
  BookOpenCheck,
  Users,
  AlertTriangle,
  Archive,
  ServerCog,
  Wrench,
  ShieldCheck,
  Crown,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export default function StaffLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("Staff");
  const { user, isAuthenticated } = useAuth();
  const pathname = usePathname();

  if (!isAuthenticated) return null;

  const isModerator = user?.role === "moderator";
  const isAdmin = user?.role === "bureau" || user?.role === "vieux";
  const isStaff = isModerator || isAdmin;

  if (!isStaff) {
    return (
      <div className="flex items-center justify-center p-12 text-muted-foreground">
        {t("noPermission")}
      </div>
    );
  }

  // Base navigation items for all staff (moderators + admins)
  const navItems = [
    {
      href: "/staff",
      label: t("nav.dashboard"),
      icon: LayoutDashboard,
      exact: true,
    },
    {
      href: "/staff/pull-requests",
      label: t("nav.pullRequests"),
      icon: GitPullRequest,
      tut: "staff-nav-prs",
    },
    {
      href: "/staff/flags",
      label: t("nav.flags"),
      icon: Flag,
      tut: "staff-nav-flags",
    },
    {
      href: "/staff/content",
      label: t("nav.content"),
      icon: BookOpenCheck,
      tut: "staff-nav-content",
    },
  ];

  // Admin-only navigation items
  if (isAdmin) {
    navItems.push(
      {
        href: "/staff/users",
        label: t("nav.users"),
        icon: Users,
        tut: "staff-nav-users",
      },
      {
        href: "/staff/dlq",
        label: t("nav.dlq"),
        icon: AlertTriangle,
        tut: "staff-nav-dlq",
      },
      {
        href: "/staff/backup",
        label: t("nav.backup"),
        icon: Archive,
        tut: "staff-nav-backup",
      },
      {
        href: "/staff/system",
        label: t("nav.system"),
        icon: ServerCog,
        tut: "staff-nav-system",
      },
      {
        href: "/staff/tools",
        label: t("nav.tools"),
        icon: Wrench,
        tut: "staff-nav-tools",
      },
    );
  }

  return (
    <div className="w-full mx-auto max-w-6xl space-y-6 p-4 sm:p-6 pb-36 sm:pb-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold tracking-tight">{t("title")}</h1>
          <Badge
            variant="outline"
            className={cn(
              "gap-1 font-semibold text-xs py-0.5",
              isAdmin
                ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30"
                : "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30",
            )}
          >
            {isAdmin ? (
              <>
                <Crown className="h-3 w-3" />
                {t("role.admin")}
              </>
            ) : (
              <>
                <ShieldCheck className="h-3 w-3" />
                {t("role.moderator")}
              </>
            )}
          </Badge>
        </div>
      </div>

      <div className="flex overflow-x-auto border-b pb-px scrollbar-none gap-1">
        {navItems.map((item) => {
          const isActive = item.exact
            ? pathname === item.href
            : pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              data-tutorial={item.tut}
              className={cn(
                "flex min-w-fit items-center gap-2 border-b-2 px-3.5 py-2.5 text-sm font-medium transition-colors hover:text-foreground",
                isActive
                  ? "border-primary text-foreground font-semibold"
                  : "border-transparent text-muted-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </div>

      <div className="animate-in fade-in duration-300">{children}</div>
      <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
    </div>
  );
}
