"use client";

import { ClipboardList, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useStagingStore, isExpired } from "@/lib/staging-store";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";

export function StagingFab() {
  const t = useTranslations("Staging");
  const operations = useStagingStore((s) => s.operations) ?? [];
  const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
  const count = operations.length;
  const expiredCount = operations.filter((s) => isExpired(s)).length;
  const pathname = usePathname();

  // Hide on QCM editor pages — the editor has its own fixed toolbar that conflicts.
  const isQcmEditor = /^\/qcm\/(new|[^/]+\/edit)/.test(pathname);

  if (count === 0 || isQcmEditor) return null;

  return (
    <Button
      onClick={() => setReviewOpen(true)}
      size="lg"
      className={cn(
        "fixed bottom-[4.5rem] right-4 md:bottom-6 md:right-6 z-50 gap-2 rounded-full shadow-lg",
        "h-14 px-5 text-base",
        "animate-in fade-in slide-in-from-bottom-4 duration-300",
        expiredCount > 0 && "bg-red-600 hover:bg-red-700",
      )}
    >
      {expiredCount > 0 ? (
        <AlertTriangle className="h-5 w-5" />
      ) : (
        <ClipboardList className="h-5 w-5" />
      )}
      <span>{expiredCount > 0 ? t("fabExpired") : t("fabDraft")}</span>
      <Badge
        variant="secondary"
        className="ml-1 h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-xs font-bold"
      >
        {count}
      </Badge>
    </Button>
  );
}
