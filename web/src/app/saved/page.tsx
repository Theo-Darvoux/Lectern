"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bookmark,
  FileText,
  Folder,
  FolderHeart,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  StarOff,
  Trash2,
  X,
} from "lucide-react";
import { useSavedTranslations } from "@/lib/saved-i18n";
import { toast } from "sonner";

import { CollectionPicker } from "@/components/saved/collection-picker";
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { apiFetch } from "@/lib/api-client";
import {
  createCollection,
  deleteCollection,
  fetchCollection,
  fetchCollections,
  fetchSavedLibrary,
  removeCollectionItem,
  renameCollection,
  type CollectionDetail,
  type CollectionSummary,
  type SavedItem,
  type SavedLibrary,
} from "@/lib/collections";
import { cn } from "@/lib/utils";

type NameDialogMode = "create" | "rename" | null;

function SavedItemRow({
  item,
  collectionId,
  onRemoved,
  onCollectionsChanged,
}: {
  item: SavedItem;
  collectionId: string | null;
  onRemoved: () => void;
  onCollectionsChanged: () => void;
}) {
  const t = useSavedTranslations();
  const [removing, setRemoving] = useState(false);
  const isDirectory = item.target_type === "directory";
  const Icon = isDirectory ? Folder : FileText;

  const remove = async () => {
    if (removing) return;
    setRemoving(true);
    try {
      if (collectionId) {
        await removeCollectionItem(collectionId, item.target_type, item.target_id);
      } else {
        const endpoint =
          item.target_type === "material"
            ? `/materials/${item.target_id}/favourite`
            : `/directories/${item.target_id}/favourite`;
        await apiFetch(endpoint, { method: "POST" });
      }
      onRemoved();
    } catch {
      toast.error(collectionId ? t("errors.removeFromCollection") : t("errors.removeSaved"));
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className="group flex flex-wrap items-center gap-3 rounded-xl border bg-card p-3 transition-colors hover:bg-muted/20">
      <Link
        href={item.href}
        className="flex min-w-0 flex-1 items-center gap-3 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold group-hover:underline">{item.title}</p>
            <Badge variant="secondary" className="shrink-0 text-[10px] capitalize">
              {item.item_type}
            </Badge>
          </div>
          {item.description && (
            <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{item.description}</p>
          )}
        </div>
      </Link>

      <div className="order-3 w-full shrink-0 sm:order-none sm:w-44">
        <CollectionPicker
          targetType={item.target_type}
          targetId={item.target_id}
          onChanged={onCollectionsChanged}
        />
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0 text-muted-foreground hover:text-destructive"
        disabled={removing}
        onClick={() => void remove()}
        title={collectionId ? t("removeFromCollection") : t("removeSaved")}
        aria-label={collectionId ? t("removeFromCollection") : t("removeSaved")}
      >
        {removing ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : collectionId ? (
          <X className="h-4 w-4" />
        ) : (
          <StarOff className="h-4 w-4" />
        )}
      </Button>
    </div>
  );
}

export default function SavedPage() {
  const t = useSavedTranslations();
  const { show } = useConfirmDialog();
  const [library, setLibrary] = useState<SavedLibrary | null>(null);
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [nameDialog, setNameDialog] = useState<NameDialogMode>(null);
  const [name, setName] = useState("");
  const [savingName, setSavingName] = useState(false);

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
  }, [loadDetail, selectedId]);

  const selected = useMemo(
    () => collections.find((collection) => collection.id === selectedId) ?? null,
    [collections, selectedId],
  );
  const items = selectedId ? detail?.items ?? [] : library?.items ?? [];

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

  return (
    <div className="mx-auto w-full max-w-6xl p-4 pb-24 sm:p-6 sm:pb-8">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Bookmark className="h-6 w-6" />
          {t("title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>
      </div>

      <div className="grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="space-y-2">
          <button
            type="button"
            onClick={() => setSelectedId(null)}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm transition-colors",
              selectedId === null ? "bg-primary/10 font-semibold text-primary" : "hover:bg-muted",
            )}
          >
            <Bookmark className="h-4 w-4" />
            <span className="min-w-0 flex-1 truncate">{t("allSaved")}</span>
            <span className="text-xs tabular-nums text-muted-foreground">{library?.items.length ?? 0}</span>
          </button>

          <div className="flex items-center justify-between px-2 pt-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("collections")}
            </p>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={openCreate}>
              <Plus className="h-4 w-4" />
              <span className="sr-only">{t("createCollection")}</span>
            </Button>
          </div>

          {collections.map((collection) => (
            <button
              key={collection.id}
              type="button"
              onClick={() => setSelectedId(collection.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm transition-colors",
                selectedId === collection.id ? "bg-primary/10 font-semibold text-primary" : "hover:bg-muted",
              )}
            >
              <FolderHeart className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{collection.name}</span>
              <span className="text-xs tabular-nums text-muted-foreground">{collection.item_count}</span>
            </button>
          ))}

          {collections.length === 0 && !loading && (
            <button
              type="button"
              onClick={openCreate}
              className="w-full rounded-lg border border-dashed p-3 text-left text-xs text-muted-foreground hover:bg-muted/40"
            >
              {t("createFirstCollection")}
            </button>
          )}
        </aside>

        <main className="min-w-0">
          <div className="mb-4 flex min-h-10 items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold">{selected?.name ?? t("allSaved")}</h2>
              <p className="text-xs text-muted-foreground">
                {t("itemCount", { count: selectedId ? detail?.item_count ?? selected?.item_count ?? 0 : library?.items.length ?? 0 })}
              </p>
            </div>
            {selected && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-8 w-8">
                    <MoreHorizontal className="h-4 w-4" />
                    <span className="sr-only">{t("collectionActions")}</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={openRename}>
                    <Pencil className="mr-2 h-4 w-4" />
                    {t("renameCollection")}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={confirmDelete} className="text-destructive focus:text-destructive">
                    <Trash2 className="mr-2 h-4 w-4" />
                    {t("deleteCollection")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>

          {(loading || detailLoading) && items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-dashed p-12 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("loading")}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-12 text-center">
              {selectedId ? <FolderHeart className="h-8 w-8 text-muted-foreground/40" /> : <Bookmark className="h-8 w-8 text-muted-foreground/40" />}
              <p className="mt-3 text-sm font-medium">
                {selectedId ? t("emptyCollection") : t("emptySaved")}
              </p>
              <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                {selectedId ? t("emptyCollectionDescription") : t("emptySavedDescription")}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {items.map((item) => (
                <SavedItemRow
                  key={`${item.target_type}-${item.target_id}`}
                  item={item}
                  collectionId={selectedId}
                  onRemoved={() => removeVisibleItem(item)}
                  onCollectionsChanged={collectionsChanged}
                />
              ))}
            </div>
          )}
        </main>
      </div>

      <Dialog open={nameDialog !== null} onOpenChange={(open) => !open && setNameDialog(null)}>
        <DialogContent className="sm:max-w-md">
          <form onSubmit={submitName}>
            <DialogHeader>
              <DialogTitle>{nameDialog === "rename" ? t("renameCollection") : t("createCollection")}</DialogTitle>
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
    </div>
  );
}
