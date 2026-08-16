"use client";


import { OperationalCommandCenter } from "@/components/admin/operational-command-center";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import {
  Activity,
  Archive,
  Users,
  AlertTriangle,
  Settings,
  BookOpenCheck,
  ServerCog,
} from "lucide-react";
import {useTranslations, useLocale} from "next-intl";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("Admin.Layout");
  const locale = useLocale();
  const fr = locale.toLowerCase().startsWith("fr");
  const { user, isAuthenticated } = useAuth();
  const pathname = usePathname();

  if (!isAuthenticated) return null;
  if (user?.role !== "bureau" && user?.role !== "vieux") {
    return (
      <div className="flex items-center justify-center p-12 text-muted-foreground">
        {t("noPermission")}
      </div>
    );
  }

  const navItems = [
    { href: "/admin", label: fr ? "Opérations" : "Operations", icon: Activity },
    { href: "/admin/content", label: fr ? "Contenu" : "Content", icon: BookOpenCheck },
    { href: "/admin/users", label: t("nav.users"), icon: Users },
    { href: "/admin/dlq", label: t("nav.dlq"), icon: AlertTriangle },
    { href: "/admin/config", label: t("nav.config"), icon: Settings },
    { href: "/admin/backup", label: t("nav.backup"), icon: Archive },
    { href: "/admin/system", label: fr ? "Système" : "System", icon: ServerCog },
  ];

  return (
    <div className="w-full mx-auto max-w-6xl space-y-6 p-4 sm:p-6 pb-20 sm:pb-6">
      <h1 className="text-3xl font-bold">{t("title")}</h1>
      <div className="flex overflow-x-auto border-b pb-px">
        {navItems.map((item) => {
          const isActive =
            item.href === "/admin"
              ? pathname === "/admin"
              : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
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
      <main className="animate-in fade-in slide-in-from-bottom-2 duration-500">
        {pathname === "/admin" ? <OperationalCommandCenter /> : children}
      </main>
    </div>
  );
}
