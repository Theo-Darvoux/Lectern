"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowUpDown,
  Bookmark,
  CheckSquare,
  Compass,
  FileText,
  Filter,
  Folder,
  FolderHeart,
  FolderPlus,
  Grid2X2,
  HelpCircle,
  LayoutGrid,
  LayoutList,
  Layers,
  ListFilter,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useSavedTranslations } from "@/lib/saved-i18n";
import { toast } from "sonner";

import { SavedCard } from "@/components/saved/saved-card";
import { SavedListRow } from "@/components/saved/saved-list-row";
import { SavedBatchBar } from "@/components/saved/saved-batch-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfirmDialog } from "@/components/confirm-dialog";
import {
  createCollection,
  deleteCollection,
  fetchCollection,
  fetchCollections,
  fetchSavedLibrary,
  renameCollection,
  type CollectionDetail,
  type CollectionSummary,
  type SavedItem,
  type SavedLibrary,
} from "@/lib/collections";
import { cn } from "@/lib/utils";

type NameDialogMode = "create" | "rename" | null;
type TypeFilter = "all" | "material" | "directory" | "qcm" | "media" | "link";
type SortOption = "recent" | "oldest" | "name_asc" | "name_desc";

export default function SavedPage() {
  const t = useSavedTranslations();
  const { show } = useConfirmDialog();

  // Data state
  const [library, setLibrary] = useState<SavedLibrary | null>(null);
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  // View & Filter state
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [sortBy, setSortBy] = useState<SortOption>("recent");
  const [collectionSearch, setCollectionSearch] = useState("");

  // Multi-select state
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [selectMode, setSelectMode] = useState(false);

  // Dialog state
  const [nameDialog, setNameDialog] = useState<NameDialogMode>(null);
  const [name, setName] = useState("");
  const [savingName, setSavingName] = useState(false);

  // Load view mode preference from localStorage
  useEffect(() => {
    try {
      const stored = localStorage.getItem("saved-view-mode");
      if (stored === "grid" || stored === "list") {
        setViewMode(stored);
      }
    } catch {}
  }, []);

  const handleSetViewMode = (mode: "grid" | "list") => {
    setViewMode(mode);
    try {
      localStorage.setItem("saved-view-mode", mode);
    } catch {}
  };

  const refreshCollections = useCallback(async () => {
    const data = await fetchCollections();
    setCollections(data);
    return data;
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      const [saved, collectionList] = await Promise.all([
        fetchSavedLibrary(),
        fetchCollections(),
      ]);
      setLibrary(saved);
      setCollections(collectionList);
    } catch {
      toast.error(t("errors.load"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const loadDetail = useCallback(
    async (collectionId: string) => {
      setDetailLoading(true);
      try {
        const data = await fetchCollection(collectionId);
        setDetail(data);
      } catch {
        toast.error(t("errors.loadCollection"));
      } finally {
        setDetailLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    } else {
      setDetail(null);
    }
    // Clear item selection when switching collection
    setSelectedKeys(new Set());
  }, [loadDetail, selectedId]);

  const selected = useMemo(
    () => collections.find((collection) => collection.id === selectedId) ?? null,
    [collections, selectedId],
  );

  const rawItems = selectedId ? detail?.items ?? [] : library?.items ?? [];

  // Filtered & Sorted items
  const filteredItems = useMemo(() => {
    let result = [...rawItems];

    // 1. Text Search query
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      result = result.filter((item) => {
        const titleMatch = item.title.toLowerCase().includes(q);
        const descMatch = item.description?.toLowerCase().includes(q) ?? false;
        const typeMatch = item.item_type.toLowerCase().includes(q);
        return titleMatch || descMatch || typeMatch;
      });
    }

    // 2. Type filter
    if (typeFilter !== "all") {
      result = result.filter((item) => {
        const itemType = item.item_type.toLowerCase();
        const mime = typeof item.metadata?.mime_type === "string" ? item.metadata.mime_type.toLowerCase() : "";
        const url = typeof item.metadata?.url === "string" ? item.metadata.url : "";

        if (typeFilter === "directory") {
          return item.target_type === "directory";
        }
        if (typeFilter === "qcm") {
          return itemType === "qcm" || mime === "application/vnd.lectern.qcm+json";
        }
        if (typeFilter === "media") {
          return (
            itemType === "video" ||
            itemType === "audio" ||
            mime.startsWith("video/") ||
            mime.startsWith("image/") ||
            mime.startsWith("audio/")
          );
        }
        if (typeFilter === "link") {
          return itemType === "link" || !!url;
        }
        if (typeFilter === "material") {
          return (
            item.target_type === "material" &&
            itemType !== "qcm" &&
            itemType !== "video" &&
            itemType !== "link" &&
            !url
          );
        }
        return true;
      });
    }

    // 3. Sorting
    result.sort((a, b) => {
      if (sortBy === "recent") {
        return new Date(b.added_at).getTime() - new Date(a.added_at).getTime();
      }
      if (sortBy === "oldest") {
        return new Date(a.added_at).getTime() - new Date(b.added_at).getTime();
      }
      if (sortBy === "name_asc") {
        return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
      }
      if (sortBy === "name_desc") {
        return b.title.localeCompare(a.title, undefined, { sensitivity: "base" });
      }
      return 0;
    });

    return result;
  }, [rawItems, searchQuery, typeFilter, sortBy]);

  // Filtered collections in the sidebar
  const filteredCollections = useMemo(() => {
    const q = collectionSearch.trim().toLowerCase();
    if (!q) return collections;
    return collections.filter((c) => c.name.toLowerCase().includes(q));
  }, [collections, collectionSearch]);

  // Multi-select helpers
  const toggleItemSelect = useCallback((item: SavedItem) => {
    const key = `${item.target_type}-${item.target_id}`;
    setSelectedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const selectAll = () => {
    const next = new Set<string>();
    filteredItems.forEach((item) => {
      next.add(`${item.target_type}-${item.target_id}`);
    });
    setSelectedKeys(next);
    setSelectMode(true);
  };

  const deselectAll = () => {
    setSelectedKeys(new Set());
    setSelectMode(false);
  };

  const selectedItemsList = useMemo(() => {
    return filteredItems.filter((item) =>
      selectedKeys.has(`${item.target_type}-${item.target_id}`),
    );
  }, [filteredItems, selectedKeys]);

  // Collection CRUD handlers
  const openCreate = () => {
    setName("");
    setNameDialog("create");
  };

  const openRename = () => {
    if (!selected) return;
    setName(selected.name);
    setNameDialog("rename");
  };

  const submitName = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextName = name.trim();
    if (!nextName || savingName) return;
    setSavingName(true);
    try {
      if (nameDialog === "create") {
        const created = await createCollection(nextName);
        await refreshCollections();
        setSelectedId(created.id);
        toast.success(t("collectionCreated", { name: created.name }));
      } else if (nameDialog === "rename" && selectedId) {
        const updated = await renameCollection(selectedId, nextName);
        setCollections((current) =>
          current.map((collection) =>
            collection.id === updated.id ? { ...collection, ...updated } : collection,
          ),
        );
        setDetail((current) => (current ? { ...current, name: updated.name } : current));
        toast.success(t("collectionRenamed"));
      }
      setNameDialog(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.saveCollection"));
    } finally {
      setSavingName(false);
    }
  };

  const confirmDelete = () => {
    if (!selected) return;
    show(
      t("deleteCollectionTitle"),
      t("deleteCollectionDescription", { name: selected.name }),
      async () => {
        try {
          await deleteCollection(selected.id);
          setSelectedId(null);
          setDetail(null);
          await refreshCollections();
          toast.success(t("collectionDeleted"));
        } catch {
          toast.error(t("errors.deleteCollection"));
        }
      },
    );
  };

  const removeVisibleItem = (item: SavedItem) => {
    const key = `${item.target_type}-${item.target_id}`;
    setSelectedKeys((current) => {
      const next = new Set(current);
      next.delete(key);
      return next;
    });

    if (selectedId) {
      setDetail((current) =>
        current
          ? {
              ...current,
              item_count: Math.max(0, current.item_count - 1),
              items: current.items.filter(
                (candidate) =>
                  !(
                    candidate.target_type === item.target_type &&
                    candidate.target_id === item.target_id
                  ),
              ),
            }
          : current,
      );
      setCollections((current) =>
        current.map((collection) =>
          collection.id === selectedId
            ? { ...collection, item_count: Math.max(0, collection.item_count - 1) }
            : collection,
        ),
      );
    } else {
      setLibrary((current) =>
        current
          ? {
              items: current.items.filter(
                (candidate) =>
                  !(
                    candidate.target_type === item.target_type &&
                    candidate.target_id === item.target_id
                  ),
              ),
            }
          : current,
      );
    }
  };

  const collectionsChanged = () => {
    void refreshCollections();
    if (selectedId) void loadDetail(selectedId);
  };

  // Stats calculation
  const totalSavedCount = library?.items.length ?? 0;
  const totalCollectionsCount = collections.length;
  const totalFoldersCount =
    library?.items.filter((item) => item.target_type === "directory").length ?? 0;
  const totalMaterialsCount =
    library?.items.filter((item) => item.target_type === "material").length ?? 0;

  const isCurrentViewLoading = loading || (selectedId !== null && detailLoading);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 pb-36 sm:px-6 sm:py-8 lg:px-8">
      {/* ── Page Hero Header ────────────────────────────────────────────── */}
      <div className="mb-8 flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
        <div className="flex items-start gap-4">
          <div className="flex h-13 w-13 shrink-0 items-center justify-center rounded-2xl bg-linear-to-br from-primary/20 via-primary/10 to-muted border border-primary/20 shadow-xs">
            <Bookmark className="h-6 w-6 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight sm:text-3xl">
              {t("title")}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("description")}
            </p>
          </div>
        </div>

        {/* ── Stats Summary Pills ────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <div className="flex items-center gap-2 rounded-xl border bg-card/80 px-3.5 py-2 shadow-xs backdrop-blur-xs">
            <Bookmark className="h-4 w-4 text-primary" />
            <div className="text-xs">
              <span className="font-bold text-foreground tabular-nums">
                {totalSavedCount}
              </span>{" "}
              <span className="text-muted-foreground">{t("statsSavedCount")}</span>
            </div>
          </div>

          <div className="flex items-center gap-2 rounded-xl border bg-card/80 px-3.5 py-2 shadow-xs backdrop-blur-xs">
            <FolderHeart className="h-4 w-4 text-primary" />
            <div className="text-xs">
              <span className="font-bold text-foreground tabular-nums">
                {totalCollectionsCount}
              </span>{" "}
              <span className="text-muted-foreground">{t("statsCollectionsCount")}</span>
            </div>
          </div>

          <Button
            onClick={openCreate}
            size="sm"
            className="gap-1.5 rounded-xl shadow-xs font-semibold"
          >
            <Plus className="h-4 w-4" />
            {t("createCollection")}
          </Button>
        </div>
      </div>

      {/* ── Main Layout Grid (Sidebar + Content) ─────────────────────────── */}
      <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* ── Left Sidebar Navigation ──────────────────────────────────── */}
        <aside className="space-y-4">
          <div className="rounded-2xl border bg-card/60 p-2.5 shadow-xs backdrop-blur-xs space-y-1">
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className={cn(
                "group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-all",
                selectedId === null
                  ? "bg-primary text-primary-foreground shadow-sm font-semibold"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Bookmark className={cn("h-4 w-4 shrink-0", selectedId === null ? "text-primary-foreground" : "text-primary")} />
              <span className="min-w-0 flex-1 truncate">{t("allSaved")}</span>
              <Badge
                variant={selectedId === null ? "secondary" : "outline"}
                className={cn(
                  "shrink-0 text-xs tabular-nums font-semibold px-2 py-0.5",
                  selectedId === null
                    ? "bg-primary-foreground/20 text-primary-foreground border-transparent"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {library?.items.length ?? 0}
              </Badge>
            </button>
          </div>

          {/* Collections Section */}
          <div className="rounded-2xl border bg-card/60 p-3 shadow-xs backdrop-blur-xs space-y-2">
            <div className="flex items-center justify-between px-1">
              <div className="flex items-center gap-1.5">
                <FolderHeart className="h-4 w-4 text-primary" />
                <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  {t("collections")}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
                onClick={openCreate}
                title={t("createCollection")}
              >
                <Plus className="h-4 w-4" />
                <span className="sr-only">{t("createCollection")}</span>
              </Button>
            </div>

            {collections.length > 4 && (
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  value={collectionSearch}
                  onChange={(e) => setCollectionSearch(e.target.value)}
                  placeholder={t("searchCollections")}
                  className="h-8 pl-8 text-xs rounded-lg bg-background/50"
                />
              </div>
            )}

            <div className="max-h-96 overflow-y-auto space-y-1 pr-0.5">
              {filteredCollections.map((collection) => (
                <div
                  key={collection.id}
                  className={cn(
                    "group relative flex items-center justify-between rounded-xl px-3 py-2 text-sm transition-all",
                    selectedId === collection.id
                      ? "bg-primary/10 text-primary font-semibold border border-primary/20"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedId(collection.id)}
                    className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                  >
                    <FolderHeart
                      className={cn(
                        "h-4 w-4 shrink-0 transition-colors",
                        selectedId === collection.id ? "text-primary" : "text-muted-foreground group-hover:text-primary",
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate">{collection.name}</span>
                  </button>

                  <div className="flex items-center gap-1">
                    <span
                      className={cn(
                        "text-xs tabular-nums font-semibold px-2 py-0.5 rounded-full",
                        selectedId === collection.id
                          ? "bg-primary/20 text-primary"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {collection.item_count}
                    </span>

                    {/* Quick Rename/Delete Menu for selected collection or on hover */}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 rounded-md opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <MoreHorizontal className="h-3 w-3" />
                          <span className="sr-only">{t("collectionActions")}</span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem
                          onClick={() => {
                            setSelectedId(collection.id);
                            setName(collection.name);
                            setNameDialog("rename");
                          }}
                        >
                          <Pencil className="mr-2 h-3.5 w-3.5" />
                          {t("renameCollection")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => {
                            show(
                              t("deleteCollectionTitle"),
                              t("deleteCollectionDescription", { name: collection.name }),
                              async () => {
                                try {
                                  await deleteCollection(collection.id);
                                  if (selectedId === collection.id) {
                                    setSelectedId(null);
                                    setDetail(null);
                                  }
                                  await refreshCollections();
                                  toast.success(t("collectionDeleted"));
                                } catch {
                                  toast.error(t("errors.deleteCollection"));
                                }
                              },
                            );
                          }}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 className="mr-2 h-3.5 w-3.5" />
                          {t("deleteCollection")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              ))}

              {collections.length === 0 && !loading && (
                <button
                  type="button"
                  onClick={openCreate}
                  className="flex flex-col items-center justify-center w-full rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground hover:bg-muted/40 transition-colors"
                >
                  <FolderPlus className="h-6 w-6 text-muted-foreground/60 mb-2" />
                  <span className="font-semibold text-foreground">{t("createCollection")}</span>
                  <span className="mt-1 text-[11px] text-muted-foreground/80">
                    {t("createFirstCollection")}
                  </span>
                </button>
              )}
            </div>
          </div>
        </aside>

        {/* ── Right Main Content Area ─────────────────────────────────── */}
        <main className="min-w-0 space-y-4">
          {/* ── View Header & Actions Banner ──────────────────────────── */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b pb-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h2 className="truncate text-xl font-bold tracking-tight">
                  {selected?.name ?? t("allSaved")}
                </h2>
                {selected && (
                  <Badge variant="secondary" className="shrink-0 text-xs font-semibold">
                    {t("itemCount", {
                      count: detail?.item_count ?? selected.item_count ?? 0,
                    })}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {selected
                  ? t("collectionPickerDescription")
                  : t("itemCount", { count: library?.items.length ?? 0 })}
              </p>
            </div>

            {/* Collection Actions Menu (Rename / Delete) */}
            {selected && (
              <div className="flex items-center gap-2 shrink-0">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs rounded-lg"
                  onClick={openRename}
                >
                  <Pencil className="h-3.5 w-3.5" />
                  {t("renameCollection")}
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs text-destructive hover:bg-destructive/10 rounded-lg"
                  onClick={confirmDelete}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {t("deleteCollection")}
                </Button>
              </div>
            )}
          </div>

          {/* ── Search & Filter Controls Toolbar ──────────────────────── */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            {/* Search Input */}
            <div className="relative flex-1 min-w-0 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("searchPlaceholder")}
                className="h-9 pl-9 pr-8 text-xs rounded-xl bg-card"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Toolbar Buttons: Sort, View Switcher, Select Mode */}
            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              {/* Sort Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="h-9 gap-1.5 text-xs rounded-xl">
                    <ArrowUpDown className="h-3.5 w-3.5" />
                    <span>
                      {sortBy === "recent"
                        ? t("sortRecent")
                        : sortBy === "oldest"
                        ? t("sortOldest")
                        : sortBy === "name_asc"
                        ? t("sortNameAsc")
                        : t("sortNameDesc")}
                    </span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                  <DropdownMenuRadioGroup
                    value={sortBy}
                    onValueChange={(val) => setSortBy(val as SortOption)}
                  >
                    <DropdownMenuRadioItem value="recent" className="text-xs">
                      {t("sortRecent")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="oldest" className="text-xs">
                      {t("sortOldest")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="name_asc" className="text-xs">
                      {t("sortNameAsc")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="name_desc" className="text-xs">
                      {t("sortNameDesc")}
                    </DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>

              {/* View Switcher (Grid / List) */}
              <div className="flex items-center rounded-xl border bg-card p-0.5 shadow-xs">
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-8 w-8 rounded-lg",
                    viewMode === "grid" && "bg-muted text-foreground font-semibold shadow-xs",
                  )}
                  onClick={() => handleSetViewMode("grid")}
                  title={t("viewGrid")}
                >
                  <LayoutGrid className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-8 w-8 rounded-lg",
                    viewMode === "list" && "bg-muted text-foreground font-semibold shadow-xs",
                  )}
                  onClick={() => handleSetViewMode("list")}
                  title={t("viewList")}
                >
                  <LayoutList className="h-4 w-4" />
                </Button>
              </div>

              {/* Select All / Batch Toggle */}
              {filteredItems.length > 0 && (
                <Button
                  variant={selectedKeys.size > 0 ? "secondary" : "outline"}
                  size="sm"
                  className="h-9 gap-1.5 text-xs rounded-xl font-medium"
                  onClick={() => {
                    if (selectedKeys.size === filteredItems.length && filteredItems.length > 0) {
                      deselectAll();
                    } else {
                      selectAll();
                    }
                  }}
                >
                  {selectedKeys.size === filteredItems.length && filteredItems.length > 0 ? (
                    <>
                      <CheckSquare className="h-3.5 w-3.5 text-primary" />
                      <span>{t("deselectAll")}</span>
                    </>
                  ) : (
                    <>
                      <Square className="h-3.5 w-3.5" />
                      <span>{t("selectAll")}</span>
                    </>
                  )}
                </Button>
              )}
            </div>
          </div>

          {/* ── Type Filter Pills ─────────────────────────────────────── */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 text-xs">
            <button
              type="button"
              onClick={() => setTypeFilter("all")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "all"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterAll")}
            </button>
            <button
              type="button"
              onClick={() => setTypeFilter("material")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "material"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterMaterials")}
            </button>
            <button
              type="button"
              onClick={() => setTypeFilter("directory")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "directory"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterDirectories")}
            </button>
            <button
              type="button"
              onClick={() => setTypeFilter("qcm")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "qcm"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterQcm")}
            </button>
            <button
              type="button"
              onClick={() => setTypeFilter("media")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "media"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterMedia")}
            </button>
            <button
              type="button"
              onClick={() => setTypeFilter("link")}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors shrink-0",
                typeFilter === "link"
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {t("filterLinks")}
            </button>
          </div>

          {/* ── Content Grid / List Display ───────────────────────────── */}
          {isCurrentViewLoading && rawItems.length === 0 ? (
            /* Skeleton Loading State */
            <div
              className={cn(
                viewMode === "grid"
                  ? "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-4"
                  : "space-y-2",
              )}
            >
              {Array.from({ length: 6 }).map((_, i) =>
                viewMode === "grid" ? (
                  <div
                    key={i}
                    className="flex flex-col overflow-hidden rounded-2xl border bg-card p-0 shadow-xs"
                  >
                    <Skeleton className="aspect-[16/9] w-full" />
                    <div className="space-y-2 p-4">
                      <Skeleton className="h-4 w-3/4" />
                      <Skeleton className="h-3 w-1/2" />
                    </div>
                  </div>
                ) : (
                  <div
                    key={i}
                    className="flex items-center gap-3 rounded-xl border bg-card p-3 shadow-xs"
                  >
                    <Skeleton className="h-11 w-11 shrink-0 rounded-xl" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-4 w-1/3" />
                      <Skeleton className="h-3 w-1/4" />
                    </div>
                  </div>
                ),
              )}
            </div>
          ) : rawItems.length === 0 ? (
            /* Empty Library or Empty Collection State */
            <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed bg-card/40 p-12 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted/60 text-muted-foreground/60 mb-4">
                {selectedId ? (
                  <FolderHeart className="h-8 w-8" />
                ) : (
                  <Bookmark className="h-8 w-8" />
                )}
              </div>
              <h3 className="text-base font-bold">
                {selectedId ? t("emptyCollection") : t("emptySaved")}
              </h3>
              <p className="mt-1.5 max-w-md text-xs text-muted-foreground leading-relaxed">
                {selectedId
                  ? t("emptyCollectionDescription")
                  : t("emptySavedDescription")}
              </p>
              {!selectedId && (
                <Button asChild size="sm" className="mt-5 gap-2 rounded-xl font-semibold">
                  <Link href="/browse">
                    <Compass className="h-4 w-4" />
                    {t("browseMaterials")}
                  </Link>
                </Button>
              )}
            </div>
          ) : filteredItems.length === 0 ? (
            /* No Search / Filter Results State */
            <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed bg-card/40 p-12 text-center">
              <Search className="h-10 w-10 text-muted-foreground/40 mb-3" />
              <h3 className="text-sm font-bold">{t("noSearchResults")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("noSearchResultsDescription")}
              </p>
              <div className="mt-4 flex items-center gap-2">
                {searchQuery && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs rounded-xl"
                    onClick={() => setSearchQuery("")}
                  >
                    {t("clearSearch")}
                  </Button>
                )}
                {typeFilter !== "all" && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs rounded-xl"
                    onClick={() => setTypeFilter("all")}
                  >
                    {t("clearFilters")}
                  </Button>
                )}
              </div>
            </div>
          ) : viewMode === "grid" ? (
            /* Grid View */
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-4">
              {filteredItems.map((item) => (
                <SavedCard
                  key={`${item.target_type}-${item.target_id}`}
                  item={item}
                  collectionId={selectedId}
                  selected={selectedKeys.has(`${item.target_type}-${item.target_id}`)}
                  selectMode={selectMode || selectedKeys.size > 0}
                  onToggleSelect={toggleItemSelect}
                  onRemoved={() => removeVisibleItem(item)}
                  onCollectionsChanged={collectionsChanged}
                />
              ))}
            </div>
          ) : (
            /* List View */
            <div className="space-y-2">
              {filteredItems.map((item) => (
                <SavedListRow
                  key={`${item.target_type}-${item.target_id}`}
                  item={item}
                  collectionId={selectedId}
                  selected={selectedKeys.has(`${item.target_type}-${item.target_id}`)}
                  selectMode={selectMode || selectedKeys.size > 0}
                  onToggleSelect={toggleItemSelect}
                  onRemoved={() => removeVisibleItem(item)}
                  onCollectionsChanged={collectionsChanged}
                />
              ))}
            </div>
          )}
        </main>
      </div>

      {/* ── Floating Batch Actions Bar ──────────────────────────────────── */}
      <SavedBatchBar
        selectedItems={selectedItemsList}
        collectionId={selectedId}
        collectionName={selected?.name}
        onClearSelection={deselectAll}
        onActionComplete={refreshAll}
      />

      {/* ── Create / Rename Collection Dialog ───────────────────────────── */}
      <Dialog
        open={nameDialog !== null}
        onOpenChange={(open) => !open && setNameDialog(null)}
      >
        <DialogContent className="sm:max-w-md">
          <form onSubmit={submitName}>
            <DialogHeader>
              <DialogTitle>
                {nameDialog === "rename" ? t("renameCollection") : t("createCollection")}
              </DialogTitle>
              <DialogDescription>{t("collectionNameDescription")}</DialogDescription>
            </DialogHeader>
            <Input
              autoFocus
              className="mt-4"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("collectionNamePlaceholder")}
              maxLength={80}
            />
            <DialogFooter className="mt-5">
              <Button type="button" variant="ghost" onClick={() => setNameDialog(null)}>
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={!name.trim() || savingName}>
                {savingName && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {nameDialog === "rename" ? t("save") : t("create")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
    </div>
  );
}
