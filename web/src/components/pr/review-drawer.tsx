"use client";

import { useState, useEffect } from "react";
import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
    SheetDescription,
} from "@/components/ui/sheet";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
    FilePlus,
    FilePenLine,
    FileX,
    FolderPlus,
    FolderPen,
    FolderX,
    ArrowRightLeft,
    Trash2,
    Loader2,
    Send,
    AlertTriangle,
    Clock,
    Eye,
    ListChecks,
    ExternalLink,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import {
    useStagingStore,
    type StagedOperation,
    isExpired,
    isExpiringSoon,
    msUntilExpiry,
    formatTimeRemaining,
    hasFileKey,
    unwrapOp,
} from "@/lib/staging-store";
import { autoTitle, submitDirectOperations, truncateDescription } from "@/lib/pr-client";
import { StagedItemEditDialog } from "./staged-item-edit-dialog";
import { useConfigStore } from "@/lib/stores";
import { PreviewDialog } from "./preview-dialog";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";
import { MIME_QCM } from "@/lib/file-utils";

const OP_ICONS: Record<string, React.ElementType> = {
    create_material: FilePlus,
    edit_material: FilePenLine,
    delete_material: FileX,
    create_directory: FolderPlus,
    edit_directory: FolderPen,
    delete_directory: FolderX,
    move_item: ArrowRightLeft,
};

const PRIVILEGED_ROLES = new Set(["moderator", "bureau", "vieux"]);
const LIMIT_REGULAR = 50;
const LIMIT_PRIVILEGED = 500;

const OP_COLORS: Record<string, string> = {
    create_material: "text-green-600 dark:text-green-400",
    edit_material: "text-blue-600 dark:text-blue-400",
    delete_material: "text-red-500 dark:text-red-400",
    create_directory: "text-green-600 dark:text-green-400",
    edit_directory: "text-blue-600 dark:text-blue-400",
    delete_directory: "text-red-500 dark:text-red-400",
    move_item: "text-amber-600 dark:text-amber-400",
};

