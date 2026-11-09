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
import { Switch } from "@/components/ui/switch";
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
    Link2,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import {
    useStagingStore,
    type Operation,
    type StagedOperation,
    isExpired,
    isExpiringSoon,
    msUntilExpiry,
    formatTimeRemaining,
    hasFileKey,
    operationName,
} from "@/lib/staging-store";
import { autoTitle, describeOperationPaths, submitDirectOperations } from "@/lib/pr-client";
import { fetchStagedQcm } from "@/lib/qcm-draft";
import { StagedItemEditDialog } from "./staged-item-edit-dialog";
import { useConfigStore } from "@/lib/stores";
import { PreviewDialog } from "./preview-dialog";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";
import { MIME_QCM } from "@/lib/file-utils";
import { isExternalUrl } from "@/lib/url-utils";
import { isStaff } from "@/lib/guest";

const OP_ICONS: Record<Operation["op"], React.ElementType> = {
    create_material: FilePlus,
    edit_material: FilePenLine,
    delete_material: FileX,
    create_directory: FolderPlus,
    edit_directory: FolderPen,
    delete_directory: FolderX,
    move_item: ArrowRightLeft,
};

const OP_COLORS: Record<Operation["op"], string> = {
    create_material: "text-green-600 dark:text-green-400",
    edit_material: "text-blue-600 dark:text-blue-400",
    delete_material: "text-red-500 dark:text-red-400",
    create_directory: "text-green-600 dark:text-green-400",
    edit_directory: "text-blue-600 dark:text-blue-400",
    delete_directory: "text-red-500 dark:text-red-400",
    move_item: "text-amber-600 dark:text-amber-400",
};

function qcmEditHref(op: Operation, entryId: string): string {
    return op.op === "edit_material"
        ? `/qcm/${op.material_id}/edit`
        : `/qcm/new?draftId=${entryId}`;
}

