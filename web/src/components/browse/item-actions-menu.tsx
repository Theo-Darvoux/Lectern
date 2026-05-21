"use client";

import React, { createContext, useContext, useState } from "react";
import {
  MoreVertical,
  Download,
  Edit2,
  PencilLine,
  Link as LinkIcon,
  Paperclip,
  Printer,
  Trash2,
  Plus,
  Send,
  Loader2,
  ShieldAlert,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useDownload } from "@/hooks/use-download";
import { usePrint } from "@/hooks/use-print";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { submitDirectOperations } from "@/lib/pr-client";
import { useBrowseRefreshStore } from "@/lib/stores";
import { FileEditDialog } from "@/components/pr/file-edit-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FlagButton } from "@/components/flags/flag-button";
import { getViewerType } from "@/lib/file-utils";

// ─── Types ────────────────────────────────────────────────────────────────────

export type ItemData = {
  id: string;
  type: "directory" | "material";
  data: Record<string, unknown>;
  staged?: "edited" | "deleted" | "moved" | "created" | null;
  isExternal?: boolean;
};

interface ActionsContextValue {
  item: ItemData;
  actions: ReturnType<typeof useItemActions>;
  onAddAttachment?: () => void;
  itemPath?: string;
}

interface VersionInfo {
  file_name?: string;
  file_mime_type?: string;
}

const ActionsContext = createContext<ActionsContextValue | null>(null);

// ─── Logic Hook ───────────────────────────────────────────────────────────────

