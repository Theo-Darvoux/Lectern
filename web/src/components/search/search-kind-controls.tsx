"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SearchKind } from "./use-search";

export type SearchKindFilter = SearchKind | "all";

export function SearchKindControls({
  value,
  onValueChange,
  className,
}: {
  value: SearchKindFilter;
  onValueChange: (value: SearchKindFilter) => void;
  className?: string;
}) {
  const t = useTranslations("Search");

  return (
    <div
      className={cn("flex items-center gap-1", className)}
      role="group"
      aria-label={t("filterByKind")}
    >
      {(["all", "material", "directory"] as const).map((kind) => (
        <Button
          key={kind}
          type="button"
          size="xs"
          variant={value === kind ? "secondary" : "ghost"}
          aria-pressed={value === kind}
          onClick={() => onValueChange(kind)}
        >
          {t(`kinds.${kind}`)}
        </Button>
      ))}
    </div>
  );
}
