"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  FileTextIcon,
  FolderIcon,
  Loader2,
  SearchIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { ContentStatusBadge } from "@/components/content-status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { getFileBadgeColor, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { EXT_ICONS, TYPE_ICONS } from "@/lib/material-icons";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/lib/stores";
import {
  getSearchErrorMessageKey,
  useSearch,
  type SearchResult,
  type SearchStatus,
} from "./use-search";
import { SearchKindControls, type SearchKindFilter } from "./search-kind-controls";

export function SearchResultRow({ result }: { result: SearchResult }) {
  const t = useTranslations("Search");
  const isDirectory = result.search_type === "directory";
  const title = result.title || result.name || result.file_name || t("untitled");
  const extension = getFileExtension(result.file_name || title);
  let Icon: React.ElementType = isDirectory ? FolderIcon : FileTextIcon;
  if (!isDirectory) {
    if (result.type && TYPE_ICONS[result.type]) Icon = TYPE_ICONS[result.type];
    else if (extension && EXT_ICONS[extension]) Icon = EXT_ICONS[extension];
  }
  const fileBadge = isDirectory
    ? null
    : getFileBadgeLabel(result.file_name || title, result.file_mime_type);
  const location = result.ancestor_path?.trim() || t("libraryRoot");

  return (
    <>
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-lg",
          isDirectory
            ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
            : "bg-blue-500/10 text-blue-600 dark:text-blue-400",
        )}
      >
        <Icon className="size-4" />
      </div>
      <div className="min-w-0 flex-1 py-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium">{title}</span>
          <Badge variant="outline" className="h-4 shrink-0 px-1.5 text-[10px] font-medium">
            {isDirectory ? t("folder") : fileBadge || t("material")}
          </Badge>
          {result.status && result.status !== "current" && (
            <ContentStatusBadge status={result.status} interactive={false} />
          )}
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {t("inLocation", { location })}
        </div>
        {result.match_context && result.matched_field && (
          <div className="mt-1 truncate text-xs text-muted-foreground/90">
            <span className="font-medium text-foreground/70">
              {t(`matchedFields.${result.matched_field}`)}:
            </span>{" "}
            {result.match_context}
          </div>
        )}
      </div>
    </>
  );
}

export function SearchList({
  query,
  onSelect,
  status,
  error,
  retry,
  results,
  total,
}: {
  query: string;
  onSelect: (result: SearchResult) => void;
  status: SearchStatus;
  error: Error | null;
  retry: () => void;
  results: SearchResult[];
  total: number;
}) {
  const t = useTranslations("Search");
  const waiting = status === "debouncing" || status === "loading";

  return (
    <CommandList aria-busy={waiting} aria-label={t("results")}>
      <CommandEmpty>
        {waiting ? (
          <div className="flex items-center justify-center gap-2 p-4" role="status">
            <Loader2 className="size-4 animate-spin" />
            <span>{t("searching")}</span>
          </div>
        ) : status === "error" ? (
          <div className="flex flex-col items-center gap-3 px-5 py-2" role="alert">
            <AlertCircle className="size-5 text-destructive" />
            <div>
              <p className="font-medium text-foreground">{t("searchUnavailable")}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t(getSearchErrorMessageKey(error))}
              </p>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={retry}>
              {t("retry")}
            </Button>
          </div>
        ) : query.trim() === "" ? (
          <div className="px-6">
            <SearchIcon className="mx-auto mb-2 size-5 opacity-50" />
            <p>{t("startTyping")}</p>
            <p className="mt-1 text-xs text-muted-foreground">{t("searchHint")}</p>
          </div>
        ) : (
          <div className="px-6">
            <p className="font-medium text-foreground">{t("noResults")}</p>
            <p className="mt-1 text-xs text-muted-foreground">{t("noResultsHint")}</p>
          </div>
        )}
      </CommandEmpty>

      {results.length > 0 && (
        <CommandGroup heading={t("resultCount", { count: total })}>
          {results.map((result) => (
            <CommandItem
              key={`${result.search_type}-${result.id}`}
              value={`${result.title || result.name || ""} ${result.id}`}
              onSelect={() => onSelect(result)}
              className="cursor-pointer items-start gap-3 py-2.5"
            >
              <SearchResultRow result={result} />
            </CommandItem>
          ))}
        </CommandGroup>
      )}
    </CommandList>
  );
}

