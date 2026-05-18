"use client";

import { use, useEffect, useState, useMemo } from "react";
import { apiFetch } from "@/lib/api-client";
import {
    Loader2,
    ArrowLeft,
    FilePlus,
    FilePenLine,
    FileX,
    FolderPlus,
    FolderPen,
    FolderX,
    ArrowRightLeft,
    CheckCircle2,
    XCircle,
    Check,
    X,
    Eye,
    ExternalLink,
    ChevronDown,
    Clock,
    ChevronsDownUp,
    ChevronsUpDown,
    AlertCircle,
    Image as ImageIcon,
    FileText,
    Video,
    MapPin,
    ArrowRight,
    Inbox,
    Undo2,
    ShieldAlert,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { PreviewDialog } from "@/components/pr/preview-dialog";
import { MarkdownRenderer } from "@/components/viewers/markdown-renderer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
    Accordion,
    AccordionContent,
    AccordionItem,
} from "@/components/ui/accordion";
import { Accordion as AccordionPrimitive } from "radix-ui";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { formatDistanceToNow } from "date-fns";
import { PRComments } from "@/components/pr/pr-comments";
import { ExpandableText } from "@/components/ui/expandable-text";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores";
import { toast } from "sonner";
import { useTranslations, useLocale } from "next-intl";
import { fr, enUS } from "date-fns/locale";
import { type Operation } from "@/lib/staging-store";

/* ── Types ──────────────────────────────────────────── */

type PullRequestOperation = Operation & {
    result_browse_path?: string | null;
    /** Legacy or extra fields from backend */
    pr_type?: string;
    target_title?: string;
    target_name?: string;
    // Common fields for easy access during rendering
    title?: string;
    name?: string;
    directory_id?: string | null;
    material_id?: string;
    parent_id?: string | null;
    target_id?: string;
    target_type?: string;
    new_parent_id?: string | null;
    file_key?: string | null;
    file_name?: string | null;
    file_mime_type?: string | null;
    diff_summary?: string | null;
};

interface PullRequestDetail {
    id: string;
    type: string;
    status: string;
    title: string;
    description: string | null;
    rejection_reason: string | null;
    author: { id: string; display_name: string } | null;
    created_at: string;
    updated_at: string;
    payload: PullRequestOperation[] | PullRequestOperation;
    applied_result?: PullRequestOperation[] | null;
    summary_types?: string[];
    virus_scan_result?: string;
    approved_at?: string | null;
    reverts_pr_id?: string | null;
    reverted_by_pr_id?: string | null;
    revert_grace_expires_at?: string | null;
    can_revert?: boolean;
}

/* ── Constants ──────────────────────────────────────── */

const OP_ICONS: Record<string, React.ElementType> = {
    create_material: FilePlus,
    edit_material: FilePenLine,
    delete_material: FileX,
    create_directory: FolderPlus,
    edit_directory: FolderPen,
    delete_directory: FolderX,
    move_item: ArrowRightLeft,
};

const OP_COLORS: Record<string, string> = {
    create_material: "text-green-600 bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800",
    edit_material: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800",
    delete_material: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800",
    create_directory: "text-green-600 bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800",
    edit_directory: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800",
    delete_directory: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800",
    move_item: "text-amber-600 bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800",
};

const OP_LABELS_KEYS: Record<string, string> = {
    create_material: "labels.create_material",
    edit_material: "labels.edit_material",
    delete_material: "labels.delete_material",
    create_directory: "labels.create_directory",
    edit_directory: "labels.edit_directory",
    delete_directory: "labels.delete_directory",
    move_item: "labels.move_item",
};

/** Fields worth displaying in the detail view (everything else is noise). */
const VISIBLE_FIELDS = new Set(["type", "tags", "description"]);

// FRIENDLY_TYPES is now handled by mt() translation hook

const STATUS_CONFIG_KEYS: Record<
    string,
    { Icon: React.ElementType; color: string; bg: string; labelKey: string }
> = {
    open: {
        Icon: Inbox,
        color: "text-blue-600",
        bg: "bg-blue-500/10",
        labelKey: "status.open",
    },
    approved: {
        Icon: CheckCircle2,
        color: "text-green-600",
        bg: "bg-green-500/10",
        labelKey: "status.approved",
    },
    rejected: {
        Icon: XCircle,
        color: "text-red-600",
        bg: "bg-red-500/10",
        labelKey: "status.rejected",
    },
    cancelled: {
        Icon: XCircle,
        color: "text-muted-foreground",
        bg: "bg-muted",
        labelKey: "status.cancelled",
    },
};

/* ── Types ───────────────────────────────────────────── */

interface ResolvedItemDetails {
    /** Display name of the item being moved or deleted */
    itemName?: string;
    /** Human-readable path of the item's current location */
    sourcePath?: string;
    /** Browse URL for the source location */
    sourceUrl?: string;
    /** Human-readable destination path (move only) */
    destPath?: string;
    /** Browse URL for the destination (move only) */
    destUrl?: string;
    /** Material ID for fetching inline preview */
    materialId?: string;
    /** MIME type for preview */
    mimeType?: string;
    /** File name for preview dialog */
    fileName?: string;
}

/* ── Helpers ─────────────────────────────────────────── */

function opSummary(op: PullRequestOperation, t: (key: any, values?: any) => string): string {
    const rawOp = op as unknown as Record<string, unknown>;
    const opType = String(rawOp.op || rawOp.pr_type || "unknown");
    const name = (rawOp.title || rawOp.name) as string | undefined;
    const finalName = name || (opType.includes("directory") ? t("labels.create_directory") : t("labels.create_material")); // fallbacks

    switch (opType) {
        case "create_material":
            return t("summary.create_material", { name: finalName });
        case "edit_material":
            return t("summary.edit_material", { name: finalName });
        case "delete_material":
            return t("summary.delete_material", { name: finalName });
        case "create_directory":
            return t("summary.create_directory", { name: finalName });
        case "edit_directory":
            return t("summary.edit_directory", { name: finalName });
        case "delete_directory":
            return t("summary.delete_directory", { name: finalName });
        case "move_item":
            const isDir = rawOp.target_type === "directory";
            return t("summary.move_item", { isDir, name: finalName });
        default:
            return t("summary.fallback", { op: opType, name: finalName });
    }
}