function OperationCard({
    staged,
    disabled,
    onRemove,
    onEdit,
    onPreview,
}: {
    staged: StagedOperation;
    disabled: boolean;
    onRemove: (id: string) => void;
    onEdit: (id: string) => void;
    onPreview: (staged: StagedOperation) => void;
}) {
    const t = useTranslations("Staging");
    const tCommon = useTranslations("Common");
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
    const op = staged.operation;
    const Icon = OP_ICONS[op.op];
    const color = OP_COLORS[op.op];

    const expired = isExpired(staged);
    const expiringSoon = isExpiringSoon(staged);
    const remaining = msUntilExpiry(staged);

    const label = t(`labels.${op.op}`, {
        name: operationName(op),
        type: op.op === "move_item" && op.target_type === "directory" ? t("labels.folder") : t("labels.material"),
    });

    const materialOp = op.op === "create_material" || op.op === "edit_material" ? op : null;
    const isQcm = materialOp?.file_mime_type === MIME_QCM;
    const linkUrl = materialOp?.metadata?.url as string | undefined;
    const isLink = materialOp?.type === "link" || !!linkUrl;
    const isInternalLink = isLink && !isExternalUrl(linkUrl);
    const canEditDetails = op.op !== "delete_material" && op.op !== "delete_directory" && op.op !== "move_item";

    return (
        <div className={`rounded-lg border transition-colors ${expired ? "border-red-300 bg-red-50/50 dark:border-red-800 dark:bg-red-950/20" : expiringSoon ? "border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20" : ""}`}>
            <div className="flex items-center gap-3 p-3">
                <div className={`shrink-0 ${expired ? "text-red-400" : isQcm ? "text-violet-500" : isLink ? (isInternalLink ? "text-indigo-600 dark:text-indigo-400" : "text-sky-600 dark:text-sky-400") : color}`}>
                    {isQcm ? (
                        <ListChecks className="h-4 w-4" />
                    ) : isLink ? (
                        isInternalLink ? (
                            <Link2 className="h-4 w-4" />
                        ) : (
                            <ExternalLink className="h-4 w-4" />
                        )
                    ) : (
                        <Icon className="h-4 w-4" />
                    )}
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
                    {!expired && hasFileKey(op) && (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            onClick={() => onPreview(staged)}
                            aria-label={t("preview")}
                        >
                            <Eye className="h-3.5 w-3.5" />
                        </Button>
                    )}
                    {isQcm ? (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            asChild
                            aria-label={tCommon("edit")}
                        >
                            <Link href={qcmEditHref(op, staged.id)} onClick={() => setReviewOpen(false)}>
                                <FilePenLine className="h-3.5 w-3.5" />
                            </Link>
                        </Button>
                    ) : canEditDetails ? (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-primary"
                            onClick={() => onEdit(staged.id)}
                            disabled={disabled}
                            aria-label={tCommon("edit")}
                        >
                            <FilePenLine className="h-3.5 w-3.5" />
                        </Button>
                    ) : null}
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => onRemove(staged.id)}
                        disabled={disabled}
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
    const tAuto = useTranslations("AutoTitle");
    const operations = useStagingStore((s) => s.operations);
    const reviewOpen = useStagingStore((s) => s.reviewOpen);
    const setReviewOpen = useStagingStore((s) => s.setReviewOpen);
    const removeOperation = useStagingStore((s) => s.removeOperation);
    const removeOperations = useStagingStore((s) => s.removeOperations);
    const clearOperations = useStagingStore((s) => s.clearOperations);
    const purgeExpired = useStagingStore((s) => s.purgeExpired);

    // Title and note follow the draft until the user writes their own.
    const [manualTitle, setManualTitle] = useState<string | null>(null);
    const [manualDescription, setManualDescription] = useState<string | null>(null);
    const [autoDescription, setAutoDescription] = useState("");
    const [isAnonymous, setIsAnonymous] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);

    // Preview state
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [previewMime, setPreviewMime] = useState<string | undefined>();
    const [previewName, setPreviewName] = useState<string | undefined>();

    const ops = operations.map((entry) => entry.operation);
    const title = manualTitle ?? (ops.length > 0 ? autoTitle(ops, tAuto) : "");
    const description = manualDescription ?? autoDescription;

    const resetForm = () => {
        setManualTitle(null);
        setManualDescription(null);
        setAutoDescription("");
        setIsAnonymous(false);
    };

    const isEmpty = operations.length === 0;
    useEffect(() => {
        if (isEmpty) resetForm();
    }, [isEmpty]);

    const wantsAutoDescription = reviewOpen && !isEmpty && manualDescription === null;
    useEffect(() => {
        if (!wantsAutoDescription) return;
        let cancelled = false;
        describeOperationPaths(
            operations.map((entry) => entry.operation),
            t("item"),
        ).then((text) => {
            if (!cancelled) setAutoDescription(text);
        });
        return () => {
            cancelled = true;
        };
    }, [wantsAutoDescription, operations, t]);

    const closePreview = () => {
        if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
    };

    const handlePreview = async ({ operation: op }: StagedOperation) => {
        if (!hasFileKey(op) || !op.file_key) return;
        const name = operationName(op) || op.file_name || undefined;

        try {
            let url: string;
            if (op.file_mime_type === MIME_QCM) {
                const qcm = await fetchStagedQcm(op.file_key);
                url = URL.createObjectURL(new Blob([JSON.stringify(qcm)], { type: "application/json" }));
            } else {
                const res = await apiFetch<{ url: string }>(`/upload/preview?file_key=${encodeURIComponent(op.file_key)}`);
                url = res.url;
            }
            setPreviewUrl(url);
            setPreviewName(name);
            setPreviewMime(op.file_mime_type || undefined);
        } catch (e) {
            toast.error((e as Error).message || t("unableToPreview"));
        }
    };

    const { user } = useAuth();
    const config = useConfigStore((s) => s.config);
    const maxDescriptionLength = config?.max_contribution_note_length ?? 10000;
    const maxOps = isStaff(user)
        ? (config?.max_contribution_ops_staff ?? 500)
        : (config?.max_contribution_ops_student ?? 50);
    const overLimit = operations.length > maxOps;

    const expiredCount = operations.filter(isExpired).length;
    const expiringSoonCount = operations.filter(isExpiringSoon).length;
    const hasExpired = expiredCount > 0;

    const canSubmit =
        title.trim().length >= 3 && operations.length > 0 && !submitting && !hasExpired && !overLimit;

    const handleSubmit = async () => {
        if (!canSubmit) return;
        setSubmitting(true);
        const submitted = operations;
        const result = await submitDirectOperations(
            submitted.map((entry) => entry.operation),
            title,
            description,
            tAuto,
            isAnonymous,
        );
        setSubmitting(false);

        if (result) {
            // Only what was sent: another tab may have added to the draft meanwhile.
            removeOperations(submitted.map((entry) => entry.id));
            resetForm();
            setReviewOpen(false);
        }
    };

    const handleClear = () => {
        clearOperations();
        resetForm();
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
            <StagedItemEditDialog key={editingId} stagedId={editingId} onClose={() => setEditingId(null)} />
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
                            {operations.map((staged) => (
                                <OperationCard
                                    key={staged.id}
                                    staged={staged}
                                    disabled={submitting}
                                    onRemove={removeOperation}
                                    onEdit={setEditingId}
                                    onPreview={handlePreview}
                                />
                            ))}
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
                                onChange={(e) => setManualTitle(e.target.value)}
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
                                onChange={(e) => setManualDescription(e.target.value)}
                                maxLength={maxDescriptionLength}
                                rows={4}
                            />
                        </div>
                        <div className="flex items-start justify-between gap-3 rounded-md border p-3">
                            <label htmlFor="pr-anonymous" className="space-y-0.5">
                                <span className="block text-sm font-medium">
                                    {t("submitAnonymously")}
                                </span>
                                <span className="block text-xs text-muted-foreground">
                                    {t("submitAnonymouslyHint")}
                                </span>
                            </label>
                            <Switch
                                id="pr-anonymous"
                                checked={isAnonymous}
                                onCheckedChange={setIsAnonymous}
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
