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
} from "lucide-react";
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
import { autoTitle, submitDirectOperations } from "@/lib/pr-client";
import { StagedItemEditDialog } from "./staged-item-edit-dialog";
import { useBrowseRefreshStore } from "@/lib/stores";
import { PreviewDialog } from "./preview-dialog";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";

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

    return (
        <div className={`rounded-lg border transition-colors ${expired ? "border-red-300 bg-red-50/50 dark:border-red-800 dark:bg-red-950/20" : expiringSoon ? "border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20" : ""}`}>
            <div className="flex items-center gap-3 p-3">
                <div className={`shrink-0 ${expired ? "text-red-400" : color}`}>
                    <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <p className={`text-sm font-medium leading-tight ${expired ? "line-through text-muted-foreground" : ""}`}>
                        {label}
                    </p>
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
                    { !expired && hasFileKey(op) && (
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
                    { (op.op === "create_material" || op.op === "edit_material" || op.op === "create_directory" || op.op === "edit_directory") && (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            onClick={() => onEdit(index)}
                            aria-label={tCommon("edit")}
                        >
                            <FilePenLine className="h-3.5 w-3.5" />
                        </Button>
                    )}
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
    const triggerBrowseRefresh = useBrowseRefreshStore((s) => s.triggerBrowseRefresh);
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

    const handlePreview = async (index: number) => {
        const op = unwrapOp(operations[index]);
        if (!hasFileKey(op) || !op.file_key) return;

        try {
            const res = await apiFetch<{ url: string }>(`/upload/preview?file_key=${encodeURIComponent(op.file_key)}`);
            if (res.url) {
                setPreviewUrl(res.url);
                setPreviewName(op.file_name || undefined);
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
                    setDescription(paths.join("\n"));
                }
            }

            resolveAllPaths();
            return () => { cancelled = true; };
        }
         
    }, [operations.length]);

    const { user } = useAuth();
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
            if (result.status === "approved") {
                triggerBrowseRefresh();
            }
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

    // Summarize operation types
    const typeCounts = operations.reduce(
        (acc, staged) => {
            const innerOp = unwrapOp(staged);
            acc[innerOp.op] = (acc[innerOp.op] || 0) + 1;
            return acc;
        },
        {} as Record<string, number>,
    );

    return (
        <>
            {previewUrl && (
                <PreviewDialog
                    url={previewUrl}
                    fileName={previewName}
                    mimeType={previewMime}
                    onClose={() => setPreviewUrl(null)}
                />
            )}
            <StagedItemEditDialog index={editingIndex} onClose={() => setEditingIndex(null)} />
            <Sheet open={reviewOpen} onOpenChange={setReviewOpen}>
                <SheetContent side="right" className="flex w-full flex-col overflow-hidden sm:max-w-lg">
                    <SheetHeader className="space-y-1">
                        <SheetTitle className="flex items-center gap-2">
                            {t("title")}
                            <Badge variant="secondary" className="text-xs">
                                {t("changesCount", { count: operations.length })}
                            </Badge>
                        </SheetTitle>
                        <SheetDescription>
                            {t("description")}
                        </SheetDescription>
                    </SheetHeader>

                    {/* Over limit banner */}
                    {overLimit && (
                        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/30 my-4">
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

                    {/* Summary badges */}
                    {Object.keys(typeCounts).length > 0 && (
                        <div className="flex flex-wrap gap-1.5 px-1 py-2">
                            {Object.entries(typeCounts).map(([type, count]) => {
                                const Icon = OP_ICONS[type] ?? FilePlus;
                                const color = OP_COLORS[type] ?? "";
                                return (
                                    <Badge
                                        key={type}
                                        variant="outline"
                                        className="gap-1 text-xs"
                                    >
                                        <Icon className={`h-3 w-3 ${color}`} />
                                        {count}
                                    </Badge>
                                );
                            })}
                        </div>
                    )}

                    <Separator />

                    {/* Expiry banner */}
                    {hasExpired && (
                        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/30 my-4">
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
                        <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/30 my-4">
                            <Clock className="h-4 w-4 shrink-0 text-amber-500" />
                            <p className="text-xs text-amber-700 dark:text-amber-400">
                                {t("expiringSoonBanner", { count: expiringSoonCount })}
                            </p>
                        </div>
                    )}

                    {/* Operations list */}
                    <ScrollArea className="min-h-0 flex-1 -mx-6 px-6 my-2">
                        <div className="space-y-2 py-1">
                            {operations.map((staged, i) => (
                                <OperationCard
                                    key={i}
                                    staged={staged}
                                    index={i}
                                    onRemove={removeOperation}
                                    onEdit={setEditingIndex}
                                    onPreview={handlePreview}
                                    tCommon={tCommon}
                                />
                            ))}
                            {operations.length === 0 && (
                                <p className="py-8 text-center text-sm text-muted-foreground">
                                    {t("noPendingChanges")}
                                </p>
                            )}
                        </div>
                    </ScrollArea>

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
                                    description.length > 9500 ? "text-destructive font-bold" : "text-muted-foreground"
                                )}>
                                    {description.length}/10000
                                </span>
                            </label>
                            <Textarea
                                id="pr-desc"
                                placeholder={t("moderatorNotePlaceholder")}
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                maxLength={10000}
                                rows={4}
                            />
                        </div>
                    </div>

                    <div className="mt-6 flex flex-col gap-2 pt-2 pb-6">
                        <Button
                            onClick={handleSubmit}
                            disabled={!canSubmit}
                            className="w-full gap-2 text-primary-foreground font-semibold h-11"
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
                            className="w-full text-muted-foreground hover:text-destructive hover:bg-destructive/10"
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
                    <DialogFooter className="gap-2 sm:gap-0 mt-2">
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
