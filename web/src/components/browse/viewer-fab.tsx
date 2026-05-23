"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import {
  Download,
  Share2,
  Paperclip,
  Loader2,
  Printer,
  Info,
  MessageSquare,
  Highlighter,
  Inbox,
  Edit,
  Trash2,
  ThumbsUp,
} from "lucide-react";
import { FlagButton } from "@/components/flags/flag-button";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { useDownload } from "@/hooks/use-download";
import { usePrint } from "@/hooks/use-print";
import { useUIStore } from "@/lib/stores";
import { FileEditDialog } from "@/components/pr/file-edit-dialog";
import { useStagingStore } from "@/lib/staging-store";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
  DrawerClose,
} from "@/components/ui/drawer";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

// ─── Action cell ──────────────────────────────────────────────────────────────

interface ActionCellProps {
  icon: ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  href?: string;
  badge?: number;
  tint?: "default" | "violet" | "destructive" | "primary";
}

function ActionCell({
  icon,
  label,
  onClick,
  disabled = false,
  href,
  badge,
  tint = "default",
}: ActionCellProps) {
  const tileClass = cn(
    "relative flex h-16 w-16 items-center justify-center rounded-2xl transition-transform active:scale-90",
    {
      "bg-secondary hover:bg-secondary/80": tint === "default",
      "bg-violet-500/15 dark:bg-violet-500/20 text-violet-500 hover:bg-violet-500/20 dark:hover:bg-violet-500/30": tint === "violet",
      "bg-blue-500/15 dark:bg-blue-500/20 text-blue-500 hover:bg-blue-500/20 dark:hover:bg-blue-500/30": tint === "primary",
      "bg-destructive/10 text-destructive hover:bg-destructive/20": tint === "destructive",
      "opacity-50 grayscale pointer-events-none": disabled,
    },
  );

  const tile = (
    <span className={tileClass}>
      {icon}
      {badge != null && badge > 0 && (
        <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-violet-600 px-1 text-[10px] font-bold text-white">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </span>
  );

  const labelEl = (
    <span className="text-[11.5px] font-medium text-muted-foreground text-center truncate w-full px-1">
      {label}
    </span>
  );

  const wrap = "flex flex-col items-center gap-2";

  if (href) {
    return (
      <DrawerClose asChild>
        <Link href={href} className={wrap}>
          {tile}
          {labelEl}
        </Link>
      </DrawerClose>
    );
  }

  return (
    <button className={wrap} onClick={onClick} disabled={disabled}>
      {tile}
      {labelEl}
    </button>
  );
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface ViewerFabProps {
  material: Record<string, unknown>;
  materialId: string;
  materialTitle?: string;
  directoryId?: string;
  attachmentCount?: number;
  isAttachment?: boolean;
  viewerType?: string;
  mimeType?: string;
  fileName?: string;
  /** Controlled open state — owned by the parent (MaterialViewer). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ViewerFab({
  material,
  materialId,
  materialTitle,
  attachmentCount = 0,
  isAttachment = false,
  viewerType = "",
  mimeType = "",
  fileName = "",
  open,
  onOpenChange,
}: ViewerFabProps) {
  const t = useTranslations("Browse");
  const pathname = usePathname();
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const searchParams = useSearchParams();
  const isPreview = !!searchParams.get("preview_pr");
  const isDraft = materialId.startsWith("$");
  const isRestricted = isPreview || isDraft;

  const { openSidebar, updateSidebarData, sidebarTarget } = useUIStore();
  const { downloadMaterial, downloadQcmAsXml, isDownloading } = useDownload();
  const { print, isPrinting, canPrint } = usePrint({
    viewerType,
    materialId,
    fileName,
    mimeType,
  });
  // Source of truth for like state: the sidebar target when it points at this
  // material (so FAB and Details tab stay in sync), else the material prop.
  const liveData =
    sidebarTarget && sidebarTarget.type === "material" && sidebarTarget.id === materialId
      ? sidebarTarget.data
      : material;
  const isLiked = Boolean(liveData.is_liked);
  const likeCount = Number(liveData.like_count ?? 0);
  const [isLiking, setIsLiking] = useState(false);
  const addOperation = useStagingStore((s) => s.addOperation);

  const handleShare = async () => {
    const shareUrl = `${window.location.origin}${pathname}`;
    if (navigator.share) {
      try {
        await navigator.share({
          title: materialTitle || "Share document",
          url: shareUrl,
        });
      } catch (err) {
        if (err instanceof Error && err.name !== "AbortError") {
          toast.error(t("sharingFailed"));
        }
      }
    } else {
      navigator.clipboard.writeText(shareUrl).then(() => {
        toast.success(t("linkCopied"));
      });
    }
  };
  
  const handleLike = async () => {
    if (isLiking) return;
    const next = !isLiked;
    const nextCount = likeCount + (next ? 1 : -1);

    // Optimistic update through the store so FAB and Details tab stay in sync.
    updateSidebarData({ is_liked: next, like_count: nextCount });
    setIsLiking(true);

    try {
      await apiFetch(`/materials/${materialId}/like`, { method: "POST" });
    } catch {
      updateSidebarData({ is_liked: !next, like_count: likeCount });
      toast.error(t("failedToUpdateLike") || "Failed to update like");
    } finally {
      setIsLiking(false);
    }
  };

  
  const handleDelete = () => {
    addOperation({
        op: "delete_material",
        material_id: materialId,
    });
    toast.success(t("addedDeletionToDraft"));
    close();
  };

  const close = () => onOpenChange(false);

  return (
    <>
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent
        className={cn(
          "rounded-t-3xl border-t border-border/50 px-5 pb-10 pt-0",
        )}
      >
        {/* Visually hidden title satisfies Radix's a11y requirement */}
        <DrawerTitle className="sr-only">{t("documentActions")}</DrawerTitle>

        {/* The Drawer component provides its own drag handle viavaul's styling */}

        {/*
          3-column grid.
          Inline style bypasses any flex/grid context SheetContent may impose.
        */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: "1.25rem 0.5rem",
          }}
        >
          {/* ── Download ── */}
          <ActionCell
            icon={
              isDownloading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <Download className="h-5 w-5" />
              )
            }
            label={viewerType === "qcm" ? t("moodleXml") : t("download")}
            disabled={isDownloading}
            onClick={() => {
              close();
              if (viewerType === "qcm") {
                downloadQcmAsXml(materialId);
              } else {
                downloadMaterial(materialId);
              }
            }}
          />

          {/* ── Share ── */}
          <ActionCell
            icon={<Share2 className="h-5 w-5" />}
            label={t("share")}
            onClick={() => {
              close();
              handleShare();
            }}
          />

          {/* ── Print ── */}
          {canPrint && (
            <ActionCell
              icon={
                isPrinting ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  <Printer className="h-5 w-5" />
                )
              }
              label={t("print")}
              disabled={isPrinting}
              onClick={() => {
                close();
                print();
              }}
            />
          )}

          {/* ── Details ── */}
          <ActionCell
            icon={<Info className="h-5 w-5" />}
            label={t("details")}
            onClick={() => {
              close();
              openSidebar("details", {
                type: "material",
                id: materialId,
                data: { ...material, __viewerType: viewerType },
              });
            }}
          />

          {/* ── Chat ── */}
          <ActionCell
            icon={<MessageSquare className="h-5 w-5" />}
            label={t("chat")}
            disabled={isRestricted}
            onClick={() => {
              close();
              openSidebar("chat", {
                type: "material",
                id: materialId,
                data: material,
              });
            }}
          />

          {/* ── Annotations ── */}
          <ActionCell
            icon={<Highlighter className="h-5 w-5" />}
            label={t("annotations")}
            disabled={isRestricted}
            onClick={() => {
              close();
              openSidebar("annotations", {
                type: "material",
                id: materialId,
                data: material,
              });
            }}
          />

          {/* ── Edits ── */}
          <ActionCell
            icon={<Inbox className="h-5 w-5" />}
            label={t("edits")}
            disabled={isRestricted}
            onClick={() => {
              close();
              openSidebar("edits", {
                type: "material",
                id: materialId,
                data: material,
              });
            }}
          />

          {/* ── Edit (Metadata & Content) ── */}
          {viewerType === "qcm" ? (
            <ActionCell
              icon={<Edit className="h-5 w-5" />}
              label={t("edit")}
              href={`/qcm/${materialId}/edit`}
            />
          ) : (
            <ActionCell
              icon={<Edit className="h-5 w-5" />}
              label={t("edit")}
              onClick={() => {
                close();
                setEditDialogOpen(true);
              }}
            />
          )}

          {/* ── Like ── */}
          <ActionCell
            icon={
              isLiking ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <ThumbsUp className={cn("h-5 w-5", isLiked && "fill-blue-500")} />
              )
            }
            label={isLiked ? t("liked") : t("like")}
            tint={isLiked ? "primary" : "default"}
            disabled={isRestricted}
            onClick={handleLike}
          />

          {/* ── View attachments ── */}
          {!isAttachment && (
            <ActionCell
              icon={<Paperclip className="h-5 w-5" />}
              label={t("attachments")}
              tint="violet"
              href={`${pathname}/attachments`}
              badge={attachmentCount}
            />
          )}

          {/* ── Report ── */}
          <div className="flex flex-col items-center gap-2">
            <FlagButton
              targetType="material"
              targetId={materialId}
              variant="ghost"
              size="icon"
              disabled={isRestricted}
              className={cn(
                "h-16 w-16 rounded-2xl bg-destructive/10 text-destructive hover:bg-destructive/20 active:scale-90 transition-transform",
                isRestricted && "opacity-50 pointer-events-none"
              )}
              iconClassName="h-5 w-5"
              hideText
            />
            <span className="text-[11.5px] font-medium text-muted-foreground text-center truncate w-full px-1">
              {t("report")}
            </span>
          </div>

          {/* ── Delete ── */}
          <ConfirmDeleteDialog
            onConfirm={handleDelete}
            title={t("deleteDocument")}
            description={t("deleteDocumentConfirm", { title: materialTitle || t("thisDocument") })}
            trigger={
                <ActionCell
                    icon={<Trash2 className="h-5 w-5" />}
                    label={t("delete")}
                    tint="destructive"
                />
            }
          />
        </div>
      </DrawerContent>
    </Drawer>
    {/* Explicitly modal dialog triggered from the FAB grid */}
    <FileEditDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        target={{ type: "material", id: materialId, data: material }}
    />
    </>
  );
}
