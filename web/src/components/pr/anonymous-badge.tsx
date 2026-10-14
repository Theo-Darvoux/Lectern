"use client";

import { EyeOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Marks an anonymous contribution whose author the viewer (a moderator) can
 * still see. Regular viewers never receive the author, so they get the plain
 * "Anonymous" label instead.
 */
export function AnonymousBadge({ className }: { className?: string }) {
  const t = useTranslations("PRs");
  return (
    <Badge
      variant="outline"
      title={t("anonymousModeratorHint")}
      className={cn("gap-1 px-1.5 py-0 text-[10px] font-normal", className)}
    >
      <EyeOff className="h-3 w-3" />
      {t("anonymous")}
    </Badge>
  );
}