function formatValue(value: unknown, mt: (key: any) => string): React.ReactNode {
    if (Array.isArray(value)) {
        if (value.length === 0) return <span className="text-muted-foreground">—</span>;
        return (
            <div className="flex flex-wrap gap-1">
                {value.map((v, i) => (
                    <Badge
                        key={i}
                        variant="secondary"
                        className="text-xs font-normal"
                    >
                        {String(v)}
                    </Badge>
                ))}
            </div>
        );
    }
    // Translate known type values
    const str = String(value);
    return mt(str as any) !== str ? mt(str as any) : str;
}

/** Resolve the browse URL and label for a directory ID via the /path endpoint. */
async function resolveTargetPath(directoryId: string, tRoot: string): Promise<{ url: string; label: string }> {
    try {
        const path = await apiFetch<{ name: string; slug: string }[]>(
            `/directories/${directoryId}/path`,
        );
        if (path.length === 0) return { url: "/browse", label: tRoot };
        const slugs = path.map((p) => p.slug).join("/");
        const label = path.map((p) => p.name).join(" › ");
        return { url: `/browse/${slugs}`, label };
    } catch {
        return { url: "/browse", label: tRoot };
    }
}

function getInitials(name: string): string {
    return name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase();
}

/* ── Inline preview dialog ───────────────────────────── */


/* ── OperationRow ────────────────────────────────────── */

