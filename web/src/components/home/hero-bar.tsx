"use client";

import Link from "next/link";
import {
  FolderTree,
  GitPullRequest,
  ListChecks,
  Bell,
} from "lucide-react";
import { SearchInline } from "@/components/search/search-inline";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

interface QuickAction {
  href: string;
  label: string;
  icon: React.ReactNode;
  className: string;
}

interface HeroBarProps {
  greeting: string;
  displayName: string;
  subtitle: string;
  isLoading?: boolean;
  showContributorActions?: boolean;
}

export function HeroBar({
  greeting,
  displayName,
  subtitle,
  isLoading = false,
  showContributorActions = true,
}: HeroBarProps) {
  const t = useTranslations("Home");

  const actions: QuickAction[] = [
    {
      href: "/browse",
      label: t("quickBrowse"),
      icon: <FolderTree className="h-4 w-4" />,
      className:
        "bg-primary/10 text-primary hover:bg-primary/15 ring-primary/20",
    },
    ...(showContributorActions
      ? [
          {
            href: "/pull-requests",
            label: t("quickPullRequests"),
            icon: <GitPullRequest className="h-4 w-4" />,
            className:
              "bg-green-500/10 text-green-600 dark:text-green-400 hover:bg-green-500/15 ring-green-500/20",
          },
          {
            href: "/qcm",
            label: t("quickQcm"),
            icon: <ListChecks className="h-4 w-4" />,
            className:
              "bg-violet-500/10 text-violet-600 dark:text-violet-400 hover:bg-violet-500/15 ring-violet-500/20",
          },
          {
            href: "/notifications",
            label: t("quickNotifications"),
            icon: <Bell className="h-4 w-4" />,
            className:
              "bg-amber-500/10 text-amber-600 dark:text-amber-400 hover:bg-amber-500/15 ring-amber-500/20",
          },
        ]
      : []),
  ];

  return (
    <div className="relative overflow-hidden rounded-2xl border bg-linear-to-br from-primary/5 via-card to-card p-5 shadow-sm sm:p-7">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        {/* Greeting */}
        <div className="min-w-0">
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-64 sm:w-80" />
              <Skeleton className="h-4 w-48" />
            </div>
          ) : (
            <>
              <h1 className="text-2xl font-bold tracking-tight wrap-break-word sm:text-3xl">
                {greeting}, {displayName}!{" "}
                <span role="img" aria-label="wave">
                  👋
                </span>
              </h1>
              <p className="mt-1 text-sm text-muted-foreground sm:text-base">
                {subtitle}
              </p>
            </>
          )}
        </div>

        {/* Search */}
        <div className="w-full lg:max-w-md lg:shrink-0">
          <SearchInline className="max-w-none" />
        </div>
      </div>

      {/* Quick actions */}
      <div className="mt-5 flex flex-wrap gap-2">
        {actions.map((a) => (
          <Link
            key={a.href}
            href={a.href}
            className={cn(
              "inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium ring-1 transition-colors focus-visible:outline-none focus-visible:ring-2",
              a.className,
            )}
          >
            {a.icon}
            {a.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
