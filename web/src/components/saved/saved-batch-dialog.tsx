"use client";

import { useEffect, useRef, useState } from "react";
import { Check, FolderPlus, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  addCollectionItem,
  createCollection,
  fetchCollections,
  type CollectionSummary,
  type SavedItem,
} from "@/lib/collections";
import { useSavedTranslations } from "@/lib/saved-i18n";
import { cn } from "@/lib/utils";

interface SavedBatchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedItems: SavedItem[];
  onSuccess: () => void;
}

export function SavedBatchDialog({
  open,
  onOpenChange,
  selectedItems,
  onSuccess,
}: SavedBatchDialogProps) {
  const t = useSavedTranslations();
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectedCollectionId, setSelectedCollectionId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const loadRequestRef = useRef(0);

  useEffect(() => {
    const requestId = ++loadRequestRef.current;
    if (!open) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchCollections()
      .then((data) => {
        if (requestId !== loadRequestRef.current) return;
        setCollections(data);
        setHasLoaded(true);
        setIsCreatingNew(data.length === 0);
        if (data.length > 0) {
          setSelectedCollectionId(data[0].id);
        }
      })
      .catch(() => {
        if (requestId !== loadRequestRef.current) return;
        toast.error(t("errors.loadCollections"));
      })
      .finally(() => {
        if (requestId === loadRequestRef.current) setLoading(false);
      });
  }, [open, t]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving || selectedItems.length === 0) return;

    let targetCollectionId = selectedCollectionId;
    let targetCollectionName = "";

    setSaving(true);
    try {
      if (isCreatingNew) {
        const trimmed = newName.trim();
        if (!trimmed) return;
        const created = await createCollection(trimmed);
        targetCollectionId = created.id;
        targetCollectionName = created.name;
      } else {
        const found = collections.find((c) => c.id === targetCollectionId);
        targetCollectionName = found?.name ?? "";
      }

      if (!targetCollectionId) return;

      // Add each item in parallel
      await Promise.all(
        selectedItems.map((item) =>
          addCollectionItem(targetCollectionId!, item.target_type, item.target_id).catch(() => null),
        ),
      );

      toast.success(
        t("batchSuccessAdded", {
          count: selectedItems.length,
          name: targetCollectionName,
        }),
      );
      onSuccess();
      onOpenChange(false);
    } catch {
      toast.error(t("errors.updateCollection"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FolderPlus className="h-5 w-5 text-primary" />
              {t("batchAddToCollection")}
            </DialogTitle>
            <DialogDescription>
              {t("collectionPickerDescription")} ({selectedItems.length} items selected)
            </DialogDescription>
          </DialogHeader>

          <div
            className={cn(
              "py-4 space-y-3",
              loading && hasLoaded && "opacity-60 transition-opacity",
            )}
            aria-busy={loading}
          >
            {loading && !hasLoaded ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("loading")}
              </div>
            ) : isCreatingNew ? (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  {t("createCollection")}
                </p>
                <Input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("collectionNamePlaceholder")}
                  maxLength={80}
                />
                {collections.length > 0 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-xs"
                    onClick={() => setIsCreatingNew(false)}
                  >
                    {t("cancel")}
                  </Button>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <div className="max-h-52 overflow-y-auto space-y-1 rounded-lg border p-1.5">
                  {collections.map((collection) => (
                    <button
                      key={collection.id}
                      type="button"
                      onClick={() => setSelectedCollectionId(collection.id)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                        selectedCollectionId === collection.id && "bg-primary/10 font-semibold text-primary",
                      )}
                    >
                      <span className="truncate">{collection.name}</span>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {collection.item_count} items
                      </span>
                    </button>
                  ))}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full gap-2 text-xs"
                  onClick={() => {
                    setIsCreatingNew(true);
                    setNewName("");
                  }}
                >
                  <Plus className="h-3.5 w-3.5" />
                  {t("createCollection")}
                </Button>
              </div>
            )}
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={
                saving ||
                (isCreatingNew ? !newName.trim() : !selectedCollectionId)
              }
            >
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