export function SearchModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("Search");
  const router = useRouter();
  const pathname = usePathname();
  const openLink = useExternalLinkStore((state) => state.openLink);
  const sidebarTarget = useUIStore((state) => state.sidebarTarget);
  const [query, setQuery] = React.useState("");
  const [kind, setKind] = React.useState<SearchKindFilter>("all");
  const [scope, setScope] = React.useState<"everywhere" | "current">("everywhere");
  const currentDirectory =
    pathname.startsWith("/browse") &&
    sidebarTarget?.type === "directory" &&
    sidebarTarget.id !== "root"
      ? sidebarTarget
      : null;
  const { results, total, status, error, retry } = useSearch(query, {
    kind: kind === "all" ? undefined : kind,
    limit: 10,
    directoryId: scope === "current" ? currentDirectory?.id : undefined,
    recursive: scope === "current",
  });

  React.useEffect(() => {
    if (!open) {
      setQuery("");
      setKind("all");
      setScope("everywhere");
    }
  }, [open]);

  const closeAndNavigate = React.useCallback(
    (path: string) => {
      onOpenChange(false);
      router.push(path);
    },
    [onOpenChange, router],
  );

  const onSelect = (result: SearchResult) => {
    const targetUrl = String(result.url || result.metadata?.url || "").trim();
    if (result.type === "link" && targetUrl) {
      onOpenChange(false);
      openLink(targetUrl, (path) => router.push(path));
      return;
    }
    closeAndNavigate(
      result.browse_path ||
        (result.search_type === "directory"
          ? `/directories/${result.id}`
          : `/materials/${result.id}`),
    );
  };

  const viewAll = () => {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (kind !== "all") params.set("kind", kind);
    if (scope === "current" && currentDirectory) {
      params.set("directory_id", currentDirectory.id);
      params.set("directory_name", String(currentDirectory.data.name || ""));
      params.set("recursive", "true");
    }
    closeAndNavigate(`/search?${params.toString()}`);
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      shouldFilter={false}
      title={t("dialogTitle")}
      description={t("dialogDescription")}
      className="sm:max-w-2xl"
    >
      <CommandInput
        placeholder={t("searchMaterialsDirs")}
        value={query}
        onValueChange={setQuery}
        maxLength={200}
      />
      <div className="flex items-center gap-1 border-b px-3 py-2">
        <SearchKindControls value={kind} onValueChange={setKind} />
        {currentDirectory && (
          <div
            className="ml-auto flex items-center gap-1 border-l pl-2"
            role="group"
            aria-label={t("filterByScope")}
          >
            <Button
              type="button"
              size="xs"
              variant={scope === "everywhere" ? "secondary" : "ghost"}
              aria-pressed={scope === "everywhere"}
              onClick={() => setScope("everywhere")}
            >
              {t("scopes.everywhere")}
            </Button>
            <Button
              type="button"
              size="xs"
              variant={scope === "current" ? "secondary" : "ghost"}
              aria-pressed={scope === "current"}
              onClick={() => setScope("current")}
              title={String(currentDirectory.data.name || "")}
            >
              {t("scopes.current")}
            </Button>
          </div>
        )}
      </div>
      <SearchList
        query={query}
        onSelect={onSelect}
        status={status}
        error={error}
        retry={retry}
        results={results}
        total={total}
      />
      <div className="border-t p-2">
        <Button type="button" variant="ghost" size="sm" className="w-full" onClick={viewAll}>
          {t("openFullSearch")}
          <ArrowRight className="size-4" />
        </Button>
      </div>
      <div className="hidden items-center justify-between border-t px-4 py-2 text-[11px] text-muted-foreground sm:flex">
        <span>{t("keyboardNavigate")}</span>
        <span>{t("keyboardOpen")}</span>
      </div>
    </CommandDialog>
  );
}
