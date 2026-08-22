"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, FolderPlus, Loader2, Plus, Settings2 } from "lucide-react";
import { useSavedTranslations } from "@/lib/saved-i18n";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  addCollectionItem,
  createCollection,
  fetchCollections,
  removeCollectionItem,
  type CollectionSummary,
  type SavedTargetType,
} from "@/lib/collections";
import { cn } from "@/lib/utils";

interface CollectionPickerProps {
  targetType: SavedTargetType;
  targetId: string;
  disabled?: boolean;
  className?: string;
  onChanged?: () => void;
}

export function CollectionPicker({
  targetType,
  targetId,
  disabled = false,
  className,
  onChanged,
}: CollectionPickerProps) {
  const t = useSavedTranslations();
  const [open, setOpen] = useState(false);
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [changingId, setChangingId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [loadedTargetKey, setLoadedTargetKey] = useState<string | null>(null);
  const loadRequestRef = useRef(0);
  const targetKey = `${targetType}:${targetId}`;
  const hasLoadedCurrent = loadedTargetKey === targetKey;
  const visibleCollections = hasLoadedCurrent ? collections : [];

  const loadCollections = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    try {
      const data = await fetchCollections({ targetType, targetId });
      if (requestId !== loadRequestRef.current) return;
      setCollections(data);
      setLoadedTargetKey(`${targetType}:${targetId}`);
    } catch {
      if (requestId !== loadRequestRef.current) return;
      toast.error(t("errors.loadCollections"));
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  }, [targetId, targetType, t]);

  useEffect(() => {
    if (open) void loadCollections();
  }, [open, loadCollections]);

  const toggle = async (collection: CollectionSummary) => {
    if (changingId) return;
    setChangingId(collection.id);
    const next = !collection.contains_target;
    setCollections((current) =>
      current.map((item) =>
        item.id === collection.id
          ? {
              ...item,
              contains_target: next,
              item_count: Math.max(0, item.item_count + (next ? 1 : -1)),
            }
          : item,
      ),
    );
    try {
      if (next) {
        await addCollectionItem(collection.id, targetType, targetId);
      } else {
        await removeCollectionItem(collection.id, targetType, targetId);
      }
      onChanged?.();
    } catch {
      await loadCollections();
      toast.error(t("errors.updateCollection"));
    } finally {
      setChangingId(null);
    }
  };

  const createAndAdd = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = newName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const created = await createCollection(name);
      await addCollectionItem(created.id, targetType, targetId);
      setCollections((current) =>
        [...current, { ...created, contains_target: true, item_count: 1 }].sort((a, b) =>
          a.name.localeCompare(b.name),
        ),
      );
      setNewName("");
      toast.success(t("collectionCreated", { name: created.name }));
      onChanged?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.createCollection"));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          className={cn("w-full justify-center gap-2", className)}
        >
          <FolderPlus className="h-4 w-4" />
          {t("addToCollection")}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2.5">
          <p className="text-sm font-semibold">{t("collectionPickerTitle")}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("collectionPickerDescription")}
          </p>
        </div>

        <div
          className={cn(
            "max-h-56 overflow-y-auto p-1.5",
            loading && hasLoadedCurrent && "opacity-60 transition-opacity",
          )}
          aria-busy={loading || !hasLoadedCurrent}
        >
          {!hasLoadedCurrent ? (
            <div className="flex items-center justify-center gap-2 p-5 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("loading")}
            </div>
          ) : visibleCollections.length === 0 ? (
            <p className="p-4 text-center text-sm text-muted-foreground">
              {t("noCollectionsYet")}
            </p>
          ) : (
            visibleCollections.map((collection) => (
              <button
                key={collection.id}
                type="button"
                disabled={changingId === collection.id}
                onClick={() => void toggle(collection)}
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted disabled:opacity-60"
              >
                <span
                  className={cn(
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                    collection.contains_target && "border-primary bg-primary text-primary-foreground",
                  )}
                >
                  {collection.contains_target && <Check className="h-3 w-3" />}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium">{collection.name}</span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {collection.item_count}
                </span>
              </button>
            ))
          )}
        </div>

        <form onSubmit={createAndAdd} className="flex gap-2 border-t p-2.5">
          <Input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder={t("collectionNamePlaceholder")}
            maxLength={80}
            className="h-8"
          />
          <Button type="submit" size="sm" className="h-8 px-2.5" disabled={!newName.trim() || creating}>
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            <span className="sr-only">{t("createCollection")}</span>
          </Button>
        </form>

        <div className="border-t p-1.5">
          <Button variant="ghost" size="sm" asChild className="w-full justify-start gap-2 text-xs">
            <Link href="/saved" onClick={() => setOpen(false)}>
              <Settings2 className="h-3.5 w-3.5" />
              {t("manageCollections")}
            </Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
