"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import {
  LayoutDashboard,
  Flag,
  FolderTree,
  GitPullRequest,
  Star,
} from "lucide-react";
import { useTranslations } from "next-intl";

export default function ModeratorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("Moderator");
  const { user, isAuthenticated } = useAuth();
  const pathname = usePathname();

  if (!isAuthenticated) return null;
  if (
    user?.role !== "moderator" &&
    user?.role !== "bureau" &&
    user?.role !== "vieux"
  ) {
    return (
      <div className="flex items-center justify-center p-12 text-muted-foreground">
        {t("noPermission")}
      </div>
    );
  }

  const navItems = [
    { href: "/moderator", label: t("nav.dashboard"), icon: LayoutDashboard, tut: "mod-nav-dashboard" },
    { href: "/moderator/flags", label: t("nav.flags"), icon: Flag, tut: "mod-nav-flags" },
    { href: "/moderator/directories", label: t("nav.directories"), icon: FolderTree, tut: "mod-nav-directories" },
    {
      href: "/moderator/pull-requests",
      label: t("nav.contributions"),
      icon: GitPullRequest,
      tut: "mod-nav-prs",
    },
    { href: "/moderator/featured", label: t("nav.featured"), icon: Star, tut: "mod-nav-featured" },
  ];

  return (
    <div className="w-full mx-auto max-w-6xl space-y-6 p-4 sm:p-6 pb-20 sm:pb-6">
      <h1 className="text-3xl font-bold">{t("title")}</h1>
      <div className="flex overflow-x-auto border-b pb-px">
        {navItems.map((item) => {
          const isActive =
            item.href === "/moderator"
              ? pathname === "/moderator"
              : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              data-tutorial={item.tut}
              className={`flex min-w-fit items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors hover:text-foreground ${
                isActive
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </div>
      <main>{children}</main>
    </div>
  );
}
