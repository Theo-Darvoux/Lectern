"use client";

import { useState } from "react";
import { FolderPlus, StarOff, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { countFailures, removeCollectionItem, setFavourite, type SavedItem } from "@/lib/collections";
import { SavedBatchDialog } from "@/components/saved/saved-batch-dialog";
import { useTranslations } from "next-intl";

interface SavedBatchBarProps {
  selectedItems: SavedItem[];
  collectionId: string | null;
  collectionName?: string;
  onClearSelection: () => void;
  onActionComplete: () => void;
}

export function SavedBatchBar({
  selectedItems,
  collectionId,
  collectionName,
  onClearSelection,
  onActionComplete,
}: SavedBatchBarProps) {
  const t = useTranslations("Saved");
  const { show } = useConfirmDialog();
  const [batchDialogOpen, setBatchDialogOpen] = useState(false);
  const [operating, setOperating] = useState(false);

  const selectedCount = selectedItems.length;
  if (selectedCount === 0) return null;

  // Report the outcome of a batch: full success, partial failure, or total failure.
  const runBatch = async (action: (item: SavedItem) => Promise<unknown>, errorMessage: string) => {
    setOperating(true);
    try {
      const failed = await countFailures(selectedItems, action);
      const succeeded = selectedCount - failed;
      if (succeeded > 0) toast.success(t("batchSuccessRemoved", { count: succeeded }));
      if (failed === selectedCount) toast.error(errorMessage);
      else if (failed > 0) toast.error(t("batchPartialFailure", { count: failed }));
      onClearSelection();
      onActionComplete();
    } finally {
      setOperating(false);
    }
  };

  const handleBatchRemoveFromCollection = () => {
    if (!collectionId) return;
    show(
      t("removeFromCollection"),
      t("batchRemoveFromCollectionConfirm", {
        count: selectedCount,
        name: collectionName ?? t("collections"),
      }),
      () =>
        runBatch(
          (item) => removeCollectionItem(collectionId, item.target_type, item.target_id),
          t("errors.removeFromCollection"),
        ),
    );
  };

  const handleBatchRemoveFromSaved = () => {
    show(
      t("removeSaved"),
      t("batchRemoveFromSavedConfirm", { count: selectedCount }),
      () =>
        runBatch(
          (item) => setFavourite(item.target_type, item.target_id, false),
          t("errors.removeSaved"),
        ),
    );
  };

  return (
    <>
      <div className="fixed bottom-20 sm:bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 rounded-2xl border bg-card/95 px-4 py-2.5 shadow-2xl backdrop-blur-md animate-in fade-in slide-in-from-bottom-4 duration-200">
        <div className="flex items-center gap-2 pr-2 border-r">
          <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-primary px-2 text-xs font-bold text-primary-foreground">
            {selectedCount}
          </span>
          <span className="hidden sm:inline text-xs font-medium text-muted-foreground">
            {t("selectedCount", { count: selectedCount })}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-xs font-medium"
            onClick={() => setBatchDialogOpen(true)}
            disabled={operating}
          >
            <FolderPlus className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{t("batchAddToCollection")}</span>
            <span className="sm:hidden">{t("addToCollection")}</span>
          </Button>

          {collectionId ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
              onClick={handleBatchRemoveFromCollection}
              disabled={operating}
            >
              <X className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{t("batchRemoveFromCollection")}</span>
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
              onClick={handleBatchRemoveFromSaved}
              disabled={operating}
            >
              <StarOff className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{t("batchRemoveFromSaved")}</span>
            </Button>
          )}

          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground hover:text-foreground"
            onClick={onClearSelection}
            disabled={operating}
            title={t("deselectAll")}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">{t("deselectAll")}</span>
          </Button>
        </div>
      </div>

      <SavedBatchDialog
        open={batchDialogOpen}
        onOpenChange={setBatchDialogOpen}
        selectedItems={selectedItems}
        onSuccess={() => {
          onClearSelection();
          onActionComplete();
        }}
      />
    </>
  );
}
