"use client";

import * as React from "react";
import { Search } from "lucide-react";
import { useTranslations } from "next-intl";

import { useUIStore } from "@/lib/stores";
import { cn } from "@/lib/utils";

export function SearchInline({ className }: { className?: string } = {}) {
  const t = useTranslations("Search");
  const setSearchOpen = useUIStore((state) => state.setSearchOpen);
  const [shortcut, setShortcut] = React.useState("Ctrl K");

  React.useEffect(() => {
    if (/Mac|iPhone|iPad|iPod/i.test(navigator.platform)) setShortcut("⌘ K");
  }, []);

  return (
    <button
      type="button"
      aria-label={t("searchMaterialsDirs")}
      aria-keyshortcuts="Control+K Meta+K"
      onClick={() => setSearchOpen(true)}
      className={cn(
        "group flex h-9 w-full max-w-md items-center gap-2 rounded-xl border border-white/20 bg-white/50 px-3 text-left text-sm text-muted-foreground shadow-sm backdrop-blur-md transition-all hover:bg-white/80 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 dark:border-white/10 dark:bg-black/20 dark:hover:bg-black/40",
        className,
      )}
    >
      <Search className="h-4 w-4 shrink-0 transition-colors group-hover:text-primary" />
      <span className="min-w-0 flex-1 truncate">{t("searchMaterialsDirs")}</span>
      <kbd className="hidden h-5 shrink-0 select-none items-center rounded border bg-muted/80 px-1.5 font-mono text-[10px] font-medium opacity-70 sm:flex">
        {shortcut}
      </kbd>
    </button>
  );
}
