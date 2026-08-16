"use client";

import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  FileText,
  Code2,
  Info,
  MessageSquare,
  RefreshCw,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useDownload } from "@/hooks/use-download";
import { usePrint } from "@/hooks/use-print";
import { apiFetch } from "@/lib/api-client";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { submitDirectOperations } from "@/lib/pr-client";
import { useAuthStore, useBrowseRefreshStore, useUIStore } from "@/lib/stores";
import { isGuest, isStaff } from "@/lib/guest";
import { FileEditDialog } from "@/components/pr/file-edit-dialog";
import { isThumbnailEligible } from "@/lib/file-utils";
import { recalculateMaterialThumbnail } from "@/lib/material-preview-source";
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
import { cn } from "@/lib/utils";

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

// Provided only while a row is "unarmed" (its Radix menus not yet mounted), so
// the lightweight dropdown trigger can arm the row when it's interacted with.
const ArmContext = createContext<(() => void) | null>(null);

// ─── Logic Hook ───────────────────────────────────────────────────────────────

function useItemActions(item: ItemData, itemPath?: string) {
  const t = useTranslations("Browse");
  const tAuto = useTranslations("AutoTitle");
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
  let fileName = "";

  if (isMaterial) {
    // Check if we have current version info (typical for materials from API)
    const vi = item.data.current_version_info as VersionInfo | undefined;
    if (vi) {
      mimeType = String(vi.file_mime_type || mimeType);
      fileName = String(vi.file_name || "");
      viewerType = getViewerType(mimeType, fileName);
    } else {
      // Fallback: maybe it's passed directly or it's a creation draft
      fileName = String(item.data.file_name || "");
      if (mimeType || fileName) {
        viewerType = getViewerType(mimeType, fileName);
      }
    }
  }

  const [isRecalculatingThumbnail, setIsRecalculatingThumbnail] = useState(false);
  const triggerBrowseRefresh = useBrowseRefreshStore((s) => s.triggerBrowseRefresh);

  const handleRecalculateThumbnail = async () => {
    if (isRecalculatingThumbnail || !isMaterial) return;
    setIsRecalculatingThumbnail(true);
    toast.loading(t("recalculatingThumbnail"), { id: `thumb-recalc-${item.id}` });
    try {
      await recalculateMaterialThumbnail(item.id);
      toast.success(t("thumbnailRecalculated"), { id: `thumb-recalc-${item.id}` });
      triggerBrowseRefresh();
    } catch {
      toast.error(t("failedToRecalculateThumbnail"), { id: `thumb-recalc-${item.id}` });
    } finally {
      setIsRecalculatingThumbnail(false);
    }
  };

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
      const result = await submitDirectOperations([
        isMaterial ? {
          op: "delete_material",
          material_id: item.id,
        } : {
          op: "delete_directory",
          directory_id: item.id,
        },
      ], undefined, undefined, tAuto);
      if (!result) return;
      setDeleteDialogOpen(false);
    } catch {
      toast.error(t("failedToDeleteItem"));
    } finally {
      setDeleting(false);
    }
  };

  const { downloadMaterial, downloadQcmAsXml, downloadQcmAsPdf, isDownloading } = useDownload();

  const [isDirDownloading, setIsDirDownloading] = useState(false);
  const dirDownloadCancelRef = useRef(false);
  const dirToastId = `dir-zip-download-${item.id}`;

  useEffect(() => {
    return () => {
      dirDownloadCancelRef.current = true;
      toast.dismiss(dirToastId);
    };
  }, []);

  const handleDownloadDirectory = useCallback(async () => {
    if (isDirDownloading) return;
    dirDownloadCancelRef.current = false;
    setIsDirDownloading(true);
    try {
      const data = await apiFetch<{ dir_name: string; chunks: Array<{ url: string; filename: string }> }>(
        `/directories/${item.id}/download-chunks`,
      );
      const triggerLink = (url: string) => {
        const a = document.createElement("a");
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      };
      if (data.chunks.length === 0) {
        triggerLink(`/api/directories/${item.id}/download`);
      } else if (data.chunks.length === 1) {
        triggerLink(data.chunks[0].url);
      } else {
        for (let i = 0; i < data.chunks.length; i++) {
          if (dirDownloadCancelRef.current) break;
          toast.loading(
            t("downloadZipPart", { dirName: data.dir_name, part: i + 1, total: data.chunks.length }),
            { id: dirToastId, duration: Infinity },
          );
          triggerLink(data.chunks[i].url);
          if (i < data.chunks.length - 1) {
            await new Promise<void>((resolve) => setTimeout(resolve, 1500));
          }
        }
        if (!dirDownloadCancelRef.current) {
          toast.success(
            t("downloadZipAllStarted", { total: data.chunks.length }),
            { id: dirToastId, duration: 6000 },
          );
        }
      }
    } catch {
      toast.dismiss(dirToastId);
      toast.error(t("downloadZipTooLarge"));
    } finally {
      setTimeout(() => setIsDirDownloading(false), 1500);
    }
  }, [item.id, isDirDownloading, t, dirToastId]);
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
    toast.success(t("copyLink") || "Link copied");
  };

  return {
    t,
    title,
    fileName,
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
    handleRecalculateThumbnail,
    isRecalculatingThumbnail,
    downloadMaterial,
    downloadQcmAsXml,
    downloadQcmAsPdf,
    isDownloading,
    handleDownloadDirectory,
    isDirDownloading,
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
  const user = useAuthStore((s) => s.user);
  const guest = isGuest(user);
  const staff = isStaff(user);
  const openSidebar = useUIStore((s) => s.openSidebar);
  if (!context) return null;
  const { item, actions } = context;
  const { t } = actions;
  const isEligible = isThumbnailEligible(actions.mimeType, actions.fileName || actions.title);

  const Item = isContextMenu ? ContextMenuItem : DropdownMenuItem;
  const Separator = isContextMenu ? ContextMenuSeparator : DropdownMenuSeparator;
  const Label = isContextMenu ? ContextMenuLabel : DropdownMenuLabel;

  const { downloadMaterial, downloadQcmAsXml, downloadQcmAsPdf, isDownloading, print, isPrinting, canPrint } = actions;

  const isCreated = item.staged === "created";

  const handleDetailsClick = () => {
    openSidebar("details", {
      type: item.type,
      id: item.id,
      data: { ...item.data, __path: context.itemPath },
    });
  };

  const handleChatClick = () => {
    openSidebar("chat", {
      type: item.type,
      id: item.id,
      data: item.data,
    });
  };

  return (
    <>
      <Label className="px-2 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
        {actions.isMaterial ? t("materialActions") : t("folderActions")}
      </Label>

      <Item onClick={handleDetailsClick} className="cursor-pointer">
        <Info className="mr-2 h-4 w-4" />
        <span>{t("details")}</span>
      </Item>
      {!actions.isRestricted && (
        <Item onClick={handleChatClick} className="cursor-pointer">
          <MessageSquare className="mr-2 h-4 w-4" />
          <span>{t("chat")}</span>
        </Item>
      )}
      <Separator />

      {actions.isMaterial && (!actions.isRestricted || (context.onAddAttachment && !guest)) && (
        <>
          {!actions.isRestricted && (actions.viewerType === "qcm" ? (
            isContextMenu ? (
              <ContextMenuSub>
                <ContextMenuSubTrigger disabled={isDownloading} className="cursor-pointer">
                  {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
                  <span>{t("download")}</span>
                </ContextMenuSubTrigger>
                <ContextMenuSubContent>
                  <ContextMenuItem onClick={() => downloadQcmAsPdf(item.id, actions.title)} className="cursor-pointer">
                    <FileText className="mr-2 h-4 w-4" />
                    <span>{t("downloadPdf")}</span>
                  </ContextMenuItem>
                  <ContextMenuItem onClick={() => downloadQcmAsXml(item.id)} className="cursor-pointer">
                    <Code2 className="mr-2 h-4 w-4" />
                    <span>{t("downloadXml")}</span>
                  </ContextMenuItem>
                </ContextMenuSubContent>
              </ContextMenuSub>
            ) : (
              <DropdownMenuSub>
                <DropdownMenuSubTrigger disabled={isDownloading} className="cursor-pointer">
                  {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
                  <span>{t("download")}</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  <DropdownMenuItem onClick={() => downloadQcmAsPdf(item.id, actions.title)} className="cursor-pointer">
                    <FileText className="mr-2 h-4 w-4" />
                    <span>{t("downloadPdf")}</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => downloadQcmAsXml(item.id)} className="cursor-pointer">
                    <Code2 className="mr-2 h-4 w-4" />
                    <span>{t("downloadXml")}</span>
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            )
          ) : (
            <Item
              onClick={() => downloadMaterial(item.id)}
              disabled={isDownloading}
              className="cursor-pointer"
            >
              {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
              <span>{t("download")}</span>
            </Item>
          ))}
          {!actions.isRestricted && canPrint && (
            <Item
              onClick={() => print()}
              disabled={isPrinting}
              className="cursor-pointer"
            >
              {isPrinting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Printer className="mr-2 h-4 w-4" />}
              <span>{t("print")}</span>
            </Item>
          )}
          {context.onAddAttachment && !guest && (
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
          {!guest && actions.isMaterial &&
            (actions.viewerType === "qcm" ? (
              <Item onClick={() => router.push(`/qcm/${item.id}/edit`)} className="cursor-pointer">
                <PencilLine className="mr-2 h-4 w-4" />
                <span>{t("edit")}</span>
              </Item>
            ) : (
              <Item onClick={() => actions.setEditDialogOpen(true)} className="cursor-pointer">
                <Edit2 className="mr-2 h-4 w-4" />
                <span>{t("edit")}</span>
              </Item>
            ))}
          {!guest && !actions.isMaterial && (
            <Item onClick={() => actions.setEditDialogOpen(true)} className="cursor-pointer">
              <Edit2 className="mr-2 h-4 w-4" />
              <span>{t("edit")}</span>
            </Item>
          )}
          <Item onClick={actions.handleShare} className="cursor-pointer">
            <LinkIcon className="mr-2 h-4 w-4" />
            <span>{t("copyLink")}</span>
          </Item>
          {actions.isMaterial && staff && isEligible && (
            <Item
              onClick={actions.handleRecalculateThumbnail}
              disabled={actions.isRecalculatingThumbnail}
              className="cursor-pointer"
            >
              {actions.isRecalculatingThumbnail ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              <span>{t("recalculateThumbnail")}</span>
            </Item>
          )}
          {!actions.isMaterial && (
            <Item
              onClick={actions.handleDownloadDirectory}
              disabled={actions.isDirDownloading}
              className="cursor-pointer"
            >
              {actions.isDirDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
              <span>{t("downloadZip")}</span>
            </Item>
          )}
        </>
      )}

      {actions.isMaterial && !actions.isRestricted && !guest && (
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

      {!guest && (
        <>
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
      )}
    </>
  );
}

// ─── Armed menu body (heavy — only mounted after first hover) ─────────────────

interface ArmedMenuBodyProps {
  item: ItemData;
  onAddAttachment?: () => void;
  itemPath?: string;
  // Ref that always points to the latest actions object so context consumers
  // never read a stale snapshot without re-rendering the whole parent.
  actionsRef: React.MutableRefObject<ReturnType<typeof useItemActions> | null>;
  // Called once (on mount) to signal the parent that a non-null context is ready.
  onReady: () => void;
}

function ArmedMenuBody({ item, onAddAttachment, itemPath, actionsRef, onReady }: ArmedMenuBodyProps) {
  const actions = useItemActions(item, itemPath);

  // Keep the shared ref current on every render so context consumers always
  // read the latest state (dialog open flags, loading states, etc.) through
  // the getter defined on the stable context value object.
  // eslint-disable-next-line react-hooks/refs
  actionsRef.current = actions;

  // Signal the parent to flip its "ready" flag once, before the first paint,
  // so ItemActionsDropdownTrigger renders the real dropdown on the same frame.
  useLayoutEffect(onReady, []);

  return (
    <>
      <ContextMenuContent className="w-56">
        <MenuItemsList isContextMenu />
      </ContextMenuContent>

      {!item.isExternal && actions.editDialogOpen && (
        <FileEditDialog
          open
          onOpenChange={actions.setEditDialogOpen}
          target={{ type: item.type, id: item.id, data: item.data }}
        />
      )}

      {actions.deleteDialogOpen && (
        <Dialog open onOpenChange={actions.setDeleteDialogOpen}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-destructive">
                <ShieldAlert className="h-5 w-5" />
                {item.staged === "created" ? actions.t("discardDraft") : actions.t("deleteTitle", { type: actions.isMaterial ? actions.t("material") : actions.t("folder") })}
              </DialogTitle>
              <DialogDescription>
                {item.staged === "created"
                  ? actions.t("discardDraftConfirm", { type: item.type === "material" ? actions.t("material") : actions.t("folder") })
                  : <>{actions.t("deletePermanentlyConfirm")} <span className="font-semibold text-foreground">{actions.title}</span>?</>
                }
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="gap-2 sm:gap-2 mt-6">
              <Button variant="ghost" onClick={() => actions.setDeleteDialogOpen(false)} disabled={actions.deleting} className="sm:mr-auto">
                {actions.t("cancel")}
              </Button>

              {item.staged !== "created" && (
                <Button
                  variant="outline"
                  onClick={actions.handleDraftDelete}
                  disabled={actions.deleting}
                  className="gap-2 border-dashed border-destructive/40 text-destructive hover:bg-destructive/5 hover:border-destructive/60"
                >
                  <Plus className="h-4 w-4" />
                  {actions.t("draft")}
                </Button>
              )}

              <Button
                variant="destructive"
                onClick={item.staged === "created" ? actions.handleDraftDelete : actions.handleDirectDelete}
                disabled={actions.deleting}
                className="gap-2 shadow-sm"
              >
                {actions.deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : (item.staged === "created" ? <Trash2 className="h-4 w-4" /> : <Send className="h-4 w-4" />)}
                {item.staged === "created" ? actions.t("discard") : actions.t("deleteNow")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

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
  // The Radix ContextMenu + DropdownMenu trees — plus the useItemActions hook
  // (print/download/i18n/store machinery) — are by far the heaviest part of a
  // row's render. Defer mounting it until the row is first interacted with,
  // so the initial paint of a freshly-navigated listing mounts zero menus.
  const [armed, setArmed] = useState(false);
  // Flips to true once ArmedMenuBody's useLayoutEffect fires, ensuring the
  // context value is non-null before the first paint after arming.
  const [contextReady, setContextReady] = useState(false);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Shared mutable ref — ArmedMenuBody writes the latest actions object here
  // on every render, so context consumers always read current state.
  const actionsRef = useRef<ReturnType<typeof useItemActions> | null>(null);

  // Stable context-value object: its identity is fixed after arming so React
  // never re-renders all context consumers on unrelated re-renders.
  // The `actions` getter reads through actionsRef to avoid stale closures.
  const stableCtxValue = useMemo<ActionsContextValue | null>(() => {
    if (!contextReady) return null;
    return {
      item,
      get actions() { return actionsRef.current!; },
      onAddAttachment,
      itemPath,
    };
  }, [contextReady]); // intentionally omit item/onAddAttachment/itemPath — they're read via closure/getter

  const arm = useCallback(() => {
    if (armTimer.current) {
      clearTimeout(armTimer.current);
      armTimer.current = null;
    }
    setArmed(true);
  }, []);

  // A deliberate hover (rest ~120ms) arms the row so right-click works on the
  // first try; a quick sweep across many rows does not, avoiding a mount storm.
  const armSoon = useCallback(() => {
    if (armTimer.current) return;
    armTimer.current = setTimeout(() => {
      armTimer.current = null;
      setArmed(true);
    }, 120);
  }, []);

  const cancelArm = useCallback(() => {
    if (armTimer.current) {
      clearTimeout(armTimer.current);
      armTimer.current = null;
    }
  }, []);

  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current);
  }, []);

  const onReady = useCallback(() => setContextReady(true), []);

  // Always render the same wrapper tree above {children} regardless of armed
  // state. Switching component types here would cause React to unmount/remount
  // the child DOM node, destroying its :hover CSS state and flashing the
  // background. Stable types = React updates the existing DOM node in place.
  return (
    <ArmContext.Provider value={contextReady ? null : arm}>
      <ActionsContext.Provider value={stableCtxValue}>
        <ContextMenu>
          <ContextMenuTrigger
            asChild
            onPointerEnter={!armed ? armSoon : undefined}
            onPointerLeave={!armed ? cancelArm : undefined}
            onFocus={!armed ? arm : undefined}
            onContextMenu={!armed ? arm : undefined}
          >
            {children}
          </ContextMenuTrigger>

          {armed && (
            <ArmedMenuBody
              item={item}
              onAddAttachment={onAddAttachment}
              itemPath={itemPath}
              actionsRef={actionsRef}
              onReady={onReady}
            />
          )}
        </ContextMenu>
      </ActionsContext.Provider>
    </ArmContext.Provider>
  );
}

export function ItemActionsDropdownTrigger({
  className,
  iconClassName,
}: {
  className?: string;
  iconClassName?: string;
} = {}) {
  const context = useContext(ActionsContext);
  const arm = useContext(ArmContext);

  const btnClass = cn("h-8 w-8 hover:bg-muted active:scale-95 transition-transform", className);
  const iconClass = cn("h-4 w-4 text-muted-foreground", iconClassName);

  // Unarmed row: render a cheap static trigger (no Radix). Hovering / pointer-
  // down on it arms the row, mounting the real menu before the click resolves.
  if (!context) {
    return (
      <div onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
        <Button
          variant="ghost"
          size="icon"
          className={btnClass}
          aria-haspopup="menu"
          onPointerEnter={arm ?? undefined}
          onPointerDown={arm ?? undefined}
          onClick={arm ?? undefined}
        >
          <MoreVertical className={iconClass} />
        </Button>
      </div>
    );
  }

  return (
    <div onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={btnClass}
            onPointerUp={(e) => e.currentTarget.blur()}
          >
            <MoreVertical className={iconClass} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56" onCloseAutoFocus={(e) => e.preventDefault()}>
          <MenuItemsList />
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
