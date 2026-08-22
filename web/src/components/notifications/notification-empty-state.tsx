"use client";

import { Bell, RotateCcw, SearchX, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";

interface NotificationEmptyStateProps {
  mode: "empty" | "all_caught_up" | "no_filter_match";
  onClearFilters?: () => void;
}

export function NotificationEmptyState({
  mode,
  onClearFilters,
}: NotificationEmptyStateProps) {
  const t = useTranslations("Notifications");

  if (mode === "all_caught_up") {
    return (
      <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed bg-card/50 px-6 py-16 text-center shadow-xs">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-500/20 mb-4">
          <Sparkles className="h-8 w-8" />
        </div>
        <h3 className="text-lg font-bold tracking-tight text-foreground">
          {t("allCaughtUpTitle")}
        </h3>
        <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
          {t("noUnreadNotifications")}
        </p>
      </div>
    );
  }

  if (mode === "no_filter_match") {
    return (
      <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed bg-card/50 px-6 py-16 text-center shadow-xs">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground ring-1 ring-border mb-4">
          <SearchX className="h-8 w-8" />
        </div>
        <h3 className="text-lg font-bold tracking-tight text-foreground">
          {t("noFilteredResults")}
        </h3>
        <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
          {t("noFilteredResultsDesc")}
        </p>
        {onClearFilters && (
          <Button
            variant="outline"
            size="sm"
            onClick={onClearFilters}
            className="mt-5 gap-2 rounded-xl"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {t("clearFilters")}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed bg-card/50 px-6 py-16 text-center shadow-xs">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/20 mb-4">
        <Bell className="h-8 w-8" />
      </div>
      <h3 className="text-lg font-bold tracking-tight text-foreground">
        {t("emptyInboxTitle")}
      </h3>
      <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
        {t("noNotifications")}
      </p>
    </div>
  );
}