function OperationCard({
    staged,
    index,
    onRemove,
    onEdit,
    onPreview,
    tCommon,
}: {
    staged: StagedOperation;
    index: number;
    onRemove: (i: number) => void;
    onEdit: (i: number) => void;
    onPreview: (i: number) => void;
    tCommon: any;
}) {
    const t = useTranslations("Staging");
    const op = unwrapOp(staged);
    const Icon = OP_ICONS[op.op] ?? FilePlus;
    const color = OP_COLORS[op.op] ?? "";

    const expired = isExpired(staged);
    const expiringSoon = isExpiringSoon(staged);
    const remaining = msUntilExpiry(staged);

    const label = t(`labels.${op.op}`, {
        name: (op as any).title || (op as any).name || (op as any).target_title || (op as any).target_name || "",
        type: (op as any).target_type === "directory" ? t("labels.folder") : t("labels.material")
    });

    const isQcm = (op as any).file_mime_type === MIME_QCM;
    const isLink = (op as any).type === "link" || !!(op as any).metadata?.url;
    const linkUrl = (op as any).metadata?.url as string | undefined;
    const hasQcmDraft = isQcm && !!(op as any).metadata?.qcm_draft;
    const qcmEditHref = isQcm && op.op === "edit_material"
        ? `/qcm/${op.material_id}/edit?draftIndex=${index}`
        : isQcm && op.op === "create_material"
        ? `/qcm/new?${(op as any).directory_id ? `directoryId=${encodeURIComponent((op as any).directory_id)}&` : ""}draftIndex=${index}`
        : null;

    return (
        <div className={`rounded-lg border transition-colors ${expired ? "border-red-300 bg-red-50/50 dark:border-red-800 dark:bg-red-950/20" : expiringSoon ? "border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20" : ""}`}>
            <div className="flex items-center gap-3 p-3">
                <div className={`shrink-0 ${expired ? "text-red-400" : isQcm ? "text-violet-500" : isLink ? "text-sky-600 dark:text-sky-400" : color}`}>
                    {isQcm ? <ListChecks className="h-4 w-4" /> : isLink ? <ExternalLink className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
                </div>
                <div className="min-w-0 flex-1">
                    <p className={`text-sm font-medium leading-tight ${expired ? "line-through text-muted-foreground" : ""}`}>
                        {label}
                    </p>
                    {isLink && linkUrl && (
                        <p className="text-[11px] text-muted-foreground truncate font-mono mt-0.5 max-w-[200px] sm:max-w-xs">
                            {linkUrl}
                        </p>
                    )}
                    {expired && hasFileKey(op) && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-0.5">
                            <AlertTriangle className="h-3 w-3" />
                            {t("expiredFile")}
                        </p>
                    )}
                    {expiringSoon && remaining !== null && (
                        <p className="text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1 mt-0.5">
                            <Clock className="h-3 w-3" />
                            {t("expiresIn", { time: formatTimeRemaining(remaining, t) })}
                        </p>
                    )}
                </div>
                <div className="flex items-center shrink-0">
                    {/* Preview: for QCMs use blob URL from metadata; for others use /upload/preview */}
                    { !expired && hasFileKey(op) && (!isQcm || hasQcmDraft) && (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            onClick={() => onPreview(index)}
                            aria-label={t("preview")}
                        >
                            <Eye className="h-3.5 w-3.5" />
                        </Button>
                    )}
                    {/* Edit: for QCM edit ops, link to QCM editor; otherwise open metadata dialog */}
                    { qcmEditHref ? (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            asChild
                            aria-label={tCommon("edit")}
                        >
                            <Link href={qcmEditHref}>
                                <FilePenLine className="h-3.5 w-3.5" />
                            </Link>
                        </Button>
                    ) : !isQcm && (op.op === "create_material" || op.op === "edit_material" || op.op === "create_directory" || op.op === "edit_directory") ? (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            onClick={() => onEdit(index)}
                            aria-label={tCommon("edit")}
                        >
                            <FilePenLine className="h-3.5 w-3.5" />
                        </Button>
                    ) : null}
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => onRemove(index)}
                        aria-label={tCommon("remove")}
                    >
                        <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                </div>
            </div>
        </div>
    );
}