function OperationRow({
    op,
    prId,
    prStatus,
    index,
}: {
    op: PullRequestOperation;
    prId: string;
    prStatus: string;
    index: number;
}) {
    const t = useTranslations("PRDetails");
    const mt = useTranslations("MaterialTypes");
    const rawOp = op as unknown as Record<string, unknown>;
    const [targetInfo, setTargetInfo] = useState<{ url: string; label: string } | null>(null);
    const [itemDetails, setItemDetails] = useState<ResolvedItemDetails | null>(null);
    const [existingPreview, setExistingPreview] = useState<{ url: string; mimeType?: string; fileName?: string } | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    const opType = String(rawOp.op || rawOp.pr_type || "unknown");
    const Icon = OP_ICONS[opType] ?? FilePlus;
    const colorClass = OP_COLORS[opType] ?? "";
    const hasFile = Boolean(rawOp.file_key);
    const isApproved = prStatus === "approved";

    // After approval: result_browse_path is already in the op
    const resultBrowsePath = (() => {
        if (!isApproved) return null;
        if (rawOp.result_browse_path !== undefined && rawOp.result_browse_path !== null) {
            const path = String(rawOp.result_browse_path);
            return path ? `/browse/${path}` : "/browse";
        }
        // Fallback for older PRs or if path wasn't captured: use resolved details
        if (itemDetails?.sourceUrl && itemDetails?.itemName) {
            if (opType.includes("material")) {
                return `${itemDetails.sourceUrl}/${itemDetails.itemName}`;
            }
            return itemDetails.sourceUrl;
        }
        return null;
    })();

    const needsItemResolution =
        opType === "delete_material" ||
        opType === "delete_directory" ||
        opType === "move_item" ||
        opType === "edit_material" ||
        opType === "edit_directory";

    // Resolve target path for create/edit ops (shows location context)
    useEffect(() => {
        if (needsItemResolution) return; // handled by the other effect
        if (isApproved) return;

        let cancelled = false;
        async function fetchInfo() {
            let dirId = "";
            if (rawOp.directory_id) dirId = String(rawOp.directory_id);
            else if (rawOp.parent_id) dirId = String(rawOp.parent_id);

            const matId = String(rawOp.material_id ?? "");

            if (!dirId && matId && !matId.startsWith("$")) {
                try {
                    const mat = await apiFetch<{ directory_id: string | null }>(`/materials/${matId}`);
                    if (mat.directory_id) dirId = mat.directory_id;
                } catch { /* ignore */ }
            }

            if (dirId && !dirId.startsWith("$") && !cancelled) {
                const info = await resolveTargetPath(dirId, t("root"));
                if (!cancelled) setTargetInfo(info);
            }
        }

        fetchInfo();
        return () => { cancelled = true; };
    }, [rawOp.directory_id, rawOp.parent_id, rawOp.material_id, isApproved, needsItemResolution, t]);

    // Resolve item name + paths for delete/move ops
    useEffect(() => {
        if (!needsItemResolution) return;

        const matId = String(rawOp.material_id ?? "");
        const dirId = String(rawOp.directory_id ?? "");
        const targetId = String(rawOp.target_id ?? "");

        // Don't try to resolve temp IDs
        if (matId.startsWith("$") || dirId.startsWith("$") || targetId.startsWith("$")) return;

        let cancelled = false;

        async function resolveDetails() {
            try {
                if (opType === "delete_material") {
                    if (!matId) return;
                    const mat = await apiFetch<{
                        title: string;
                        directory_id: string | null;
                        current_version_info?: { file_mime_type?: string; file_name?: string } | null;
                    }>(`/materials/${matId}`);
                    if (cancelled) return;
                    let sourcePath: string | undefined;
                    let sourceUrl: string | undefined;
                    if (mat.directory_id) {
                        const info = await resolveTargetPath(mat.directory_id, t("root"));
                        if (!cancelled) { sourcePath = info.label; sourceUrl = info.url; }
                    } else {
                        sourcePath = t("root");
                        sourceUrl = "/browse";
                    }
                    if (!cancelled) setItemDetails({
                        itemName: mat.title,
                        sourcePath,
                        sourceUrl,
                        materialId: matId,
                        mimeType: mat.current_version_info?.file_mime_type ?? undefined,
                        fileName: mat.current_version_info?.file_name ?? undefined,
                    });

                } else if (opType === "delete_directory") {
                    if (!dirId) return;
                    const path = await apiFetch<{ name: string; slug: string }[]>(`/directories/${dirId}/path`);
                    if (cancelled) return;
                    if (path.length === 0) { setItemDetails({ itemName: t("root") }); return; }
                    const itemName = path[path.length - 1].name;
                    const parentSegs = path.slice(0, -1);
                    const sourcePath = parentSegs.length > 0
                        ? parentSegs.map((p) => p.name).join(" › ")
                        : "Root";
                    const parentSlugs = parentSegs.map((p) => p.slug).join("/");
                    if (!cancelled) setItemDetails({
                        itemName,
                        sourcePath,
                        sourceUrl: parentSlugs ? `/browse/${parentSlugs}` : "/browse",
                    });

                } else if (opType === "move_item") {
                    if (!targetId) return;
                    const targetType = String(rawOp.target_type ?? "");
                    let itemName: string | undefined;
                    let sourcePath: string | undefined;
                    let sourceUrl: string | undefined;
                    let materialId: string | undefined;
                    let mimeType: string | undefined;
                    let fileName: string | undefined;

                    if (targetType === "material") {
                        const mat = await apiFetch<{
                            title: string;
                            directory_id: string | null;
                            current_version_info?: { file_mime_type?: string; file_name?: string } | null;
                        }>(`/materials/${targetId}`);
                        if (cancelled) return;
                        itemName = mat.title;
                        materialId = targetId;
                        mimeType = mat.current_version_info?.file_mime_type ?? undefined;
                        fileName = mat.current_version_info?.file_name ?? undefined;
                        if (mat.directory_id) {
                            const info = await resolveTargetPath(mat.directory_id, t("root"));
                            if (!cancelled) { sourcePath = info.label; sourceUrl = info.url; }
                        } else {
                            sourcePath = t("root");
                            sourceUrl = "/browse";
                        }
                    } else {
                        // directory
                        const path = await apiFetch<{ name: string; slug: string }[]>(`/directories/${targetId}/path`);
                        if (cancelled) return;
                        if (path.length > 0) {
                            itemName = path[path.length - 1].name;
                            const parentSegs = path.slice(0, -1);
                            sourcePath = parentSegs.length > 0
                                ? parentSegs.map((p) => p.name).join(" › ")
                                : t("root");
                            const parentSlugs = parentSegs.map((p) => p.slug).join("/");
                            sourceUrl = parentSlugs ? `/browse/${parentSlugs}` : "/browse";
                        }
                    }

                    // Resolve destination
                    const newParentId = rawOp.new_parent_id ? String(rawOp.new_parent_id) : null;
                    let destPath = t("root");
                    let destUrl = "/browse";
                    if (newParentId && !newParentId.startsWith("$")) {
                        const info = await resolveTargetPath(newParentId, t("root"));
                        if (!cancelled) { destPath = info.label; destUrl = info.url; }
                    }

                    if (!cancelled) setItemDetails({ itemName, sourcePath, sourceUrl, destPath, destUrl, materialId, mimeType, fileName });
                } else if (opType === "edit_material") {
                    if (!matId) return;
                    const mat = await apiFetch<{
                        title: string;
                        directory_id: string | null;
                        current_version_info?: { file_mime_type?: string; file_name?: string } | null;
                    }>(`/materials/${matId}`);
                    if (cancelled) return;
                    let sourcePath: string | undefined;
                    let sourceUrl: string | undefined;
                    if (mat.directory_id) {
                        const info = await resolveTargetPath(mat.directory_id, t("root"));
                        if (!cancelled) { sourcePath = info.label; sourceUrl = info.url; }
                    } else {
                        sourcePath = t("root");
                        sourceUrl = "/browse";
                    }
                    if (!cancelled) setItemDetails({
                        itemName: mat.title,
                        sourcePath,
                        sourceUrl,
                        materialId: matId,
                        mimeType: mat.current_version_info?.file_mime_type ?? undefined,
                        fileName: mat.current_version_info?.file_name ?? undefined,
                    });
                } else if (opType === "edit_directory") {
                    if (!dirId) return;
                    const path = await apiFetch<{ name: string; slug: string }[]>(`/directories/${dirId}/path`);
                    if (cancelled) return;
                    const itemName = path.length > 0 ? path[path.length - 1].name : t("root");
                    const parentSegs = path.slice(0, -1);
                    const sourcePath = parentSegs.length > 0
                        ? parentSegs.map((p) => p.name).join(" › ")
                        : t("root");
                    const parentSlugs = parentSegs.map((p) => p.slug).join("/");
                    if (!cancelled) setItemDetails({
                        itemName,
                        sourcePath,
                        sourceUrl: parentSlugs ? `/browse/${parentSlugs}` : "/browse",
                    });
                }
            } catch { /* Silently ignore — item may not exist (e.g., deleted after approval) */ }
        }

        resolveDetails();
        return () => { cancelled = true; };
    }, [opType, rawOp.material_id, rawOp.directory_id, rawOp.target_id, rawOp.new_parent_id, rawOp.target_type, needsItemResolution]);

    // Fetch inline preview URL for an existing material
    const handleExistingPreview = async () => {
        const matId = itemDetails?.materialId;
        if (!matId) return;
        setPreviewLoading(true);
        try {
            const res = await apiFetch<{ url: string }>(`/materials/${matId}/inline`);
            setExistingPreview({
                url: res.url,
                mimeType: itemDetails?.mimeType,
                fileName: itemDetails?.fileName,
            });
        } catch {
            // ignore
        } finally {
            setPreviewLoading(false);
        }
    };

    // Derive summary text using resolved item name when available
    const displaySummary = (() => {
        const name = itemDetails?.itemName;
        const opName = (rawOp.title || rawOp.name) as string | undefined;
        const finalName = name || opName || (opType.includes("directory") ? t("labels.create_directory") : t("labels.create_material"));

        switch (opType) {
            case "delete_material": return t("summary.delete_material", { name: finalName });
            case "delete_directory": return t("summary.delete_directory", { name: finalName });
            case "edit_material":   return t("summary.edit_material", { name: finalName });
            case "edit_directory":   return t("summary.edit_directory", { name: finalName });
            case "move_item":
                const isDir = rawOp.target_type === "directory";
                return t("summary.move_item", { isDir: String(isDir), name: finalName });
            default: return opSummary(op, t);
        }
    })();

    // Visible metadata (type, tags, description only)
    const entries = Object.entries(op).filter(
        ([k, v]) => VISIBLE_FIELDS.has(k) && v !== null && v !== undefined,
    );

    const diffSummary = "diff_summary" in op ? (op as unknown as Record<string, unknown>).diff_summary : null;
    const hasDiff = Boolean(diffSummary);

    const canPreviewExisting = Boolean(itemDetails?.materialId) && !previewLoading;

    return (
        <>
            {existingPreview && (
                <PreviewDialog
                    url={existingPreview.url}
                    mimeType={existingPreview.mimeType}
                    fileName={existingPreview.fileName}
                    onClose={() => setExistingPreview(null)}
                />
            )}
            <AccordionItem
                value={`op-${index}`}
                className="border-b last:border-0"
            >
                {/* Row: trigger + action buttons side by side */}
                <AccordionPrimitive.Header className="flex items-center">
                    <AccordionPrimitive.Trigger
                        className="flex flex-1 items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/40 [&[data-state=open]>svg.chevron]:rotate-180 min-w-0"
                    >
                        <div
                            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md border ${colorClass}`}
                        >
                            <Icon className="h-3.5 w-3.5" />
                        </div>
                        <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium">
                                {displaySummary}
                            </p>
                            {/* Subtitle: location context */}
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                {/* Hide operation label if it's already in the summary (most cases now) */}
                                {(opType === "move_item" || opType.includes("delete")) ? null : (
                                    <span className={colorClass.split(" ")[0]}>{t(OP_LABELS_KEYS[opType] as any) ?? opType}</span>
                                )}

                                {/* Create: show target directory */}
                                {opType.startsWith("create_") && targetInfo && (
                                    <>
                                        {!opType.includes("delete") && opType !== "move_item" && <span className="opacity-40">·</span>}
                                        <div className="flex items-center gap-1 max-w-[200px]">
                                            <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                            <span className="truncate">{targetInfo.label}</span>
                                        </div>
                                    </>
                                )}

                                {/* Edit/Delete: show current path */}
                                {(opType.startsWith("edit_") || opType.startsWith("delete_")) && itemDetails?.sourcePath && (
                                    <>
                                        {opType.startsWith("edit_") && <span className="opacity-40">·</span>}
                                        <div className="flex items-center gap-1 max-w-[200px]">
                                            <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                            <span className="truncate">{itemDetails.sourcePath}</span>
                                        </div>
                                    </>
                                )}

                                {/* Move: show from → to */}
                                {opType === "move_item" && itemDetails && (
                                    <>
                                        {itemDetails.sourcePath && (
                                            <div className="flex items-center gap-1 shrink-0">
                                                <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                                <span className="truncate max-w-[120px]">{itemDetails.sourcePath}</span>
                                            </div>
                                        )}
                                        <ArrowRight className="h-3 w-3 shrink-0 opacity-60" />
                                        <div className="flex items-center gap-1 shrink-0">
                                            <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                            <span className="truncate max-w-[120px]">
                                                {itemDetails.destPath ?? t("root")}
                                            </span>
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>
                        <ChevronDown className="chevron h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200" />
                    </AccordionPrimitive.Trigger>

                    {/* Action buttons — outside the trigger */}
                    <div
                        className="flex shrink-0 items-center gap-1.5 pr-4"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Approved: show direct library link if available */}
                        {isApproved && resultBrowsePath && !opType.includes("delete") && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1.5 text-xs text-primary font-medium"
                                asChild
                            >
                                <Link href={resultBrowsePath}>
                                    <Eye className="h-3.5 w-3.5" />
                                    {t("preview")}
                                </Link>
                            </Button>
                        )}

                        {/* Fallback for approved PRs without browse path (e.g. root items before fix or older PRs) */}
                        {isApproved && !resultBrowsePath && canPreviewExisting && !opType.includes("delete") && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1.5 text-xs text-primary"
                                onClick={handleExistingPreview}
                                disabled={previewLoading}
                            >
                                {previewLoading
                                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    : <Eye className="h-3.5 w-3.5" />}
                                {t("preview")}
                            </Button>
                        )}

                        {/* Open PR Case: Single Preview Button */}
                        {!isApproved && !opType.includes("delete") && (
                            <>
                                {hasFile ? (
                                    /* Prioritize NEW file preview if uploaded */
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 gap-1.5 text-xs"
                                        asChild
                                    >
                                        <Link href={`/pull-requests/${prId}/preview/${index}`}>
                                            <Eye className="h-3.5 w-3.5" />
                                            {t("preview")}
                                        </Link>
                                    </Button>
                                ) : canPreviewExisting ? (
                                    /* Otherwise preview EXISTING material if available (e.g. metadata-only edit) */
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 gap-1.5 text-xs"
                                        onClick={handleExistingPreview}
                                        disabled={previewLoading}
                                    >
                                        {previewLoading
                                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            : <Eye className="h-3.5 w-3.5" />}
                                        {t("preview")}
                                    </Button>
                                ) : null}
                            </>
                        )}
                    </div>
                </AccordionPrimitive.Header>

                {/* Expandable metadata & Diffs */}
                {(entries.length > 0 || hasDiff) && (
                    <AccordionContent className="px-4 pb-4">
                        {entries.length > 0 && (
                            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
                                {entries.map(([k, v]) => (
                                    <div key={k} className="contents">
                                        <dt className="py-0.5 capitalize text-muted-foreground">
                                            {t(`fields.${k}` as any)}
                                        </dt>
                                        <dd className="py-0.5 min-w-0">
                                            {k === "description" ? (
                                                <ExpandableText 
                                                    text={String(v)} 
                                                    clampedLines={2}
                                                    className="text-sm"
                                                />
                                            ) : (
                                                formatValue(v, mt)
                                            )}
                                        </dd>
                                    </div>
                                ))}
                            </dl>
                        )}
                        {hasDiff && (
                            <div className={entries.length > 0 ? "mt-4 pt-4 border-t" : ""}>
                                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
                                    <Clock className="h-3 w-3" />
                                    {t("changes")}
                                </div>
                                <MarkdownRenderer 
                                    content={String(diffSummary)} 
                                    className="prose prose-xs dark:prose-invert max-w-none 
                                        prose-pre:p-0 prose-pre:bg-transparent prose-pre:border-0
                                        [&_pre]:m-0 [&_code]:text-[11px] [&_code]:leading-relaxed [&_code]:bg-muted/30 [&_code]:p-3 [&_code]:block [&_code]:rounded-md"
                                />
                            </div>
                        )}
                    </AccordionContent>
                )}
            </AccordionItem>
        </>
    );
}

/* ── Main Page ──────────────────────────────────────── */

interface PRDetailPageProps {
    params: Promise<{ id: string }>;
}

export default function PRDetailPage({ params }: PRDetailPageProps) {
    const { id } = use(params);
    const t = useTranslations("PRDetails");
    const tPRs = useTranslations("PRs");
    const tCommon = useTranslations("Common");
    const locale = useLocale();
    const dateLocale = locale === "fr" ? fr : enUS;
    const { user } = useAuthStore();
    const [pr, setPr] = useState<PullRequestDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [acting, setActing] = useState<"approve" | "reject" | "cancel" | "revert" | null>(null);
    const [showRejectDialog, setShowRejectDialog] = useState(false);
    const [showCancelDialog, setShowCancelDialog] = useState(false);
    const [rejectReason, setRejectReason] = useState("");
    const [showRevertDialog, setShowRevertDialog] = useState(false);
    const [revertConfirmText, setRevertConfirmText] = useState("");
    const [revertUnderstood, setRevertUnderstood] = useState(false);
    const [expandedItems, setExpandedItems] = useState<string[]>([]);
    const [previewDirId, setPreviewDirId] = useState<string | null>(null);
    const [previewPath, setPreviewPath] = useState<string | null>(null);

    // Use applied_result for approved PRs (contains result_browse_path); fall back to payload
    const operations: PullRequestOperation[] = useMemo(() => {
        if (!pr) return [];
        return pr.status === "approved" && Array.isArray(pr.applied_result) && pr.applied_result.length > 0
            ? pr.applied_result
            : Array.isArray(pr.payload)
                ? pr.payload
                : [pr.payload] as PullRequestOperation[];
    }, [pr]);

    useEffect(() => {
        if (!pr) return;
        // Find the first operation that points to a real directory
        const findDir = async () => {
            for (const op of operations) {
                const rawOp = op as unknown as Record<string, unknown>;
                const dirId = (rawOp.directory_id ?? rawOp.parent_id) as string | undefined;
                if (dirId && typeof dirId === "string" && !dirId.startsWith("$")) {
                    setPreviewDirId(dirId);
                    return;
                }
            }
        };
        findDir();
    }, [pr, operations]);

    useEffect(() => {
        if (previewDirId) {
            resolveTargetPath(previewDirId, t("root")).then(info => setPreviewPath(info.url));
        }
    }, [previewDirId, t]);

    const allItemValues = useMemo(() => operations.map((_, i) => `op-${i}`), [operations]);
    const allExpanded = useMemo(() => expandedItems.length === allItemValues.length, [expandedItems, allItemValues]);

    useEffect(() => {
        let active = true;
        setLoading(true);
        apiFetch<PullRequestDetail>(`/pull-requests/${id}`)
            .then((data) => {
                if (active) setPr(data);
            })
            .catch(console.error)
            .finally(() => {
                if (active) setLoading(false);
            });
        return () => {
            active = false;
        };
    }, [id]);

    const handleApprove = async () => {
        setActing("approve");
        try {
            await apiFetch(`/pull-requests/${id}/approve`, { method: "POST" });
            setPr((prev) => prev ? { ...prev, status: "approved" } : prev);
            toast.success(t("publishedToast"));
        } catch (e) {
            toast.error(e instanceof Error ? e.message : t("publishFailed"));
        } finally {
            setActing(null);
        }
    };

    const handleReject = async () => {
        if (rejectReason.trim().length < 10) return;
        setActing("reject");
        setShowRejectDialog(false);
        try {
            await apiFetch(`/pull-requests/${id}/reject`, {
                method: "POST",
                body: JSON.stringify({ reason: rejectReason.trim() }),
            });
            setPr((prev) => prev ? { ...prev, status: "rejected", rejection_reason: rejectReason.trim() } : prev);
            setRejectReason("");
            toast(t("rejectedToast"));
        } catch (e) {
            toast.error(e instanceof Error ? e.message : t("rejectFailed"));
        } finally {
            setActing(null);
        }
    };

    const handleCancel = async () => {
        setActing("cancel");
        setShowCancelDialog(false);
        try {
            await apiFetch(`/pull-requests/${id}/cancel`, { method: "POST" });
            setPr((prev) => prev ? { ...prev, status: "cancelled" } : prev);
            toast.success(t("cancelledToast"));
        } catch (e) {
            toast.error(e instanceof Error ? e.message : t("cancelFailed"));
        } finally {
            setActing(null);
        }
    };

    const handleRevert = async () => {
        setActing("revert");
        setShowRevertDialog(false);
        try {
            const revertPr = await apiFetch<PullRequestDetail>(`/pull-requests/${id}/revert`, { method: "POST" });
            setPr((prev) => prev ? { ...prev, reverted_by_pr_id: revertPr.id, can_revert: false } : prev);
            setRevertConfirmText("");
            setRevertUnderstood(false);
            toast.success(t("revertedToast"));
        } catch (e) {
            toast.error(e instanceof Error ? e.message : t("revertFailed"));
        } finally {
            setActing(null);
        }
    };

    /* Loading */
    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    /* Not found */
    if (!pr) {
        return (
            <div className="flex flex-col items-center gap-3 py-20 text-muted-foreground">
                <XCircle className="h-10 w-10" />
                <p className="text-sm">{t("notFound")}</p>
                <Button variant="ghost" size="sm" asChild>
                    <Link href="/pull-requests">← {t("backToList")}</Link>
                </Button>
            </div>
        );
    }

    const typeCounts: Record<string, number> = {};
    for (const op of operations) {
        const rawOp = op as unknown as Record<string, unknown>;
        const t = String(rawOp.op ?? rawOp.pr_type ?? "unknown");
        typeCounts[t] = (typeCounts[t] || 0) + 1;
    }

    const isModerator =
        user?.role === "moderator" ||
        user?.role === "bureau" ||
        user?.role === "vieux";
    const isAdmin = user?.role === "bureau" || user?.role === "vieux";
    const isAuthor = !!user && !!pr.author?.id && pr.author.id === user.id;
    const canCancel = pr.status === "open" && isAuthor;
    const canRevert = isAdmin && pr.can_revert === true && !pr.reverted_by_pr_id;
    const status = STATUS_CONFIG_KEYS[pr.status] ?? STATUS_CONFIG_KEYS.open;
    const StatusIcon = status.Icon;

    const initials = pr.author?.display_name
        ? getInitials(pr.author.display_name)
        : "?";

    // Expiration calculation
    const updatedDate = new Date(pr.updated_at);
    const expiresDate = new Date(updatedDate.getTime() + 7 * 24 * 60 * 60 * 1000);
    const isExpiringSoon = pr.status === "open" && (expiresDate.getTime() - Date.now() < 24 * 60 * 60 * 1000);

    const isApproved = pr.status === "approved";
    const previewUrl = isApproved
        ? (previewPath || "/browse")
        : (previewPath ? `${previewPath}?preview_pr=${id}` : `/browse?preview_pr=${id}`);

    return (
        <div className="container mx-auto max-w-4xl space-y-6 px-4 py-6 pb-20 md:pb-6">
            {/* Back link */}
            <Link
                href="/pull-requests"
                className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
                <ArrowLeft className="h-3.5 w-3.5" />
                {tPRs("contributions")}
            </Link>

            {/* ─── Reverted banner ──────────────────────── */}
            {pr.reverted_by_pr_id && (
                <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/20">
                    <Undo2 className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                    <div>
                        <p className="text-sm font-medium text-amber-700 dark:text-amber-400">
                            {t("revertedBanner")}
                        </p>
                        <p className="mt-1 text-sm text-amber-600/90 dark:text-amber-400/80">
                            {t("revertedBannerDesc")}{" "}
                            <Link
                                href={`/pull-requests/${pr.reverted_by_pr_id}`}
                                className="underline hover:text-amber-700 dark:hover:text-amber-300"
                            >
                                {t("viewRevertPR")}
                            </Link>
                        </p>
                    </div>
                </div>
            )}

            {/* ─── Revert-of banner ──────────────────────── */}
            {pr.reverts_pr_id && (
                <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/20">
                    <Undo2 className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                    <div>
                        <p className="text-sm font-medium text-blue-700 dark:text-blue-400">
                            {t("revertOfBanner")}
                        </p>
                        <p className="mt-1 text-sm text-blue-600/90 dark:text-blue-400/80">
                            {t.rich("revertOfBannerDesc", {
                                link: (chunks) => (
                                    <Link
                                        href={`/pull-requests/${pr.reverts_pr_id}`}
                                        className="underline hover:text-blue-700 dark:hover:text-blue-300"
                                    >
                                        {t("originalPR")}
                                    </Link>
                                ),
                            })}
                        </p>
                    </div>
                </div>
            )}

            {/* ─── Rejection reason banner ─────────────── */}
            {pr.status === "rejected" && pr.rejection_reason && (
                <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-950/20">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                    <div>
                        <p className="text-sm font-medium text-red-700 dark:text-red-400">
                            {t("rejectionReasonBanner")}
                        </p>
                        <p className="mt-1 text-sm text-red-600/90 dark:text-red-400/80">
                            {pr.rejection_reason}
                        </p>
                    </div>
                </div>
            )}

            {/* ─── Header ─────────────────────────────── */}
            <div className="rounded-lg border bg-card shadow-sm">
                <div className="space-y-4 p-6">
                    <div className="flex items-start justify-between gap-4">
                        <div className="space-y-4 flex-1 min-w-0">
                            {/* Status row */}
                            <div className="flex items-center gap-2 flex-wrap">
                                <span
                                    className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${status.color} ${status.bg}`}
                                >
                                    <StatusIcon className="h-3.5 w-3.5" />
                                    {t(status.labelKey as any)}
                                </span>
                            </div>

                            {/* Title */}
                            <h1 className="text-xl font-semibold leading-tight [overflow-wrap:anywhere]">
                                {pr.title}
                            </h1>
                        </div>

                        {(pr.status === "open" || pr.status === "approved") && (
                            <Button variant="outline" size="sm" className="gap-2 shrink-0 border-primary/20 hover:bg-primary/5 hover:text-primary transition-all" asChild>
                                <Link href={previewUrl}>
                                    <Eye className="h-4 w-4" />
                                    <span className="hidden sm:inline">
                                        {isApproved ? t("viewInLibrary") : t("browsePreview")}
                                    </span>
                                    <span className="sm:hidden">
                                        {isApproved ? t("view") : t("preview")}
                                    </span>
                                </Link>
                            </Button>
                        )}
                    </div>

                    {/* Author + date */}
                    <div className="flex items-center gap-2 text-sm flex-wrap">
                        {pr.author?.id ? (
                            <Link href={`/profile/${pr.author.id}`}>
                                <Avatar size="sm" className="hover:ring-2 hover:ring-primary/40 transition-all">
                                    <AvatarFallback className="text-[10px]">
                                        {initials}
                                    </AvatarFallback>
                                </Avatar>
                            </Link>
                        ) : (
                            <Avatar size="sm">
                                <AvatarFallback className="text-[10px]">
                                    {initials}
                                </AvatarFallback>
                            </Avatar>
                        )}
                        {pr.author?.id ? (
                            <Link href={`/profile/${pr.author.id}`} className="font-medium hover:underline">
                                {pr.author.display_name}
                            </Link>
                        ) : (
                            <span className="font-medium">{tCommon("deletedAccount")}</span>
                        )}
                        <span className="text-muted-foreground">
                            {t("submitted", {
                                time: formatDistanceToNow(new Date(pr.created_at), {
                                    addSuffix: true,
                                    locale: dateLocale,
                                }),
                            })}
                        </span>
                        {pr.status === "open" && (
                            <>
                                <span className="text-muted-foreground">·</span>
                                <span className={`flex items-center gap-1 text-xs ${isExpiringSoon ? "text-amber-600 font-medium" : "text-muted-foreground"}`}>
                                    <Clock className="h-3 w-3" />
                                    {isExpiringSoon
                                        ? t("expiresSoon", {
                                              time: formatDistanceToNow(expiresDate, {
                                                  addSuffix: true,
                                                  locale: dateLocale,
                                              }),
                                          })
                                        : t("expiresDefault")}
                                </span>
                            </>
                        )}
                    </div>

                    {/* Description */}
                    {pr.description && (
                        <ExpandableText 
                            text={pr.description} 
                            className="text-sm leading-relaxed text-muted-foreground" 
                            clampedLines={3}
                        />
                    )}

                    {/* Summary badges */}
                    <div className="flex flex-wrap gap-1.5">
                        {Object.entries(typeCounts).map(([type, count]) => {
                            const Icon = OP_ICONS[type] ?? FilePlus;
                            return (
                                <Badge
                                    key={type}
                                    variant="outline"
                                    className="gap-1 text-xs font-normal"
                                >
                                    <Icon className="h-3 w-3" />
                                    {count} {t(OP_LABELS_KEYS[type] as any) ?? type}
                                </Badge>
                            );
                        })}
                    </div>
                </div>

                {/* Revert toolbar (admin only, approved PRs within grace) */}
                {canRevert && pr.revert_grace_expires_at && (
                    <>
                        <Separator />
                        <div className="flex items-center justify-between gap-2 px-6 py-3">
                            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                <Clock className="h-3 w-3" />
                                {t("revertGrace", {
                                    time: formatDistanceToNow(new Date(pr.revert_grace_expires_at), {
                                        locale: dateLocale,
                                    }),
                                })}
                            </span>
                            <Button
                                size="sm"
                                variant="outline"
                                className="gap-1.5 border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700 dark:border-red-800 dark:hover:bg-red-950/30"
                                onClick={() => setShowRevertDialog(true)}
                                disabled={acting !== null}
                            >
                                {acting === "revert" ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Undo2 className="h-3.5 w-3.5" />
                                )}
                                {t("revertContribution")}
                            </Button>
                        </div>
                    </>
                )}

                {/* Expired grace period (admin only, informational) */}
                {isAdmin && pr.status === "approved" && !pr.reverted_by_pr_id && pr.type !== "revert" && !canRevert && pr.approved_at && (
                    <>
                        <Separator />
                        <div className="flex items-center justify-end gap-2 px-6 py-3">
                            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                <Clock className="h-3 w-3" />
                                {t("revertExpired")}
                            </span>
                        </div>
                    </>
                )}

                {/* Action toolbar (moderator review + author cancel) */}
                {pr.status === "open" && (isModerator || canCancel) && (
                    <>
                        <Separator />
                        <div className="flex items-center justify-end gap-2 px-6 py-3">
                            {canCancel && (
                                <Button
                                    size="sm"
                                    variant="outline"
                                    className="gap-1.5"
                                    onClick={() => setShowCancelDialog(true)}
                                    disabled={acting !== null}
                                >
                                    {acting === "cancel" ? (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                        <X className="h-3.5 w-3.5" />
                                    )}
                                    {t("cancelContribution")}
                                </Button>
                            )}
                            {isModerator && (
                                <>
                                    <Button
                                        size="sm"
                                        className="gap-1.5 bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
                                        onClick={handleApprove}
                                        disabled={acting !== null}
                                    >
                                        {acting === "approve" ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            <Check className="h-3.5 w-3.5" />
                                        )}
                                        {t("publish")}
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="destructive"
                                        className="gap-1.5"
                                        onClick={() => setShowRejectDialog(true)}
                                        disabled={acting !== null}
                                    >
                                        {acting === "reject" ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            <X className="h-3.5 w-3.5" />
                                        )}
                                        {t("reject")}
                                    </Button>
                                </>
                            )}
                        </div>
                    </>
                )}
            </div>

            {/* ─── Operations ─────────────────────────── */}
            <div className="overflow-hidden rounded-lg border bg-card">
                <div className="flex items-center justify-between border-b bg-muted/50 px-4 py-2.5">
                    <span className="text-sm font-medium text-muted-foreground">
                        {t("proposedChanges")}
                        <span className="ml-1.5 text-foreground/60">
                            · {t("changeCount", { count: operations.length })}
                        </span>
                    </span>
                    {operations.length > 1 && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 text-xs text-muted-foreground"
                            onClick={() =>
                                setExpandedItems(allExpanded ? [] : allItemValues)
                            }
                        >
                            {allExpanded ? (
                                <>
                                    <ChevronsDownUp className="h-3.5 w-3.5" />
                                    {t("collapseAll")}
                                </>
                            ) : (
                                <>
                                    <ChevronsUpDown className="h-3.5 w-3.5" />
                                    {t("expandAll")}
                                </>
                            )}
                        </Button>
                    )}
                </div>
                <Accordion
                    type="multiple"
                    className="w-full"
                    value={expandedItems}
                    onValueChange={setExpandedItems}
                >
                    {operations.map((op, i) => (
                        <OperationRow
                            key={i}
                            op={op}
                            prId={pr.id}
                            prStatus={pr.status}
                            index={i}
                        />
                    ))}
                </Accordion>
            </div>

            {/* ─── Comments ───────────────────────────── */}
            <div className="overflow-hidden rounded-lg border bg-card">
                <div className="border-b bg-muted/50 px-4 py-2.5 text-sm font-medium text-muted-foreground">
                    {t("discussion")}
                </div>
                <div className="p-4">
                    <PRComments prId={pr.id} />
                </div>
            </div>

            {/* ─── Reject dialog ──────────────────────── */}
            <Dialog open={showRejectDialog} onOpenChange={(open) => { setShowRejectDialog(open); if (!open) setRejectReason(""); }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{t("rejectTitle")}</DialogTitle>
                        <DialogDescription>
                            {t("rejectDesc")}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <Textarea
                            placeholder={t("rejectPlaceholder")}
                            value={rejectReason}
                            onChange={(e) => setRejectReason(e.target.value)}
                            rows={4}
                            maxLength={1000}
                            autoFocus
                        />
                        <div className="flex justify-between text-xs text-muted-foreground">
                            <span>{rejectReason.trim().length < 10 ? t("charsMin", { count: 10 - rejectReason.trim().length }) : ""}</span>
                            <span>{rejectReason.length}/1000</span>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => { setShowRejectDialog(false); setRejectReason(""); }}>
                            {t("cancel", { ns: "PRWizard" })}
                        </Button>
                        <Button
                            variant="destructive"
                            disabled={rejectReason.trim().length < 10}
                            onClick={handleReject}
                        >
                            <X className="mr-2 h-4 w-4" />
                            {t("reject")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ─── Cancel dialog ──────────────────────── */}
            <Dialog open={showCancelDialog} onOpenChange={setShowCancelDialog}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{t("cancelTitle")}</DialogTitle>
                        <DialogDescription>
                            {t("cancelDesc")}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setShowCancelDialog(false)}
                        >
                            {t("keepItOpen")}
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleCancel}
                        >
                            <X className="mr-2 h-4 w-4" />
                            {t("cancelContribution")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ─── Revert dialog (double confirmation) ── */}
            <Dialog
                open={showRevertDialog}
                onOpenChange={(open) => {
                    setShowRevertDialog(open);
                    if (!open) {
                        setRevertConfirmText("");
                        setRevertUnderstood(false);
                    }
                }}
            >
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-red-600">
                            <ShieldAlert className="h-5 w-5" />
                            {t("revertTitle")}
                        </DialogTitle>
                        <DialogDescription>
                            {t("revertDesc")}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200">
                        {t.rich("revertWarning", {
                            strong: (chunks) => <strong>{chunks}</strong>,
                        })}
                    </div>

                    <div className="space-y-4">
                        <div className="flex items-start gap-2">
                            <Checkbox
                                id="revert-understood"
                                checked={revertUnderstood}
                                onCheckedChange={(checked) => setRevertUnderstood(checked === true)}
                            />
                            <Label htmlFor="revert-understood" className="text-sm leading-tight cursor-pointer">
                                {t("revertUnderstood")}
                            </Label>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="revert-confirm" className="text-sm text-muted-foreground">
                                {t.rich("revertConfirmLabel", {
                                    text: (chunks) => <span className="font-mono font-semibold text-foreground">{t("revertConfirmText")}</span>,
                                })}
                            </Label>
                            <Input
                                id="revert-confirm"
                                value={revertConfirmText}
                                onChange={(e) => setRevertConfirmText(e.target.value)}
                                placeholder={t("revertConfirmText")}
                                className="font-mono"
                                autoComplete="off"
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => {
                                setShowRevertDialog(false);
                                setRevertConfirmText("");
                                setRevertUnderstood(false);
                            }}
                        >
                            {t("cancel", { ns: "PRWizard" })}
                        </Button>
                        <Button
                            variant="destructive"
                            disabled={!revertUnderstood || revertConfirmText !== t("revertConfirmText")}
                            onClick={handleRevert}
                        >
                            <Undo2 className="mr-2 h-4 w-4" />
                            {t("revertContribution")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