function useItemActions(item: ItemData, itemPath?: string) {
  const t = useTranslations("Browse");
  const tAuto = useTranslations("AutoTitle");
  const triggerBrowseRefresh = useBrowseRefreshStore((s) => s.triggerBrowseRefresh);
  const addOperation = useStagingStore((s) => s.addOperation);
  const removeOperation = useStagingStore((s) => s.removeOperation);
  // Read operations lazily (inside handlers only) so this component doesn't
  // re-render every time any staging op is added — avoid 50× card re-renders.
  const getOperations = () => useStagingStore.getState().operations;
  const searchParams = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
  const isPreview = searchParams?.has("preview_pr");
  const isDraft = item.id.startsWith("$");
  const isRestricted = isPreview || isDraft || !!item.staged;

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const isMaterial = item.type === "material";
  const title = String(isMaterial ? (item.data.title ?? "") : (item.data.name ?? ""));

  // Refined viewerType and mimeType determination for materials
  let viewerType = String(item.data.type || "");
  let mimeType = String(item.data.mime_type || "");

  if (isMaterial) {
    // Check if we have current version info (typical for materials from API)
    const vi = item.data.current_version_info as VersionInfo | undefined;
    if (vi) {
      mimeType = String(vi.file_mime_type || mimeType);
      const fileName = String(vi.file_name || "");
      viewerType = getViewerType(mimeType, fileName);
    } else {
      // Fallback: maybe it's passed directly or it's a creation draft
      const fileName = String(item.data.file_name || "");
      if (mimeType || fileName) {
        viewerType = getViewerType(mimeType, fileName);
      }
    }
  }

  const handleDraftDelete = () => {
    // If this is already a staged creation, "deleting" it means just removing the creation op
    if (item.staged === "created" && !item.isExternal) {
      // Find index of the creation op for this temp id
      const idx = getOperations().findIndex(o => {
        const unwrapped = unwrapOp(o);
        if (item.type === "directory") {
          return unwrapped.op === "create_directory" && unwrapped.temp_id === item.id;
        } else {
          return unwrapped.op === "create_material" && unwrapped.temp_id === item.id;
        }
      });
      if (idx !== -1) {
        removeOperation(idx);
        toast.success(t("creationCancelled"));
      }
    } else {
      if (isMaterial) {
        addOperation({
          op: "delete_material",
          material_id: item.id,
        });
      } else {
        addOperation({
          op: "delete_directory",
          directory_id: item.id,
        });
      }
      toast.success(t("addedDeletionToDraft"));
    }
    setDeleteDialogOpen(false);
  };

  const handleDirectDelete = async () => {
    setDeleting(true);
    try {
      await submitDirectOperations([
        isMaterial ? {
          op: "delete_material",
          material_id: item.id,
        } : {
          op: "delete_directory",
          directory_id: item.id,
        },
      ], undefined, undefined, tAuto);
      toast.success(t("itemDeletedSuccessfully", { type: isMaterial ? t("material") : t("folder") }));
      setDeleteDialogOpen(false);
      triggerBrowseRefresh();
    } catch {
      toast.error(t("failedToDeleteItem"));
    } finally {
      setDeleting(false);
    }
  };

  const { downloadMaterial, downloadQcmAsXml, isDownloading } = useDownload();
  const { print, isPrinting, canPrint } = usePrint({
    viewerType,
    materialId: item.id,
    fileName: title,
    mimeType,
  });

  const handleShare = () => {
    const url = itemPath 
      ? `${window.location.origin}${itemPath}`
      : window.location.href;
    navigator.clipboard.writeText(url);
    toast.success(t("linkCopied"));
  };

  return {
    t,
    title,
    isMaterial,
    viewerType,
    mimeType,
    deleteDialogOpen,
    setDeleteDialogOpen,
    editDialogOpen,
    setEditDialogOpen,
    deleting,
    handleDraftDelete,
    handleDirectDelete,
    handleShare,
    downloadMaterial,
    downloadQcmAsXml,
    isDownloading,
    print,
    isPrinting,
    canPrint,
    isRestricted,
  };
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MenuItemsList({ isContextMenu = false }: { isContextMenu?: boolean }) {
  const router = useRouter();
  const context = useContext(ActionsContext);
  if (!context) return null;
  const { item, actions } = context;
  const { t } = actions;



  const Item = isContextMenu ? ContextMenuItem : DropdownMenuItem;
  const Separator = isContextMenu ? ContextMenuSeparator : DropdownMenuSeparator;
  const Label = isContextMenu ? ContextMenuLabel : DropdownMenuLabel;

  const { downloadMaterial, downloadQcmAsXml, isDownloading, print, isPrinting, canPrint } = actions;

  const isStaged = !!item.staged;
  const isCreated = item.staged === "created";

  return (
    <>
      <Label className="px-2 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
        {actions.isMaterial ? t("materialActions") : t("folderActions")}
      </Label>

      {actions.isMaterial && !actions.isRestricted && (
        <>
          <Item
            onClick={() => actions.viewerType === "qcm" ? downloadQcmAsXml(item.id) : downloadMaterial(item.id)}
            disabled={isDownloading}
            className="cursor-pointer"
          >
            {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
            <span>{t("download")}</span>
          </Item>
          {canPrint && (
            <Item
              onClick={() => print()}
              disabled={isPrinting}
              className="cursor-pointer"
            >
              {isPrinting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Printer className="mr-2 h-4 w-4" />}
              <span>{t("print")}</span>
            </Item>
          )}
          {context.onAddAttachment && (
            <Item onClick={context.onAddAttachment} className="cursor-pointer">
              <Paperclip className="mr-2 h-4 w-4" />
              <span>{t("addAttachment")}</span>
            </Item>
          )}
          <Separator />
        </>
      )}

      {!actions.isRestricted && (
        <>
          {actions.viewerType === "qcm" ? (
            <Item onClick={() => router.push(`/qcm/${item.id}/edit`)} className="cursor-pointer">
              <PencilLine className="mr-2 h-4 w-4" />
              <span>{t("edit")}</span>
            </Item>
          ) : (
            <Item onClick={() => actions.setEditDialogOpen(true)} className="cursor-pointer">
              <Edit2 className="mr-2 h-4 w-4" />
              <span>{t("edit")}</span>
            </Item>
          )}
          <Item onClick={actions.handleShare} className="cursor-pointer">
            <LinkIcon className="mr-2 h-4 w-4" />
            <span>{t("copyLink")}</span>
          </Item>
        </>
      )}

      {actions.isMaterial && !actions.isRestricted && (
        <div onClick={(e) => e.stopPropagation()}>
          <FlagButton
            targetType="material"
            targetId={item.id}
            variant="ghost"
            className="flex w-full items-center justify-start gap-2.5 px-2 py-1.5 text-sm font-normal rounded-sm hover:bg-accent transition-colors h-auto"
            iconClassName="h-4 w-4 text-muted-foreground mr-0.5"
          />
        </div>
      )}

      <Separator />

      <Item
        onClick={() => actions.setDeleteDialogOpen(true)}
        variant="destructive"
        className="cursor-pointer"
      >
        <Trash2 className="mr-2 h-4 w-4" />
        <span>{isCreated ? t("discardDraft") : t("delete")}</span>
      </Item>
    </>
  );
}

// ─── Main Components ──────────────────────────────────────────────────────────

export function ItemActionsMenu({
  item,
  children,
  onAddAttachment,
  itemPath,
}: {
  item: ItemData;
  children: React.ReactNode;
  onAddAttachment?: () => void;
  itemPath?: string;
}) {
   
  const { staged, isExternal, ...rest } = item;
  const actions = useItemActions(item, itemPath);
  const { t } = actions;

  return (
    <ActionsContext.Provider value={{ item, actions, onAddAttachment, itemPath }}>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          {children}
        </ContextMenuTrigger>
        <ContextMenuContent className="w-56">
          <MenuItemsList isContextMenu />
        </ContextMenuContent>
      </ContextMenu>

      {!item.isExternal && (
        <FileEditDialog
          open={actions.editDialogOpen}
          onOpenChange={actions.setEditDialogOpen}
          target={{ type: item.type, id: item.id, data: item.data }}
        />
      )}

      <Dialog open={actions.deleteDialogOpen} onOpenChange={actions.setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <ShieldAlert className="h-5 w-5" />
              {item.staged === "created" ? t("discardDraft") : t("deleteTitle", { type: actions.isMaterial ? t("material") : t("folder") })}
            </DialogTitle>
            <DialogDescription>
              {item.staged === "created"
                ? t("discardDraftConfirm", { type: item.type === "material" ? t("material") : t("folder") })
                : <>{t("deletePermanentlyConfirm")} <span className="font-semibold text-foreground">{actions.title}</span>?</>
              }
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0 mt-6">
            <Button variant="ghost" onClick={() => actions.setDeleteDialogOpen(false)} disabled={actions.deleting} className="sm:mr-auto">
              {t("cancel")}
            </Button>

            {item.staged !== "created" && (
              <Button
                variant="outline"
                onClick={actions.handleDraftDelete}
                disabled={actions.deleting}
                className="gap-2 border-dashed border-destructive/40 text-destructive hover:bg-destructive/5 hover:border-destructive/60"
              >
                <Plus className="h-4 w-4" />
                {t("draft")}
              </Button>
            )}

            <Button
              variant="destructive"
              onClick={item.staged === "created" ? actions.handleDraftDelete : actions.handleDirectDelete}
              disabled={actions.deleting}
              className="gap-2 shadow-sm"
            >
              {actions.deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : (item.staged === "created" ? <Trash2 className="h-4 w-4" /> : <Send className="h-4 w-4" />)}
              {item.staged === "created" ? t("discard") : t("deleteNow")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ActionsContext.Provider>
  );
}

export function ItemActionsDropdownTrigger() {
  const context = useContext(ActionsContext);
  if (!context) return null;

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 hover:bg-muted active:scale-95 transition-transform"
          >
            <MoreVertical className="h-4 w-4 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <MenuItemsList />
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