export function ReviewDrawer() {
    const t = useTranslations("Staging");
    const tCommon = useTranslations("Common");
    const tAuto = useTranslations("AutoTitle");
    const operations = useStagingStore((s) => s.operations) ?? [];
    const reviewOpen = useStagingStore((s) => s.reviewOpen);
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
    const removeOperation = useStagingStore((s) => s.removeOperation);
    const clearOperations = useStagingStore((s) => s.clearOperations);
    const purgeExpired = useStagingStore((s) => s.purgeExpired);
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
    const [editingIndex, setEditingIndex] = useState<number | null>(null);

    // Preview state
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [previewMime, setPreviewMime] = useState<string | undefined>();
    const [previewName, setPreviewName] = useState<string | undefined>();

    const closePreview = () => {
        if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
    };

    const handlePreview = async (index: number) => {
        const op = unwrapOp(operations[index]);
        if (!hasFileKey(op) || !op.file_key) return;

        // Prefer the drafted title over the raw file name for the preview header.
        // If this is a content-only op (no title), look for a sibling metadata op
        // for the same material that carries the new title.
        const siblingTitle = !(op as any).title && op.op === "edit_material"
            ? operations.map(unwrapOp).find(
                (o) => o.op === "edit_material" && o.material_id === op.material_id && !(o as any).file_key
              ) as any
            : null;
        const displayName = (op as any).title || siblingTitle?.title || op.file_name || undefined;
        const isQcm = (op as any).file_mime_type === MIME_QCM;

        if (isQcm) {
            const qcmDraft = (op as any).metadata?.qcm_draft;
            if (qcmDraft) {
                const blob = new Blob([JSON.stringify(qcmDraft)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                setPreviewUrl(url);
                setPreviewName(displayName);
                setPreviewMime(MIME_QCM);
            }
            return;
        }

        try {
            const res = await apiFetch<{ url: string }>(`/upload/preview?file_key=${encodeURIComponent(op.file_key)}`);
            if (res && res.url) {
                setPreviewUrl(res.url);
                setPreviewName(displayName);
                setPreviewMime(op.file_mime_type || undefined);
            }
        } catch (e) {
            toast.error((e as Error).message || t("unableToPreview"));
        }
    };

    // Auto-fill title & description
    useEffect(() => {
        if (operations.length === 0) return;
        
        const ops = operations.map(unwrapOp);
        
        // Auto-title if empty
        if (title === "") {
            setTitle(autoTitle(ops, tAuto));
        }

        // Auto-description with path if empty
        if (description === "") {
            let cancelled = false;

            async function resolveAllPaths() {
                const materialIdsToFetch: string[] = [];
                const directoryIdsToFetch: string[] = [];
                
                for (const op of ops) {
                    if (op.op === "edit_material" || op.op === "delete_material") {
                        if (!op.material_id.startsWith("$")) {
                            materialIdsToFetch.push(op.material_id);
                        }
                    } else if (op.op === "move_item") {
                        if (op.new_parent_id && !op.new_parent_id.startsWith("$")) {
                            directoryIdsToFetch.push(op.new_parent_id);
                        }
                    } else if (op.op === "create_material") {
                        if (op.directory_id && !op.directory_id.startsWith("$")) {
                            directoryIdsToFetch.push(op.directory_id);
                        }
                    } else if (op.op === "create_directory") {
                        if (op.parent_id && !op.parent_id.startsWith("$")) {
                            directoryIdsToFetch.push(op.parent_id);
                        }
                    }
                }

                let resolvedMats: Record<string, { directory_id: string | null; title: string }> = {};
                let resolvedDirs: Record<string, { id: string; name: string; slug: string }[]> = {};

                if (materialIdsToFetch.length > 0 || directoryIdsToFetch.length > 0) {
                    try {
                        const payload = {
                            material_ids: Array.from(new Set(materialIdsToFetch)),
                            directory_ids: Array.from(new Set(directoryIdsToFetch))
                        };
                        const res = await apiFetch<{
                            materials: Record<string, { directory_id: string | null; title: string }>;
                            directories: Record<string, { id: string; name: string; slug: string }[]>;
                        }>("/directories/resolve-paths", {
                            method: "POST",
                            body: JSON.stringify(payload)
                        });
                        resolvedMats = res.materials || {};
                        resolvedDirs = res.directories || {};
                    } catch {
                        // ignore
                    }
                }

                const paths: string[] = [];
                
                for (const op of ops) {
                    let dirId: string | null = null;
                    let itemName: string | null = null;

                    if (op.op === "edit_material" || op.op === "delete_material") {
                        const mat = resolvedMats[op.material_id];
                        if (mat) {
                            dirId = mat.directory_id;
                            itemName = mat.title;
                        }
                    } else if (op.op === "move_item") {
                        dirId = op.new_parent_id;
                        itemName = op.target_title || op.target_name || t("item");
                    } else if (op.op === "create_material") {
                        dirId = op.directory_id;
                        itemName = op.title;
                    } else if (op.op === "create_directory") {
                        dirId = op.parent_id ?? null;
                        itemName = op.name;
                    }

                    if (dirId && !dirId.startsWith("$")) {
                        const path = resolvedDirs[dirId];
                        if (path && path.length > 0) {
                            const pathStr = path.map(p => p.name).join(" › ") + " › " + (itemName || "");
                            paths.push(pathStr);
                        } else if (itemName) {
                            paths.push(itemName);
                        }
                    } else if (itemName) {
                        paths.push(itemName);
                    }
                }

                if (!cancelled && paths.length > 0) {
                    setDescription(truncateDescription(paths.join("\n")));
                }
            }

            resolveAllPaths();
            return () => { cancelled = true; };
        }

    }, [operations, tAuto]);

    const { user } = useAuth();
    const maxDescriptionLength = useConfigStore((s) => s.config?.max_contribution_note_length ?? 10000);
    const isPrivileged = PRIVILEGED_ROLES.has(user?.role ?? "");
    const maxOps = isPrivileged ? LIMIT_PRIVILEGED : LIMIT_REGULAR;
    const overLimit = operations.length > maxOps;

    const expiredCount = operations.filter((s) => isExpired(s)).length;
    const expiringSoonCount = operations.filter((s) => isExpiringSoon(s)).length;
    const hasExpired = expiredCount > 0;

    const canSubmit =
        title.trim().length >= 3 && operations.length > 0 && !submitting && !hasExpired && !overLimit;

    const handleSubmit = async () => {
        if (!canSubmit) return;
        setSubmitting(true);
        const result = await submitDirectOperations(
            operations.map(unwrapOp),
            title,
            description,
            tAuto
        );
        setSubmitting(false);

        if (result) {
            clearOperations();
            setTitle("");
            setDescription("");
            setReviewOpen(false);
        }
    };

    const handleClear = () => {
        clearOperations();
        setTitle("");
        setDescription("");
        setShowDiscardConfirm(false);
        setReviewOpen(false);
        toast(t("draftDiscarded"));
    };

    return (
        <>
            {previewUrl && (
                <PreviewDialog
                    url={previewUrl}
                    fileName={previewName}
                    mimeType={previewMime}
                    onClose={closePreview}
                />
            )}
            <StagedItemEditDialog index={editingIndex} onClose={() => setEditingIndex(null)} />
            <Sheet open={reviewOpen} onOpenChange={setReviewOpen}>
                <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
                    <SheetHeader className="shrink-0 space-y-1 border-b px-5 py-4 pr-12 text-left">
                        <SheetTitle className="flex items-center gap-2">
                            {t("title")}
                            <Badge variant="secondary" className="rounded-full text-xs font-medium">
                                {t("changesCount", { count: operations.length })}
                            </Badge>
                        </SheetTitle>
                        <SheetDescription>
                            {t("description")}
                        </SheetDescription>
                    </SheetHeader>

                    <ScrollArea className="min-h-0 flex-1">
                        <div className="space-y-5 px-5 py-5">
                    {/* Over limit banner */}
                    {overLimit && (
                        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/30">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                            <div className="min-w-0 flex-1">
                                <p className="text-sm font-medium text-red-700 dark:text-red-400">
                                    {t("overLimitTitle", { count: operations.length, max: maxOps })}
                                </p>
                                <p className="text-xs text-red-600/80 dark:text-red-400/70 mt-0.5">
                                    {t("overLimitText")}
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Expiry banner */}
                    {hasExpired && (
                        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/30">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                            <div className="min-w-0 flex-1">
                                <p className="text-sm font-medium text-red-700 dark:text-red-400">
                                    {t("expiredBannerTitle", { count: expiredCount })}
                                </p>
                                <p className="text-xs text-red-600/80 dark:text-red-400/70 mt-0.5">
                                    {t("expiredBannerText")}
                                </p>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="mt-2 h-7 text-xs border-red-300 text-red-600 hover:bg-red-100 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-950/50"
                                    onClick={() => {
                                        const removed = purgeExpired();
                                        toast(t("itemsRemoved", { count: removed }));
                                    }}
                                >
                                    <Trash2 className="mr-1.5 h-3 w-3" />
                                    {t("removeExpired")}
                                </Button>
                            </div>
                        </div>
                    )}
                    {!hasExpired && expiringSoonCount > 0 && (
                        <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/30">
                            <Clock className="h-4 w-4 shrink-0 text-amber-500" />
                            <p className="text-xs text-amber-700 dark:text-amber-400">
                                {t("expiringSoonBanner", { count: expiringSoonCount })}
                            </p>
                        </div>
                    )}

                    {/* Operations list */}
                    <section aria-labelledby="review-change-list" className="space-y-2">
                        <h3 id="review-change-list" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            {t("changes")}
                        </h3>
                        <div className="space-y-2">
                            {operations.map((staged, i) => {
                                const op = unwrapOp(staged);
                                // Batch-created items share stagedAt and often the same
                                // parent directory. Prefer the generated temp_id so each
                                // staged create keeps a distinct React identity.
                                const targetId =
                                    (op as any).material_id ??
                                    (op as any).target_id ??
                                    (op as any).temp_id ??
                                    (op as any).file_key ??
                                    (op as any).directory_id ??
                                    i;
                                const stableKey = `${staged.stagedAt ?? i}-${op.op}-${targetId}`;
                                return (
                                    <OperationCard
                                        key={stableKey}
                                        staged={staged}
                                        index={i}
                                        onRemove={removeOperation}
                                        onEdit={setEditingIndex}
                                        onPreview={handlePreview}
                                        tCommon={tCommon}
                                    />
                                );
                            })}
                            {operations.length === 0 && (
                                <p className="py-8 text-center text-sm text-muted-foreground">
                                    {t("noPendingChanges")}
                                </p>
                            )}
                        </div>
                    </section>

                    <Separator />

                    {/* Title & description form */}
                    <div className="space-y-3 pt-4">
                        <div className="space-y-1.5">
                            <label
                                htmlFor="pr-title"
                                className="text-sm font-medium"
                            >
                                {t("contributionTitle")}
                            </label>
                            <Input
                                id="pr-title"
                                placeholder={t("contributionTitlePlaceholder")}
                                value={title}
                                onChange={(e) => setTitle(e.target.value)}
                                maxLength={300}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <label
                                htmlFor="pr-desc"
                                className="text-sm font-medium flex justify-between items-center"
                            >
                                <span>
                                    {t("moderatorNote")}{" "}
                                    <span className="text-muted-foreground">
                                        {t("optional")}
                                    </span>
                                </span>
                                <span className={cn(
                                    "text-[10px] font-mono",
                                    description.length > maxDescriptionLength * 0.95 ? "text-destructive font-bold" : "text-muted-foreground"
                                )}>
                                    {description.length}/{maxDescriptionLength}
                                </span>
                            </label>
                            <Textarea
                                id="pr-desc"
                                placeholder={t("moderatorNotePlaceholder")}
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                maxLength={maxDescriptionLength}
                                rows={4}
                            />
                        </div>
                    </div>
                        </div>
                    </ScrollArea>

                    <div className="shrink-0 border-t bg-background px-5 py-4">
                        <Button
                            onClick={handleSubmit}
                            disabled={!canSubmit}
                            className="h-11 w-full gap-2 font-semibold text-primary-foreground"
                        >
                            {submitting ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                            {t("submitContribution")}
                        </Button>
                        <Button
                            variant="ghost"
                            onClick={() => setShowDiscardConfirm(true)}
                            disabled={operations.length === 0 || submitting}
                            className="mt-1 w-full text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        >
                            {t("discardAll")}
                        </Button>
                    </div>
                </SheetContent>
            </Sheet>

            <Dialog
                open={showDiscardConfirm}
                onOpenChange={setShowDiscardConfirm}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="text-destructive">
                            {t("discardDraftTitle")}
                        </DialogTitle>
                        <DialogDescription>
                            {t("discardDraftDescription", { count: operations.length })}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="gap-2 sm:gap-2 mt-2">
                        <Button
                            variant="ghost"
                            onClick={() => setShowDiscardConfirm(false)}
                        >
                            {t("back")}
                        </Button>
                        <Button
                            variant="destructive"
                            className="gap-2"
                            onClick={handleClear}
                        >
                            <Trash2 className="h-4 w-4" />
                            {t("delete")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
