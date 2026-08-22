"use client";

import Link from "next/link";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import { ArrowUpRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  getNotificationCategory,
  getNotificationStyle,
  NotificationIcon,
  type NotificationItem,
} from "@/lib/notifications";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

interface NotificationItemCardProps {
  notification: NotificationItem;
  isSelected?: boolean;
  onToggleSelect?: (id: string) => void;
  onMarkRead: (notification: NotificationItem) => void;
  selectable?: boolean;
}

export function NotificationItemCard({
  notification,
  isSelected = false,
  onToggleSelect,
  onMarkRead,
  selectable = false,
}: NotificationItemCardProps) {
  const t = useTranslations("Notifications");
  const styles = getNotificationStyle(notification.type, notification.read);
  const category = getNotificationCategory(notification.type);

  const getCategoryLabel = () => {
    switch (category) {
      case "pr":
        return t("categoryPR");
      case "comment":
        return t("categoryComments");
      case "moderation":
        return t("categoryModeration");
      case "system":
        return t("categorySystem");
      default:
        return t("categoryAll");
    }
  };

  const createdDate = new Date(notification.created_at);
  const formattedDistance = formatDistanceToNow(createdDate, { addSuffix: true });
  const formattedFullDate = createdDate.toLocaleString();

  const handleCardClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (
      target.closest("button") ||
      target.closest("a") ||
      target.closest("[data-slot='checkbox']")
    ) {
      return;
    }

    if ((selectable || isSelected) && onToggleSelect) {
      onToggleSelect(notification.id);
      return;
    }

    if (!notification.read) {
      onMarkRead(notification);
    }
  };

  return (
    <div
      onClick={handleCardClick}
      className={cn(
        "group relative flex items-center justify-between gap-3 sm:gap-4 rounded-xl border p-3 sm:px-4 sm:py-3 transition-all duration-150 cursor-pointer select-none",
        notification.read
          ? "bg-card/40 border-border/60 text-muted-foreground hover:bg-card/70 hover:border-border"
          : "bg-card border-border shadow-xs hover:border-primary/40 hover:bg-accent/40",
        isSelected && "ring-2 ring-primary bg-primary/5 border-primary/40",
      )}
    >
      {/* Selection Checkbox (visible in selection mode or on hover) */}
      <div
        className={cn(
          "shrink-0 flex items-center transition-opacity",
          selectable || isSelected
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
        )}
        onClick={(e) => {
          e.stopPropagation();
          onToggleSelect?.(notification.id);
        }}
      >
        <Checkbox checked={isSelected} aria-label={`Select ${notification.title}`} />
      </div>

      {/* Category Icon */}
      <div
        className={cn(
          "flex h-8 w-8 sm:h-9 sm:w-9 shrink-0 items-center justify-center rounded-full transition-transform group-hover:scale-105",
          styles.bg,
        )}
      >
        <NotificationIcon type={notification.type} className="h-4 w-4" />
      </div>

      {/* Content Area */}
      <div className="flex-1 min-w-0 space-y-0.5">
        {/* Title & Unread indicator */}
        <div className="flex items-center gap-2">
          {notification.link ? (
            <Link
              href={notification.link}
              onClick={(e) => {
                e.stopPropagation();
                if (!notification.read) {
                  onMarkRead(notification);
                }
              }}
              className={cn(
                "text-sm font-medium hover:underline leading-snug truncate",
                notification.read
                  ? "text-muted-foreground"
                  : "text-foreground font-semibold",
              )}
            >
              {notification.title}
            </Link>
          ) : (
            <span
              className={cn(
                "text-sm leading-snug truncate",
                notification.read
                  ? "text-muted-foreground"
                  : "text-foreground font-semibold",
              )}
            >
              {notification.title}
            </span>
          )}

          {!notification.read && (
            <span
              className="h-2 w-2 shrink-0 rounded-full bg-primary"
              title={t("filterUnread")}
            />
          )}
        </div>

        {/* Body preview if present */}
        {notification.body && (
          <p className="text-xs text-muted-foreground line-clamp-1 leading-relaxed">
            {notification.body}
          </p>
        )}

        {/* Metadata: Category & Timestamp */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground pt-0.5">
          <span className="inline-flex items-center rounded-md bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {getCategoryLabel()}
          </span>
          <span className="text-[11px] text-muted-foreground/80" title={formattedFullDate}>
            {formattedDistance}
          </span>
        </div>
      </div>

      {/* Right Actions */}
      <div className="flex items-center gap-1 shrink-0">
        {notification.link && (
          <Link
            href={notification.link}
            onClick={(e) => {
              e.stopPropagation();
              if (!notification.read) {
                onMarkRead(notification);
              }
            }}
            className="hidden sm:inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/80 transition-colors"
            title={t("viewDetails")}
          >
            <span>{t("viewDetails")}</span>
            <ArrowUpRight className="h-3 w-3" />
          </Link>
        )}

        {!notification.read && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={(e) => {
              e.stopPropagation();
              onMarkRead(notification);
            }}
            title={t("markRead")}
            aria-label={t("markRead")}
            className="h-7 w-7 sm:h-8 sm:w-8 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}
