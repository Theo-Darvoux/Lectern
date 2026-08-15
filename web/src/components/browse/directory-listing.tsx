"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTutorialMenuOpen } from "@/lib/tutorials/tutorial-store";
import { DirectoryLineItem } from "@/components/browse/directory-line-item";
import { MaterialLineItem } from "@/components/browse/material-line-item";
import { Breadcrumbs } from "@/components/browse/breadcrumbs";
import { navigateBrowse } from "@/components/browse/browse-link";
import { EmptyDirectory } from "@/components/browse/empty-directory";
import { UploadDrawer } from "@/components/pr/upload-drawer";
import { NewFolderDialog } from "@/components/pr/new-folder-dialog";
import { FileEditDialog } from "@/components/pr/file-edit-dialog";
import { DirectoryOpenPRs } from "@/components/browse/directory-open-prs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { submitDirectOperations } from "@/lib/pr-client";
import { useUIStore, useAuthStore } from "@/lib/stores";
import { isGuest } from "@/lib/guest";
import { apiFetch } from "@/lib/api-client";
import {
  Plus,
  Upload,
  FolderPlus,
  Folder,
  ArrowLeft,
  CheckSquare,
  X,
  Trash2,
  Scissors,
  ClipboardPaste,
  Loader2,
  Send,
  Info,
  ChevronDown,
  LayoutList,
  LayoutGrid,
  Download,
  FolderPen,
  MoreHorizontal,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import { useStagingStore } from "@/lib/staging-store";
import { useDropZoneStore } from "@/lib/drop-zone-store";
import { useSelectionStore } from "@/lib/selection-store";
import type { Operation } from "@/lib/staging-store";
import type { SelectedItem } from "@/lib/selection-store";
import { useTranslations } from "next-intl";
import { useAugmentedListing, stagedStatus, type NavItem } from "@/hooks/use-augmented-listing";
import { useViewMode } from "@/hooks/use-view-mode";
import { useIsMobile } from "@/hooks/use-media-query";
import { MaterialGridCard } from "@/components/browse/material-grid-card";
import { DirectoryGridCard } from "@/components/browse/directory-grid-card";
import {
  VirtualizedDirectoryGrid,
  VirtualizedDirectoryList,
} from "@/components/browse/virtualized-directory-items";

interface DirectoryListingProps {
  directory: Record<string, unknown> | null;
  directories: Record<string, unknown>[];
  materials: Record<string, unknown>[];
  breadcrumbs?: { id: string; name: string; slug: string }[];
  previewOperations?: Operation[];
  previewPrId?: string;
}

export function DirectoryListing({
  directory,
  directories,
  materials,
  breadcrumbs = [],
  previewOperations = [],
  previewPrId,
}: DirectoryListingProps) {
  const t = useTranslations("Browse");
  const tAutoTitle = useTranslations("AutoTitle");
  const tQCM = useTranslations("QCM");
  const router = useRouter();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const openSidebar = useUIStore((s) => s.openSidebar);
  const guest = isGuest(useAuthStore((s) => s.user));
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadParentMat, setUploadParentMat] = useState<{ id: string; name: string } | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const tutorialCreateOpen = useTutorialMenuOpen("create-menu");
  const [editFolderOpen, setEditFolderOpen] = useState(false);
  const requestUpload = useDropZoneStore((s) => s.requestUpload);
  const setBrowseContext = useDropZoneStore((s) => s.setBrowseContext);

  const {
    operations,
    allOps,
    realDirId,
    realDirName,
    dirId,
    dirName,
    activeGhostDir,
    ghostDirStack,
    setGhostDirStack,
    enterGhostDir,
    goBack,
    sortedDirs,
    sortedMats,
    ghostDirs,
    ghostMaterials,
    isEmpty,
    flatItems,
    allSelectableItems,
  } = useAugmentedListing({
    directory,
    directories,
    materials,
    previewOperations,
  });

  const { mode: viewMode, setMode: setViewMode } = useViewMode();

  // Index staged operations by target id once per render so each item is an
  // O(1) lookup instead of scanning allOps per row (O(items × ops)). First
  // match wins, matching the previous Array.find semantics.
  const dirOpById = useMemo(() => {
    const m = new Map<string, (typeof allOps)[number]>();
    for (const o of allOps) {
      let key: string | undefined;
      if (o.op === "edit_directory" || o.op === "delete_directory") key = o.directory_id;
      else if (o.op === "move_item" && o.target_type === "directory") key = o.target_id;
      if (key !== undefined && !m.has(key)) m.set(key, o);
    }
    return m;
  }, [allOps]);

  const matOpById = useMemo(() => {
    const m = new Map<string, (typeof allOps)[number]>();
    for (const o of allOps) {
      let key: string | undefined;
      if (o.op === "edit_material" || o.op === "delete_material") key = o.material_id;
      else if (o.op === "move_item" && o.target_type === "material") key = o.target_id;
      if (key !== undefined && !m.has(key)) m.set(key, o);
    }
    return m;
  }, [allOps]);

  const addOperations = useStagingStore((s) => s.addOperations);
  const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);

  const handleAddAttachment = useCallback((id: string, name: string) => {
    setUploadParentMat({ id, name });
    setUploadOpen(true);
  }, [setUploadOpen, setUploadParentMat]);

  const [isDownloading, setIsDownloading] = useState(false);
  const downloadCancelRef = useRef(false);

  // Dismiss any in-progress download toast and signal the loop to stop when
  // the component unmounts (e.g. the user navigates away mid-sequence).
  useEffect(() => {
    return () => {
      downloadCancelRef.current = true;
      toast.dismiss("dir-zip-download");
    };
  }, []);

  const handleDownloadZip = useCallback(async () => {
    if (isDownloading) return;
    downloadCancelRef.current = false;
    setIsDownloading(true);
    const toastId = "dir-zip-download";
    const endpoint = realDirId ? `/directories/${realDirId}/download-chunks` : `/directories/root/download-chunks`;
    try {
      const data = await apiFetch<{ dir_name: string; chunks: Array<{ url: string; filename: string }> }>(
        endpoint,
      );

      const triggerLink = (url: string) => {
        const a = document.createElement("a");
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      };

      if (data.chunks.length === 0) {
        // Worker not configured — fall back to backend streaming endpoint (root has no streaming fallback).
        if (realDirId) {
          triggerLink(`/api/directories/${realDirId}/download`);
        } else {
          toast.error(t("downloadZipTooLarge"));
        }
      } else if (data.chunks.length === 1) {
        triggerLink(data.chunks[0].url);
      } else {
        // Multiple parts: trigger sequentially so the browser registers each
        // download separately, and keep the user informed via a live toast.
        for (let i = 0; i < data.chunks.length; i++) {
          if (downloadCancelRef.current) break;
          toast.loading(
            t("downloadZipPart", { dirName: data.dir_name, part: i + 1, total: data.chunks.length }),
            { id: toastId, duration: Infinity },
          );
          triggerLink(data.chunks[i].url);
          if (i < data.chunks.length - 1) {
            await new Promise<void>((resolve) => setTimeout(resolve, 1500));
          }
        }
        if (!downloadCancelRef.current) {
          toast.success(
            t("downloadZipAllStarted", { total: data.chunks.length }),
            { id: toastId, duration: 6000 },
          );
        }
      }
    } catch {
      toast.dismiss(toastId);
      toast.error(t("downloadZipTooLarge"));
    } finally {
      setTimeout(() => setIsDownloading(false), 1500);
    }
  }, [realDirId, isDownloading, t]);

  const selectMode = useSelectionStore((s) => s.selectMode);
  const selected = useSelectionStore((s) => s.selected);
  const clipboard = useSelectionStore((s) => s.clipboard);
  const setSelectMode = useSelectionStore((s) => s.setSelectMode);
  const toggleSelect = useSelectionStore((s) => s.toggle);
  const selectAll = useSelectionStore((s) => s.selectAll);
  const deselectAll = useSelectionStore((s) => s.deselectAll);
  const cutRaw = useSelectionStore((s) => s.cut);
  const clearClipboard = useSelectionStore((s) => s.clearClipboard);

  useEffect(() => {
    setBrowseContext({ directoryId: dirId || "", directoryName: dirName });
    return () => setBrowseContext(null);
  }, [dirId, dirName, setBrowseContext]);

  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const focusedIndexRef = useRef<number | null>(null);
  useEffect(() => {
    focusedIndexRef.current = focusedIndex;
  }, [focusedIndex]);

  useEffect(() => {
    setFocusedIndex(null);
    setLastSelectedIndex(null);
  }, [directory?.id, selectMode]);

  const handleToggleItem = useCallback(
    (index: number, e?: React.MouseEvent) => {
      const item = flatItems[index];
      if (!item) return;

      const toSelectedItem = (navItem: NavItem): SelectedItem | null => {
        if (navItem.type === "dir") {
          return {
            id: String(navItem.dir.id),
            type: "directory",
            name: String(navItem.dir.name ?? ""),
            parentId: dirId || null,
          };
        }
        if (navItem.type === "mat") {
          return {
            id: String(navItem.mat.id),
            type: "material",
            name: String(navItem.mat.title ?? ""),
            parentId: dirId || null,
            material_type: String(navItem.mat.type ?? "other"),
          };
        }
        if (navItem.type === "ghost-dir") {
          return {
            id: navItem.tempId,
            type: "directory",
            name: navItem.name,
            parentId: dirId || null,
          };
        }
        if (navItem.type === "ghost-mat") {
          const op = navItem.op;
          const title = op.op === "create_material" ? op.title : op.target_title;
          const tempId = op.op === "create_material" ? op.temp_id : op.target_id;
          const mType = op.op === "create_material" ? op.type : op.target_material_type;
          return {
            id: tempId || "",
            type: "material",
            name: title || "Unnamed",
            parentId: dirId || null,
            material_type: mType || "other",
          };
        }
        return null;
      };

      const selectedItem = toSelectedItem(item);
      if (!selectedItem) return;

      if (e?.shiftKey && lastSelectedIndex !== null) {
        const start = Math.min(lastSelectedIndex, index);
        const end = Math.max(lastSelectedIndex, index);

        const itemsToSelect: SelectedItem[] = [];
        for (let i = start; i <= end; i++) {
          const found = toSelectedItem(flatItems[i]);
          if (found) itemsToSelect.push(found);
        }
        selectAll(itemsToSelect);
      } else {
        toggleSelect(selectedItem);
      }
      setLastSelectedIndex(index);
    },
    [flatItems, lastSelectedIndex, selectAll, toggleSelect, dirId],
  );

  const pathBase = pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  const buildItemPath = (slug: string) =>
    previewPrId
      ? `${pathBase}/${slug}?preview_pr=${previewPrId}`
      : `${pathBase}/${slug}`;

  useEffect(() => {
    if (focusedIndex === null) return;
    document
      .querySelector(`[data-nav-index="${focusedIndex}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedIndex]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      if ((e.target as HTMLElement).isContentEditable) return;
      if (selectMode) return;
      if (flatItems.length === 0) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIndex((prev) =>
          prev === null ? 0 : Math.min(prev + 1, flatItems.length - 1),
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIndex((prev) =>
          prev === null ? flatItems.length - 1 : Math.max(prev - 1, 0),
        );
      } else if (e.key === "Enter") {
        const idx = focusedIndexRef.current;
        if (idx === null) return;
        const item = flatItems[idx];
        if (!item) return;
        e.preventDefault();
        if (item.type === "dir") {
          navigateBrowse(buildItemPath(String(item.dir.slug ?? "")));
        } else if (item.type === "ghost-dir") {
          enterGhostDir(item.tempId, item.name);
        } else if (item.type === "mat") {
          navigateBrowse(buildItemPath(String(item.mat.slug ?? "")));
        } else if (item.type === "ghost-mat") {
          const op = item.op;
          if (op.isExternal && previewPrId && op._previewIdx !== undefined) {
            router.push(
              `/pull-requests/${previewPrId}/preview/${op._previewIdx}`,
            );
          } else {
            setReviewOpen(true);
          }
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
     
  }, [flatItems, selectMode, router, pathBase, previewPrId, enterGhostDir, setReviewOpen, buildItemPath, setFocusedIndex]);

  const selectedCount = selected.size;
  const allSelected =
    allSelectableItems.length > 0 &&
    allSelectableItems.every((item) => selected.has(item.id));

  const [batchDeleteOps, setBatchDeleteOps] = useState<Operation[] | null>(null);
  const [batchPasteOps, setBatchPasteOps] = useState<Operation[] | null>(null);
  const [submittingBatch, setSubmittingBatch] = useState(false);

  const handleBatchDelete = () => {
    const ops: Operation[] = [];
    for (const item of selected.values()) {
      const existing = stagedStatus(operations, item.id, item.type);
      if (existing === "deleted") {
        continue;
      }
      if (item.type === "directory") {
        ops.push({ op: "delete_directory", directory_id: item.id });
      } else {
        ops.push({ op: "delete_material", material_id: item.id });
      }
    }
    if (ops.length === 0) {
      toast.info(t("allSelectedAlreadyStagedForDeletion"));
      setSelectMode(false);
      return;
    }
    setBatchDeleteOps(ops);
  };

  const handleCut = () => {
    let hasConflict = false;
    for (const item of selected.values()) {
      if (stagedStatus(operations, item.id, item.type) === "deleted") {
        hasConflict = true;
        break;
      }
    }
    if (hasConflict) {
      toast.error(t("someSelectedAlreadyStagedForDeletion"));
      return;
    }
    cutRaw();
    toast.success(t("itemsCut", { count: selected.size }));
  };

  const ancestorIds = new Set([dirId, ...breadcrumbs.map((b) => b.id)]);

  const handlePaste = () => {
    const targetParent = dirId || null;
    const safe = clipboard.filter((item) => {
      if (item.type === "directory" && ancestorIds.has(item.id)) return false;
      if (item.parentId === targetParent) return false;
      return true;
    });

    if (safe.length === 0) {
      const allSameParent = clipboard.every((i) => i.parentId === targetParent);
      toast.error(
        allSameParent
          ? t("itemsAlreadyInFolder")
          : t("cannotMoveFolderIntoItself"),
      );
      return;
    }

    const ops: Operation[] = safe.map((item) => ({
      op: "move_item" as const,
      target_type: item.type,
      target_id: item.id,
      new_parent_id: targetParent,
      ...(item.type === "directory"
        ? { target_name: item.name }
        : {
            target_title: item.name,
            target_material_type: item.material_type || "other",
          }),
    }));
    setBatchPasteOps(ops);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex-1 space-y-1">
          {!activeGhostDir && (
            <div className="flex items-center gap-2 group/header">
              <Breadcrumbs items={breadcrumbs} previewPrId={previewPrId} large />
              {directory && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 rounded-full text-muted-foreground hover:bg-muted opacity-0 group-hover/header:opacity-100 transition-opacity"
                  onClick={() => openSidebar("details", { type: "directory", id: String(directory.id), data: directory })}
                  title={t("directoryDetails")}
                >
                  <Info className="h-4 w-4" />
                </Button>
              )}
            </div>
          )}

          {activeGhostDir && (
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={goBack}
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <div className="flex items-center gap-1.5 min-w-0">
                <button
                  className="text-sm text-muted-foreground hover:text-foreground transition-colors truncate"
                  onClick={() => setGhostDirStack([])}
                >
                  {realDirName}
                </button>
                {ghostDirStack.map((entry, i) => (
                  <span
                    key={entry.tempId}
                    className="flex items-center gap-1.5 min-w-0"
                  >
                    <span className="text-muted-foreground">/</span>
                    {i < ghostDirStack.length - 1 ? (
                      <button
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors truncate"
                        onClick={() =>
                          setGhostDirStack(ghostDirStack.slice(0, i + 1))
                        }
                      >
                        {entry.name}
                      </button>
                    ) : (
                      <span className="text-sm font-medium text-green-700 dark:text-green-400 truncate">
                        {entry.name}
                      </span>
                    )}
                  </span>
                ))}
                <Badge
                  variant="outline"
                  className="ml-1 text-[10px] text-green-600 border-green-300 shrink-0"
                >
                  {t("staged")}
                </Badge>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-2">
          {!selectMode ? (
            <div className="flex items-center justify-between h-11">
              <div className="flex items-center gap-2">
                <span className="hidden text-sm text-muted-foreground sm:inline">
                  {t("itemsCount", { count: allSelectableItems.length })}
                </span>
                <div data-tutorial="view-mode" className="flex items-center border rounded-md overflow-hidden h-8">
                  <button
                    onClick={() => setViewMode("list")}
                    className={`px-2 h-full flex items-center transition-colors ${viewMode === "list" ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-muted/50"}`}
                    title={t("listView")}
                    aria-label={t("listView")}
                  >
                    <LayoutList className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setViewMode("grid")}
                    className={`px-2 h-full flex items-center border-l transition-colors ${viewMode === "grid" ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-muted/50"}`}
                    title={t("gridView")}
                    aria-label={t("gridView")}
                  >
                    <LayoutGrid className="h-4 w-4" />
                  </button>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={t("moreActions")}>
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-52">
                    {!guest && allSelectableItems.length > 0 && (
                      <DropdownMenuItem onClick={() => setSelectMode(true)}>
                        <CheckSquare className="mr-2 h-4 w-4" />
                        {t("select")}
                      </DropdownMenuItem>
                    )}
                    {!activeGhostDir && (
                      <DropdownMenuItem onClick={handleDownloadZip} disabled={isDownloading}>
                        {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
                        {t("downloadZipTooltip")}
                      </DropdownMenuItem>
                    )}
                    {!guest && !activeGhostDir && !previewPrId && directory && (
                      <DropdownMenuItem onClick={() => setEditFolderOpen(true)}>
                        <FolderPen className="mr-2 h-4 w-4" />
                        {t("editFolder")}
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              <div className="flex items-center gap-2">
                {clipboard.length > 0 && (
                  <div className="flex items-center gap-1 group">
                    <Button
                      key="paste-btn"
                      size="sm"
                      variant="outline"
                      className="gap-2 border-amber-300 text-amber-700 bg-amber-50/50 hover:bg-amber-100 dark:border-amber-700/50 dark:text-amber-400 dark:bg-amber-950/20 dark:hover:bg-amber-900/30"
                      onClick={handlePaste}
                    >
                      <ClipboardPaste className="w-4 h-4" />
                      {t("paste")}({clipboard.length})
                    </Button>
                    <Button
                      key="cancel-paste-btn"
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      onClick={clearClipboard}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                )}

                {!guest && (
                <DropdownMenu
                  open={tutorialCreateOpen || createMenuOpen}
                  onOpenChange={(o) => {
                    if (!tutorialCreateOpen) setCreateMenuOpen(o);
                  }}
                >
                  <DropdownMenuTrigger asChild>
                    <Button
                      key="create-btn"
                      size="sm"
                      data-tutorial="create-menu"
                      className="gap-1.5"
                    >
                      <Plus className="w-4 h-4" />
                      <span className="hidden sm:inline">{tQCM("newContent")}</span>
                      <ChevronDown className="w-3 h-3 sm:ml-0.5 opacity-60" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="end"
                    className={`w-48 ${tutorialCreateOpen ? "z-[1001] pointer-events-none" : ""}`}
                    onInteractOutside={(e) => {
                      if (tutorialCreateOpen) e.preventDefault();
                    }}
                  >
                    <DropdownMenuItem onClick={() => setNewFolderOpen(true)}>
                      <FolderPlus className="w-4 h-4 mr-2" />
                      {t("newFolder")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setUploadOpen(true)}>
                      <Upload className="w-4 h-4 mr-2" />
                      {tQCM("importFile")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        const params = new URLSearchParams();
                        if (dirId) params.set("directoryId", dirId);
                        router.push(`/qcm/new?${params.toString()}`);
                      }}
                    >
                      <LayoutList className="w-4 h-4 mr-2" />
                      {tQCM("createQCM")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                )}
            </div>
          </div>
        ) : (
          <div className="flex min-h-11 flex-wrap items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 animate-in fade-in slide-in-from-top-1 duration-200 dark:bg-primary/10 sm:gap-3 sm:px-4">
            <div
              className="h-8 gap-2 text-muted-foreground hover:text-foreground flex items-center cursor-pointer transition-colors rounded-md hover:bg-accent/50"
              onClick={() => {
                if (allSelected) deselectAll(allSelectableItems.map((i) => i.id));
                else selectAll(allSelectableItems);
              }}
            >
              <Checkbox
                checked={allSelected}
                className="shrink-0"
                onCheckedChange={(checked) => {
                   if (checked === "indeterminate") return;
                   if (checked) selectAll(allSelectableItems);
                   else deselectAll(allSelectableItems.map((i) => i.id));
                }}
              />
              <span className="text-xs font-semibold uppercase tracking-wider select-none">
                {allSelected ? t("deselectAll") : t("selectAll")}
              </span>
            </div>
            <span className="text-sm font-medium flex-1">
              {selectedCount > 0 && t("selectedItemsCount", { count: selectedCount })}
            </span>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 text-amber-700 border-amber-300 bg-amber-50/50 hover:bg-amber-50 dark:text-amber-400 dark:border-amber-700/50 dark:hover:bg-amber-950/30 disabled:opacity-100 disabled:bg-muted/40 disabled:text-muted-foreground/60 disabled:border-border/50 disabled:grayscale"
                disabled={selectedCount === 0}
                onClick={handleCut}
              >
                <Scissors className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{t("cut")}</span>
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 text-destructive border-destructive/30 bg-destructive/5 hover:bg-destructive/10 disabled:opacity-100 disabled:bg-muted/40 disabled:text-muted-foreground/60 disabled:border-border/50 disabled:grayscale"
                disabled={selectedCount === 0}
                onClick={handleBatchDelete}
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{t("delete")}</span>
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 gap-1 px-2 text-foreground font-medium hover:bg-zinc-200 dark:hover:bg-zinc-800 transition-colors"
                onClick={() => setSelectMode(false)}
              >
                <X className="w-4 h-4" />
                <span className="hidden sm:inline">{t("exit")}</span>
              </Button>
            </div>
          </div>
        )}
      </div>

      {!activeGhostDir && (
        <DirectoryOpenPRs directoryId={realDirId || "root"} />
      )}

      {activeGhostDir && isEmpty && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-green-300 bg-green-50/30 dark:bg-green-950/10 py-12 px-4 text-center">
          <Folder className="h-10 w-10 text-green-400" />
          <div>
            <p className="text-sm font-medium text-green-700 dark:text-green-400">
              {t("stagedFolderTitle")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("stagedFolderDesc")}
            </p>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <Button
              size="sm"
              variant="outline"
              className="gap-2"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="w-4 h-4" />
              {t("uploadFiles")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="gap-2"
              onClick={() => setNewFolderOpen(true)}
            >
              <FolderPlus className="w-4 h-4" />
              {t("newFolder")}
            </Button>
          </div>
        </div>
      )}

      {!activeGhostDir && isEmpty ? (
        <EmptyDirectory />
      ) : (
        !isEmpty && (
          viewMode === "list" ? (
            <VirtualizedDirectoryList focusedIndex={focusedIndex} className={`rounded-lg border ${selectMode ? "select-none" : ""}`}>
              {sortedDirs.map((dir, i) => {
                const id = String(dir.id);
                const op = dirOpById.get(id);

                const staged = op
                  ? op.op === "delete_directory"
                    ? "deleted"
                    : op.op === "edit_directory"
                      ? "edited"
                      : "moved"
                  : null;

                let displayDir = dir;
                if (op?.op === "edit_directory") {
                  displayDir = {
                    ...dir,
                    ...(op.name != null ? { name: op.name } : {}),
                    ...(op.type != null ? { type: op.type } : {}),
                    ...(op.description != null ? { description: op.description } : {}),
                    ...(op.tags != null ? { tags: op.tags } : {}),
                  };
                }

                return (
                  <DirectoryLineItem
                    key={id}
                    directory={displayDir}
                    staged={staged}
                    selectMode={selectMode}
                    selected={selected.has(id)}
                    onToggleSelect={handleToggleItem}
                    previewPrId={previewPrId}
                    navIndex={i}
                    focused={focusedIndex === i}
                    pathBase={pathBase}
                    isMobile={isMobile}
                  />
                );
              })}

              {ghostDirs.map((op, i) => {
                const tempId =
                  (op.op === "create_directory" ? op.temp_id : op.target_id) ||
                  `ghost-${i}`;
                const isExternal = op.isExternal;
                const name = op.op === "create_directory" ? op.name : op.target_name;
                const ghostDirNavIndex = sortedDirs.length + i;
                const ghostDir = {
                  id: tempId,
                  name: name || "Unnamed",
                  child_directory_count: allOps.filter(o => o.op === "create_directory" && o.parent_id === tempId).length,
                  child_material_count: allOps.filter(o => o.op === "create_material" && o.directory_id === tempId).length,
                };

                return (
                  <DirectoryLineItem
                    key={`ghost-dir-${tempId}`}
                    directory={ghostDir}
                    staged="created"
                    isExternal={isExternal}
                    selectMode={selectMode}
                    selected={selected.has(tempId)}
                    onToggleSelect={handleToggleItem}
                    navIndex={ghostDirNavIndex}
                    focused={focusedIndex === ghostDirNavIndex}
                    onNavigate={() => enterGhostDir(tempId, name || "Unnamed")}
                    pathBase={pathBase}
                    isMobile={isMobile}
                  />
                );
              })}

              {sortedMats.map((mat, i) => {
                const id = String(mat.id);
                const op = matOpById.get(id);

                const staged = op
                  ? op.op === "delete_material"
                    ? "deleted"
                    : op.op === "edit_material"
                      ? "edited"
                      : "moved"
                  : null;

                const previewOpIndex =
                  op?.isExternal && op.op === "edit_material" ? op._previewIdx : undefined;

                let displayMat = mat;
                if (op?.op === "edit_material") {
                  displayMat = {
                    ...mat,
                    ...(op.title != null ? { title: op.title } : {}),
                    ...(op.type != null ? { type: op.type } : {}),
                    ...(op.description != null ? { description: op.description } : {}),
                    ...(op.tags != null ? { tags: op.tags } : {}),
                  };
                }

                const matNavIndex = sortedDirs.length + ghostDirs.length + i;
                return (
                  <MaterialLineItem
                    key={id}
                    material={displayMat}
                    staged={staged}
                    previewOpIndex={previewOpIndex}
                    selectMode={selectMode}
                    selected={selected.has(id)}
                    onToggleSelect={handleToggleItem}
                    previewPrId={previewPrId}
                    navIndex={matNavIndex}
                    focused={focusedIndex === matNavIndex}
                    onAddAttachment={handleAddAttachment}
                    pathBase={pathBase}
                    isMobile={isMobile}
                  />
                );
              })}

              {ghostMaterials.map((op, i) => {
                const isExternal = op.isExternal;
                const title = op.op === "create_material" ? op.title : op.target_title;
                const tempId = op.op === "create_material" ? op.temp_id : op.target_id;
                const draftAttachmentCount =
                  op.op === "create_material" && op.temp_id
                    ? allOps.filter(
                        (o) => o.op === "create_material" && o.parent_material_id === op.temp_id,
                      ).length
                    : 0;
                const ghostMatNavIndex =
                  sortedDirs.length + ghostDirs.length + sortedMats.length + i;
                const ghostMat = {
                  id: tempId || `ghost-mat-${i}`,
                  title: title || "Unnamed",
                  type: op.op === "create_material" ? op.type : op.target_material_type,
                  current_version_info: op.op === "create_material" ? { file_name: op.file_name } : undefined,
                };

                return (
                  <MaterialLineItem
                    key={`ghost-mat-${tempId ?? i}`}
                    material={ghostMat}
                    staged="created"
                    isExternal={isExternal}
                    selectMode={selectMode}
                    selected={selected.has(tempId || "")}
                    onToggleSelect={handleToggleItem}
                    navIndex={ghostMatNavIndex}
                    focused={focusedIndex === ghostMatNavIndex}
                    previewOpIndex={op._previewIdx}
                    onNavigate={() => {
                      if (isExternal) {
                        if (previewPrId && op._previewIdx !== undefined) {
                          router.push(`/pull-requests/${previewPrId}/preview/${op._previewIdx}`);
                        }
                      } else {
                        if (
                          op.op === "create_material" &&
                          op.metadata?.qcm_draft &&
                          op._storeIndex !== undefined
                        ) {
                          router.push(`/qcm/preview?draftIndex=${op._storeIndex}`);
                        } else {
                          setReviewOpen(true);
                        }
                      }
                    }}
                    onAddAttachment={handleAddAttachment}
                    draftAttachmentCount={draftAttachmentCount}
                    pathBase={pathBase}
                    isMobile={isMobile}
                  />
                );
              })}
            </VirtualizedDirectoryList>
          ) : (
            /* ── Grid view ──────────────────────────────────────────────────── */
            <VirtualizedDirectoryGrid focusedIndex={focusedIndex} className={`grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3 ${selectMode ? "select-none" : ""}`}>
              {sortedDirs.map((dir, i) => {
                const id = String(dir.id);
                const op = dirOpById.get(id);

                const staged = op
                  ? op.op === "delete_directory"
                    ? "deleted"
                    : op.op === "edit_directory"
                      ? "edited"
                      : "moved"
                  : null;

                let displayDir = dir;
                if (op?.op === "edit_directory") {
                  displayDir = {
                    ...dir,
                    ...(op.name != null ? { name: op.name } : {}),
                    ...(op.type != null ? { type: op.type } : {}),
                    ...(op.description != null ? { description: op.description } : {}),
                    ...(op.tags != null ? { tags: op.tags } : {}),
                  };
                }

                return (
                  <DirectoryGridCard
                    key={id}
                    directory={displayDir}
                    staged={staged}
                    selectMode={selectMode}
                    selected={selected.has(id)}
                    onToggleSelect={handleToggleItem}
                    previewPrId={previewPrId}
                    navIndex={i}
                    focused={focusedIndex === i}
                    pathBase={pathBase}
                  />
                );
              })}

              {ghostDirs.map((op, i) => {
                const tempId =
                  (op.op === "create_directory" ? op.temp_id : op.target_id) ||
                  `ghost-${i}`;
                const isExternal = op.isExternal;
                const name = op.op === "create_directory" ? op.name : op.target_name;
                const ghostDirNavIndex = sortedDirs.length + i;
                const ghostDir = {
                  id: tempId,
                  name: name || "Unnamed",
                  child_directory_count: allOps.filter(o => o.op === "create_directory" && o.parent_id === tempId).length,
                  child_material_count: allOps.filter(o => o.op === "create_material" && o.directory_id === tempId).length,
                };

                return (
                  <DirectoryGridCard
                    key={`ghost-dir-${tempId}`}
                    directory={ghostDir}
                    staged="created"
                    isExternal={isExternal}
                    selectMode={selectMode}
                    selected={selected.has(tempId)}
                    onToggleSelect={handleToggleItem}
                    navIndex={ghostDirNavIndex}
                    focused={focusedIndex === ghostDirNavIndex}
                    onNavigate={() => enterGhostDir(tempId, name || "Unnamed")}
                    pathBase={pathBase}
                  />
                );
              })}

              {sortedMats.map((mat, i) => {
                const id = String(mat.id);
                const op = matOpById.get(id);

                const staged = op
                  ? op.op === "delete_material"
                    ? "deleted"
                    : op.op === "edit_material"
                      ? "edited"
                      : "moved"
                  : null;

                const previewOpIndex =
                  op?.isExternal && op.op === "edit_material" ? op._previewIdx : undefined;

                let displayMat = mat;
                if (op?.op === "edit_material") {
                  displayMat = {
                    ...mat,
                    ...(op.title != null ? { title: op.title } : {}),
                    ...(op.type != null ? { type: op.type } : {}),
                    ...(op.description != null ? { description: op.description } : {}),
                    ...(op.tags != null ? { tags: op.tags } : {}),
                  };
                }

                const matNavIndex = sortedDirs.length + ghostDirs.length + i;
                return (
                  <MaterialGridCard
                    key={id}
                    material={displayMat}
                    staged={staged}
                    previewOpIndex={previewOpIndex}
                    selectMode={selectMode}
                    selected={selected.has(id)}
                    onToggleSelect={handleToggleItem}
                    previewPrId={previewPrId}
                    navIndex={matNavIndex}
                    focused={focusedIndex === matNavIndex}
                    onAddAttachment={handleAddAttachment}
                    pathBase={pathBase}
                  />
                );
              })}

              {ghostMaterials.map((op, i) => {
                const isExternal = op.isExternal;
                const title = op.op === "create_material" ? op.title : op.target_title;
                const tempId = op.op === "create_material" ? op.temp_id : op.target_id;
                const ghostFileKey = op.op === "create_material" ? (op.file_key ?? null) : null;
                const ghostFileMimeType = op.op === "create_material" ? (op.file_mime_type ?? null) : null;
                const draftAttachmentCount =
                  op.op === "create_material" && op.temp_id
                    ? allOps.filter(
                        (o) => o.op === "create_material" && o.parent_material_id === op.temp_id,
                      ).length
                    : 0;
                const ghostMatNavIndex =
                  sortedDirs.length + ghostDirs.length + sortedMats.length + i;
                const ghostMat = {
                  id: tempId || `ghost-mat-${i}`,
                  title: title || "Unnamed",
                  type: op.op === "create_material" ? op.type : op.target_material_type,
                  current_version_info:
                    op.op === "create_material"
                      ? { file_name: op.file_name, file_mime_type: op.file_mime_type }
                      : undefined,
                };

                return (
                  <MaterialGridCard
                    key={`ghost-mat-${tempId ?? i}`}
                    material={ghostMat}
                    staged="created"
                    isExternal={isExternal}
                    selectMode={selectMode}
                    selected={selected.has(tempId || "")}
                    onToggleSelect={handleToggleItem}
                    navIndex={ghostMatNavIndex}
                    focused={focusedIndex === ghostMatNavIndex}
                    previewOpIndex={op._previewIdx}
                    ghostFileKey={ghostFileKey}
                    ghostFileMimeType={ghostFileMimeType}
                    onNavigate={() => {
                      if (isExternal) {
                        if (previewPrId && op._previewIdx !== undefined) {
                          router.push(`/pull-requests/${previewPrId}/preview/${op._previewIdx}`);
                        }
                      } else {
                        if (
                          op.op === "create_material" &&
                          op.metadata?.qcm_draft &&
                          op._storeIndex !== undefined
                        ) {
                          router.push(`/qcm/preview?draftIndex=${op._storeIndex}`);
                        } else {
                          setReviewOpen(true);
                        }
                      }
                    }}
                    onAddAttachment={handleAddAttachment}
                    draftAttachmentCount={draftAttachmentCount}
                    pathBase={pathBase}
                  />
                );
              })}
            </VirtualizedDirectoryGrid>
          )
        )
      )}

      <UploadDrawer
        open={uploadOpen}
        onOpenChange={(open) => {
          setUploadOpen(open);
          if (!open) setUploadParentMat(null);
        }}
        directoryId={dirId}
        directoryName={uploadParentMat ? uploadParentMat.name : dirName}
        parentMaterialId={uploadParentMat?.id}
      />

      <NewFolderDialog
        open={newFolderOpen}
        onOpenChange={setNewFolderOpen}
        parentId={dirId || null}
        parentName={dirName}
      />
      {directory && editFolderOpen && (
        <FileEditDialog
          open={editFolderOpen}
          onOpenChange={setEditFolderOpen}
          target={{
            type: "directory",
            id: String(directory.id),
            data: directory,
          }}
        />
      )}
      <Dialog
        open={batchDeleteOps !== null}
        onOpenChange={(open) => !open && setBatchDeleteOps(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <Trash2 className="h-5 w-5" />
              {t("deleteItemsTitle", { count: batchDeleteOps?.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>
              {t("deleteItemsConfirm")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0 mt-4">
            <Button
              variant="ghost"
              onClick={() => setBatchDeleteOps(null)}
              disabled={submittingBatch}
              className="sm:mr-auto"
            >
              {t("cancel")}
            </Button>
            <Button
              variant="outline"
              disabled={submittingBatch}
              onClick={() => {
                if (batchDeleteOps) {
                  addOperations(batchDeleteOps);
                  toast.success(t("itemsAddedToDraft", { count: batchDeleteOps.length }));
                  setBatchDeleteOps(null);
                  setSelectMode(false);
                }
              }}
              className="gap-2 border-dashed border-destructive/50 text-destructive hover:bg-destructive/10"
            >
              <Plus className="h-4 w-4" /> {t("draft")}
            </Button>
            <Button
              variant="destructive"
              disabled={submittingBatch}
              onClick={async () => {
                if (batchDeleteOps) {
                  setSubmittingBatch(true);
                  const result = await submitDirectOperations(batchDeleteOps, undefined, undefined, tAutoTitle);
                  setSubmittingBatch(false);
                  if (!result) return;
                  setBatchDeleteOps(null);
                  setSelectMode(false);
                }
              }}
              className="gap-2"
            >
              {submittingBatch ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}{" "}
              {t("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={batchPasteOps !== null}
        onOpenChange={(open) => !open && setBatchPasteOps(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-amber-600">
              <ClipboardPaste className="h-5 w-5" />
              {t("moveItemsTitle", { count: batchPasteOps?.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>
              {t("moveItemsConfirm")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0 mt-4">
            <Button
              variant="ghost"
              onClick={() => setBatchPasteOps(null)}
              disabled={submittingBatch}
              className="sm:mr-auto"
            >
              {t("cancel")}
            </Button>
            <Button
              variant="outline"
              disabled={submittingBatch}
              onClick={() => {
                if (batchPasteOps) {
                  addOperations(batchPasteOps);
                  toast.success(t("itemsAddedToDraft", { count: batchPasteOps.length }));
                  setBatchPasteOps(null);
                  clearClipboard();
                }
              }}
              className="gap-2 border-dashed border-primary/50 text-primary hover:bg-primary/5"
            >
              <Plus className="h-4 w-4" /> {t("draft")}
            </Button>
            {!dirId?.startsWith("$") && (
              <Button
                disabled={submittingBatch}
                onClick={async () => {
                  if (batchPasteOps) {
                    setSubmittingBatch(true);
                    const result = await submitDirectOperations(batchPasteOps, undefined, undefined, tAutoTitle);
                    setSubmittingBatch(false);
                    if (!result) return;
                    setBatchPasteOps(null);
                    clearClipboard();
                  }
                }}
                className="gap-2"
              >
                {submittingBatch ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}{" "}
                {t("directMove")}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
