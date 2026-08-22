"use client";

import { useEffect, useState, useMemo } from "react";
import { usePathname } from "next/navigation";
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
    Link2,
    ChevronDown,
    Clock,
    ChevronsDownUp,
    ChevronsUpDown,
    AlertCircle,
    MapPin,
    ArrowRight,
    Inbox,
    Undo2,
    ShieldAlert,
    FileText,
    Paperclip,
    Folder,
    Sparkles,
    AlertTriangle,
    Tag,
} from "lucide-react";
import { isExternalUrl } from "@/lib/url-utils";
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
import { PROperationThumbnail } from "./pr-operation-thumbnail";
import { PRLocationBreadcrumb, PRMoveTransition, type PathSegment } from "./pr-location-breadcrumb";
import { formatFileSize, getFileBadgeColor, getFileBadgeLabel, getFileExtension } from "@/lib/file-utils";
import { TYPE_COLORS, TYPE_ICONS } from "@/lib/material-icons";

/* ── Types ──────────────────────────────────────────── */

export type PullRequestOperation = Operation & {
    result_browse_path?: string | null;
    pr_type?: string;
    target_title?: string;
    target_name?: string;
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
    file_size?: number | null;
    file_mime_type?: string | null;
    diff_summary?: string | null;
    metadata?: Record<string, unknown>;
    tags?: string[];
    attachments?: Array<{
        title: string;
        type: string;
        file_name?: string | null;
        file_size?: number | null;
        file_key?: string | null;
        file_mime_type?: string | null;
        tags?: string[];
        metadata?: Record<string, unknown>;
    }>;
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
    create_link: ExternalLink,
    edit_link: ExternalLink,
    delete_link: ExternalLink,
    create_directory: FolderPlus,
    edit_directory: FolderPen,
    delete_directory: FolderX,
    move_item: ArrowRightLeft,
};

const OP_COLORS: Record<string, string> = {
    create_material: "text-green-600 bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800",
    create_link: "text-sky-600 bg-sky-50 border-sky-200 dark:bg-sky-950/30 dark:border-sky-800",
    edit_material: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800",
    edit_link: "text-sky-600 bg-sky-50 border-sky-200 dark:bg-sky-950/30 dark:border-sky-800",
    delete_material: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800",
    delete_link: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800",
    create_directory: "text-green-600 bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800",
    edit_directory: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800",
    delete_directory: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800",
    move_item: "text-amber-600 bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800",
};

const OP_LABELS_KEYS: Record<string, string> = {
    create_material: "labels.create_material",
    create_link: "labels.create_link",
    edit_material: "labels.edit_material",
    edit_link: "labels.edit_link",
    delete_material: "labels.delete_material",
    delete_link: "labels.delete_link",
    create_directory: "labels.create_directory",
    edit_directory: "labels.edit_directory",
    delete_directory: "labels.delete_directory",
    move_item: "labels.move_item",
};

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

/* ── Helpers ─────────────────────────────────────────── */

function getEffectiveOpKey(rawOp: Record<string, unknown>): string {
    const baseOp = String(rawOp.op ?? rawOp.pr_type ?? "unknown");
    const targetUrl = String((rawOp.metadata as Record<string, unknown> | undefined)?.url || rawOp.url || "").trim();
    const isLink = rawOp.type === "link" || rawOp.material_type === "link" || Boolean(targetUrl);
    if (isLink && baseOp.includes("material")) {
        return baseOp.replace("material", "link");
    }
    return baseOp;
}

function getInitials(name: string): string {
    return name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase();
}

async function resolveDirectoryPath(
    dirId: string | null | undefined,
    allOperations: PullRequestOperation[],
    tRoot: string,
): Promise<{ url: string; label: string; segments: PathSegment[]; isTemp: boolean }> {
    if (!dirId) {
        return { url: "/browse", label: tRoot, segments: [], isTemp: false };
    }
    const dirIdStr = String(dirId);
    if (dirIdStr.startsWith("$")) {
        const tempOp = allOperations.find(
            (o) => {
                const ro = o as unknown as Record<string, unknown>;
                return ro.temp_id === dirIdStr && ro.op === "create_directory";
            }
        ) as unknown as Record<string, unknown> | undefined;

        if (tempOp) {
            const tempName = String(tempOp.name || "New folder");
            const parentDirId = tempOp.parent_id ? String(tempOp.parent_id) : null;
            if (parentDirId && !parentDirId.startsWith("$")) {
                try {
                    const parentPath = await apiFetch<{ name: string; slug: string }[]>(`/directories/${parentDirId}/path`);
                    if (Array.isArray(parentPath) && parentPath.length > 0) {
                        const slugs = parentPath.map((p) => p.slug).join("/");
                        const segs: PathSegment[] = parentPath.map((p) => ({ name: p.name, slug: p.slug }));
                        segs.push({ name: tempName, isTemp: true });
                        return {
                            url: slugs ? `/browse/${slugs}` : "/browse",
                            label: `${parentPath.map((p) => p.name).join(" › ")} › ${tempName}`,
                            segments: segs,
                            isTemp: true,
                        };
                    }
                } catch {
                    // ignore
                }
            }
            return {
                url: "/browse",
                label: `${tRoot} › ${tempName}`,
                segments: [{ name: tempName, isTemp: true }],
                isTemp: true,
            };
        }
        return { url: "/browse", label: `${tRoot} › ${dirIdStr}`, segments: [{ name: dirIdStr, isTemp: true }], isTemp: true };
    }

    try {
        const path = await apiFetch<{ name: string; slug: string }[]>(`/directories/${dirIdStr}/path`);
        if (!Array.isArray(path) || path.length === 0) return { url: "/browse", label: tRoot, segments: [], isTemp: false };
        const slugs = path.map((p) => p.slug).join("/");
        const label = path.map((p) => p.name).join(" › ");
        return {
            url: `/browse/${slugs}`,
            label,
            segments: path.map((p) => ({ name: p.name, slug: p.slug })),
            isTemp: false,
        };
    } catch {
        return { url: "/browse", label: tRoot, segments: [], isTemp: false };
    }
}

/* ── Operation Details State ─────────────────────────── */

interface ResolvedItemState {
    itemName?: string;
    itemType?: string;
    materialId?: string;
    directoryId?: string;
    fileName?: string;
    fileSize?: number;
    mimeType?: string;
    tags?: string[];
    description?: string;
    metadata?: Record<string, unknown>;
    targetUrl?: string;
    attachmentCount?: number;
    attachments?: Array<{
        title: string;
        type: string;
        file_name?: string | null;
        file_size?: number | null;
        file_mime_type?: string | null;
        material_id?: string;
    }>;
    // Tree locations
    sourcePath?: string;
    sourceUrl?: string;
    sourceSegments?: PathSegment[];
    destPath?: string;
    destUrl?: string;
    destSegments?: PathSegment[];
    destIsTemp?: boolean;
}

/* ── OperationRow ────────────────────────────────────── */

function OperationRow({
    op,
    prId,
    prStatus,
    index,
    allOperations,
}: {
    op: PullRequestOperation;
    prId: string;
    prStatus: string;
    index: number;
    allOperations: PullRequestOperation[];
}) {
    const t = useTranslations("PRDetails");
    const mt = useTranslations("MaterialTypes");
    const dt = useTranslations("DirectoryTypes");
    const rawOp = op as unknown as Record<string, unknown>;

    const [itemDetails, setItemDetails] = useState<ResolvedItemState | null>(null);
    const [previewModal, setPreviewModal] = useState<{ url: string; fileName?: string; mimeType?: string } | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    const baseOpType = String(rawOp.op || rawOp.pr_type || "unknown");
    const targetUrl = String((rawOp.metadata as Record<string, unknown> | undefined)?.url || rawOp.url || itemDetails?.targetUrl || (itemDetails?.metadata as Record<string, unknown> | undefined)?.url || "").trim();
    const isLink = rawOp.type === "link" || rawOp.material_type === "link" || itemDetails?.itemType === "link" || Boolean(targetUrl);
    const isInternalLink = isLink && !isExternalUrl(targetUrl);
    const isDirectoryOp = baseOpType.includes("directory") || rawOp.target_type === "directory";

    const opType = isLink && baseOpType.includes("material")
        ? baseOpType.replace("material", "link")
        : baseOpType;

    const Icon = isLink
        ? (isInternalLink ? Link2 : ExternalLink)
        : (OP_ICONS[opType] ?? OP_ICONS[baseOpType] ?? FilePlus);
    const colorClass = OP_COLORS[opType] ?? OP_COLORS[baseOpType] ?? "";
    const isApproved = prStatus === "approved";

    // Result browse path if approved
    const resultBrowsePath = (() => {
        if (!isApproved) return null;
        if (rawOp.result_browse_path !== undefined && rawOp.result_browse_path !== null) {
            const path = String(rawOp.result_browse_path);
            return path ? `/browse/${path}` : "/browse";
        }
        if (itemDetails?.sourceUrl && itemDetails?.itemName) {
            if (opType.includes("material")) {
                return `${itemDetails.sourceUrl}/${itemDetails.itemName}`;
            }
            return itemDetails.sourceUrl;
        }
        return null;
    })();

    // Resolve item information and tree locations
    useEffect(() => {
        let cancelled = false;

        async function resolveAll() {
            try {
                // 1. New Creations (create_material, create_directory, create_link)
                if (opType.startsWith("create_")) {
                    const dirId = rawOp.directory_id ? String(rawOp.directory_id) : (rawOp.parent_id ? String(rawOp.parent_id) : null);
                    const destInfo = await resolveDirectoryPath(dirId, allOperations, t("root"));
                    if (cancelled) return;
                    setItemDetails({
                        itemName: String(rawOp.title || rawOp.name || ""),
                        itemType: String(rawOp.type || "document"),
                        fileName: rawOp.file_name ? String(rawOp.file_name) : undefined,
                        fileSize: typeof rawOp.file_size === "number" ? rawOp.file_size : undefined,
                        mimeType: rawOp.file_mime_type ? String(rawOp.file_mime_type) : undefined,
                        tags: Array.isArray(rawOp.tags) ? rawOp.tags as string[] : undefined,
                        description: rawOp.description ? String(rawOp.description) : undefined,
                        metadata: rawOp.metadata as Record<string, unknown> | undefined,
                        destPath: destInfo.label,
                        destUrl: destInfo.url,
                        destSegments: destInfo.segments,
                        destIsTemp: destInfo.isTemp,
                    });
                    return;
                }

                // 2. Edit Material or Delete Material
                if (opType === "edit_material" || opType === "delete_material" || opType === "edit_link" || opType === "delete_link") {
                    const matId = String(rawOp.material_id ?? "");
                    if (matId && !matId.startsWith("$")) {
                        try {
                            const [mat, matAttachments] = await Promise.all([
                                apiFetch<{
                                    title: string;
                                    type: string;
                                    directory_id: string | null;
                                    description: string | null;
                                    tags: string[];
                                    metadata: Record<string, unknown>;
                                    attachment_count?: number;
                                    current_version_info?: { file_mime_type?: string; file_name?: string; file_size?: number } | null;
                                }>(`/materials/${matId}`).catch(() => null),
                                apiFetch<Array<{
                                    id: string;
                                    title: string;
                                    type: string;
                                    current_version_info?: { file_mime_type?: string; file_name?: string; file_size?: number } | null;
                                }>>(`/materials/${matId}/attachments`).catch(() => []),
                            ]);
                            if (cancelled) return;

                            if (mat) {
                                const srcInfo = await resolveDirectoryPath(mat.directory_id, allOperations, t("root"));
                                if (cancelled) return;

                                const matTargetUrl = String((mat.metadata as Record<string, unknown> | undefined)?.url || "").trim();

                                setItemDetails({
                                    itemName: mat.title,
                                    itemType: mat.type,
                                    materialId: matId,
                                    fileName: mat.current_version_info?.file_name ?? undefined,
                                    fileSize: typeof mat.current_version_info?.file_size === "number" ? mat.current_version_info.file_size : undefined,
                                    mimeType: mat.current_version_info?.file_mime_type ?? undefined,
                                    tags: mat.tags,
                                    description: mat.description ?? undefined,
                                    metadata: mat.metadata,
                                    targetUrl: matTargetUrl,
                                    attachmentCount: mat.attachment_count || (matAttachments?.length ?? 0),
                                    attachments: matAttachments?.map((a) => ({
                                        title: a.title,
                                        type: a.type,
                                        file_name: a.current_version_info?.file_name,
                                        file_size: a.current_version_info?.file_size,
                                        file_mime_type: a.current_version_info?.file_mime_type,
                                        material_id: a.id,
                                    })) ?? [],
                                    sourcePath: srcInfo.label,
                                    sourceUrl: srcInfo.url,
                                    sourceSegments: srcInfo.segments,
                                });
                            }
                        } catch (e) {
                            console.error("Failed to load material details for PR", e);
                        }
                    }
                    return;
                }

                // 3. Edit Directory or Delete Directory
                if (opType === "edit_directory" || opType === "delete_directory") {
                    const dirId = String(rawOp.directory_id ?? "");
                    if (dirId && !dirId.startsWith("$")) {
                        const [dir, path] = await Promise.all([
                            apiFetch<{ name: string; type: string; description: string | null; parent_id: string | null; metadata?: Record<string, unknown> }>(`/directories/${dirId}`).catch(() => null),
                            apiFetch<{ name: string; slug: string }[]>(`/directories/${dirId}/path`).catch(() => []),
                        ]);
                        if (cancelled) return;

                        const itemName = dir?.name || (path.length > 0 ? path[path.length - 1].name : t("root"));
                        const parentSegs = path.slice(0, -1);
                        const sourcePath = parentSegs.length > 0
                            ? parentSegs.map((p) => p.name).join(" › ")
                            : t("root");
                        const parentSlugs = parentSegs.map((p) => p.slug).join("/");

                        setItemDetails({
                            itemName,
                            itemType: dir?.type || "folder",
                            directoryId: dirId,
                            description: dir?.description ?? undefined,
                            metadata: dir?.metadata,
                            sourcePath,
                            sourceUrl: parentSlugs ? `/browse/${parentSlugs}` : "/browse",
                            sourceSegments: parentSegs.map((p) => ({ name: p.name, slug: p.slug })),
                        });
                    }
                    return;
                }

                // 4. Move Item
                if (opType === "move_item") {
                    const targetId = String(rawOp.target_id ?? "");
                    const targetType = String(rawOp.target_type ?? "");
                    const newParentId = rawOp.new_parent_id ? String(rawOp.new_parent_id) : null;

                    let itemName: string | undefined = rawOp.target_title ? String(rawOp.target_title) : (rawOp.target_name ? String(rawOp.target_name) : undefined);
                    let itemType: string | undefined = rawOp.target_material_type ? String(rawOp.target_material_type) : (targetType === "directory" ? "folder" : "document");
                    let materialId: string | undefined;
                    let directoryId: string | undefined;
                    let fileName: string | undefined;
                    let fileSize: number | undefined;
                    let mimeType: string | undefined;
                    let sourcePath: string = t("root");
                    let sourceUrl: string = "/browse";
                    let sourceSegments: PathSegment[] = [];

                    if (targetType === "material" && targetId && !targetId.startsWith("$")) {
                        try {
                            const mat = await apiFetch<{
                                title: string;
                                type: string;
                                directory_id: string | null;
                                current_version_info?: { file_mime_type?: string; file_name?: string; file_size?: number } | null;
                            }>(`/materials/${targetId}`);
                            if (!cancelled) {
                                itemName = mat.title;
                                itemType = mat.type;
                                materialId = targetId;
                                fileName = mat.current_version_info?.file_name ?? undefined;
                                fileSize = typeof mat.current_version_info?.file_size === "number" ? mat.current_version_info.file_size : undefined;
                                mimeType = mat.current_version_info?.file_mime_type ?? undefined;

                                const srcInfo = await resolveDirectoryPath(mat.directory_id, allOperations, t("root"));
                                sourcePath = srcInfo.label;
                                sourceUrl = srcInfo.url;
                                sourceSegments = srcInfo.segments;
                            }
                        } catch { /* ignore */ }
                    } else if (targetType === "directory" && targetId && !targetId.startsWith("$")) {
                        try {
                            const [dir, path] = await Promise.all([
                                apiFetch<{ name: string; type: string; parent_id: string | null }>(`/directories/${targetId}`).catch(() => null),
                                apiFetch<{ name: string; slug: string }[]>(`/directories/${targetId}/path`).catch(() => []),
                            ]);
                            if (!cancelled) {
                                itemName = dir?.name || (path.length > 0 ? path[path.length - 1].name : itemName);
                                itemType = dir?.type || "folder";
                                directoryId = targetId;
                                const parentSegs = path.slice(0, -1);
                                sourcePath = parentSegs.length > 0 ? parentSegs.map((p) => p.name).join(" › ") : t("root");
                                const parentSlugs = parentSegs.map((p) => p.slug).join("/");
                                sourceUrl = parentSlugs ? `/browse/${parentSlugs}` : "/browse";
                                sourceSegments = parentSegs.map((p) => ({ name: p.name, slug: p.slug }));
                            }
                        } catch { /* ignore */ }
                    }

                    const destInfo = await resolveDirectoryPath(newParentId, allOperations, t("root"));
                    if (cancelled) return;

                    setItemDetails({
                        itemName,
                        itemType,
                        materialId,
                        directoryId,
                        fileName,
                        fileSize,
                        mimeType,
                        sourcePath,
                        sourceUrl,
                        sourceSegments,
                        destPath: destInfo.label,
                        destUrl: destInfo.url,
                        destSegments: destInfo.segments,
                        destIsTemp: destInfo.isTemp,
                    });
                }
            } catch {
                // Silently fallback to basic payload
            }
        }

        resolveAll();
        return () => { cancelled = true; };
    }, [op, opType, prId, allOperations.length]);

    // Handle Quick Preview action
    const handleQuickPreview = async (e?: React.MouseEvent) => {
        if (e) e.stopPropagation();
        if (previewLoading) return;

        // If link with targetUrl
        if (isLink && targetUrl) {
            setPreviewModal({
                url: targetUrl,
                fileName: String(rawOp.title || rawOp.name || itemDetails?.itemName || "Link"),
                mimeType: "application/x-external-link",
            });
            return;
        }

        setPreviewLoading(true);
        try {
            // 1. Try PR preview endpoint
            const res = await apiFetch<{ url: string; file_name?: string; file_mime_type?: string }>(
                `/pull-requests/${prId}/preview?opIndex=${index}`,
            );
            if (res?.url) {
                setPreviewModal({
                    url: res.url,
                    fileName: res.file_name || String(rawOp.file_name || rawOp.title || itemDetails?.fileName || "File"),
                    mimeType: res.file_mime_type || String(rawOp.file_mime_type || itemDetails?.mimeType || ""),
                });
                return;
            }
        } catch {
            // 2. Fallback to existing material inline endpoint
            const matId = itemDetails?.materialId || (rawOp.material_id && !String(rawOp.material_id).startsWith("$") ? String(rawOp.material_id) : undefined);
            if (matId) {
                try {
                    const res = await apiFetch<{ url: string }>(`/materials/${matId}/inline`);
                    if (res?.url) {
                        setPreviewModal({
                            url: res.url,
                            fileName: itemDetails?.fileName || String(rawOp.title || "File"),
                            mimeType: itemDetails?.mimeType,
                        });
                        return;
                    }
                } catch {
                    toast.error(t("previewUnavailable"));
                }
            } else {
                toast.error(t("previewUnavailable"));
            }
        } finally {
            setPreviewLoading(false);
        }
    };

    // Rename detection
    const isMaterialRename = Boolean(
        opType.startsWith("edit_material") &&
        rawOp.title &&
        itemDetails?.itemName &&
        String(rawOp.title).trim() !== itemDetails.itemName.trim(),
    );
    const isDirectoryRename = Boolean(
        opType.startsWith("edit_directory") &&
        rawOp.name &&
        itemDetails?.itemName &&
        String(rawOp.name).trim() !== itemDetails.itemName.trim(),
    );
    const isRename = isMaterialRename || isDirectoryRename;
    const oldTitle = itemDetails?.itemName;
    const newTitle = String(rawOp.title || rawOp.name || "");

    // File replacement detection
    const isFileReplaced = Boolean(
        opType.startsWith("edit_material") &&
        rawOp.file_key &&
        (itemDetails?.fileName || rawOp.file_name),
    );
    const oldFileName = itemDetails?.fileName;
    const oldFileSize = itemDetails?.fileSize;
    const newFileName = rawOp.file_name ? String(rawOp.file_name) : undefined;
    const newFileSize = typeof rawOp.file_size === "number" ? rawOp.file_size : undefined;

    // Type adjustment detection
    const isTypeChanged = Boolean(
        rawOp.type &&
        itemDetails?.itemType &&
        String(rawOp.type) !== itemDetails.itemType,
    );
    const oldType = itemDetails?.itemType;
    const newType = rawOp.type ? String(rawOp.type) : undefined;

    // Tag differences
    const oldTags = itemDetails?.tags || [];
    const newTags = Array.isArray(rawOp.tags) ? rawOp.tags as string[] : undefined;
    const tagsAdded = newTags ? newTags.filter((t) => !oldTags.includes(t)) : [];
    const tagsRemoved = newTags ? oldTags.filter((t) => !newTags.includes(t)) : [];

    // Primary effective name
    const effectiveName = isRename
        ? newTitle
        : (itemDetails?.itemName || String(rawOp.title || rawOp.name || rawOp.target_title || rawOp.target_name || (isDirectoryOp ? (opType.includes("delete") ? t("labels.delete_directory") : t("labels.create_directory")) : isLink ? (opType.includes("delete") ? t("labels.delete_link") : t("labels.create_link")) : (opType.includes("delete") ? t("labels.delete_material") : t("labels.create_material")))));

    // File metadata
    const activeFileName = newFileName || oldFileName || itemDetails?.fileName || (rawOp.file_name ? String(rawOp.file_name) : undefined);
    const activeFileSize = typeof newFileSize === "number" ? newFileSize : (typeof oldFileSize === "number" ? oldFileSize : (typeof itemDetails?.fileSize === "number" ? itemDetails.fileSize : (typeof rawOp.file_size === "number" ? rawOp.file_size : undefined)));
    const activeMimeType = rawOp.file_mime_type ? String(rawOp.file_mime_type) : itemDetails?.mimeType;
    const activeMaterialType = newType || oldType || itemDetails?.itemType || (rawOp.type ? String(rawOp.type) : undefined);

    const hasPreview = Boolean(
        rawOp.file_key ||
        itemDetails?.materialId ||
        isLink ||
        rawOp.target_type === "material",
    );

    const attachments = (Array.isArray(rawOp.attachments) && rawOp.attachments.length > 0)
        ? rawOp.attachments
        : (itemDetails?.attachments || []);
    const diffSummary = "diff_summary" in op ? (rawOp.diff_summary as string | null) : null;

    return (
        <>
            {previewModal && (
                <PreviewDialog
                    url={previewModal.url}
                    fileName={previewModal.fileName}
                    mimeType={previewModal.mimeType}
                    onClose={() => setPreviewModal(null)}
                />
            )}

            <AccordionItem
                value={`op-${index}`}
                className="border-b last:border-0 transition-colors"
            >
                <AccordionPrimitive.Header className="flex items-center">
                    <AccordionPrimitive.Trigger
                        className="flex flex-1 items-center gap-3.5 px-4 py-3.5 text-left transition-colors hover:bg-accent/40 [&[data-state=open]>svg.chevron]:rotate-180 min-w-0"
                    >
                        {/* Mini Thumbnail / Icon Preview */}
                        <PROperationThumbnail
                            size="sm"
                            fileName={activeFileName}
                            mimeType={activeMimeType}
                            materialType={activeMaterialType}
                            materialId={itemDetails?.materialId}
                            stagedFileKey={rawOp.file_key ? String(rawOp.file_key) : undefined}
                            targetUrl={targetUrl}
                            isDirectory={isDirectoryOp}
                            directoryIcon={String((rawOp.metadata as Record<string, unknown> | undefined)?.thumbnail_icon || itemDetails?.metadata?.thumbnail_icon || "")}
                            directoryColor={String((rawOp.metadata as Record<string, unknown> | undefined)?.thumbnail_color || itemDetails?.metadata?.thumbnail_color || "")}
                        />

                        {/* Title and Summary */}
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap min-w-0">
                                {isRename ? (
                                    <div className="flex items-center gap-1.5 text-sm font-medium truncate flex-wrap">
                                        <span className="line-through text-muted-foreground opacity-75 truncate max-w-[200px]" title={oldTitle}>
                                            {oldTitle}
                                        </span>
                                        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-primary" />
                                        <span className="text-foreground font-semibold truncate max-w-[240px]" title={newTitle}>
                                            {newTitle}
                                        </span>
                                    </div>
                                ) : (
                                    <p className="truncate text-sm font-medium text-foreground">
                                        {opType === "move_item"
                                            ? `${t("summary.move_item", { isDir: String(isDirectoryOp), name: effectiveName })}`
                                            : effectiveName}
                                    </p>
                                )}

                                {/* Action Type Badge */}
                                <span
                                    className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium border ${colorClass}`}
                                >
                                    <Icon className="h-3 w-3 shrink-0" />
                                    {t(OP_LABELS_KEYS[opType] as any) ?? opType}
                                </span>
                            </div>

                            {/* Second line: Target URL / Breadcrumb Snippet / File badge */}
                            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1 flex-wrap">
                                {targetUrl && (
                                    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-sky-600 dark:text-sky-400 truncate max-w-xs">
                                        {isInternalLink ? <Link2 className="h-3 w-3 shrink-0" /> : <ExternalLink className="h-3 w-3 shrink-0" />}
                                        <span className="truncate">{targetUrl}</span>
                                    </span>
                                )}

                                {activeFileName && !isDirectoryOp && !isLink && (
                                    <span className={`px-1.5 py-0.2 rounded text-[10px] font-semibold ${getFileBadgeColor(activeFileName)}`}>
                                        {getFileBadgeLabel(activeFileName, activeMimeType ?? undefined)}
                                        {typeof activeFileSize === "number" && ` · ${formatFileSize(activeFileSize)}`}
                                    </span>
                                )}

                                {activeMaterialType && !isDirectoryOp && (
                                    <Badge variant="outline" className="text-[10px] h-4.5 px-1.5 py-0 font-normal">
                                        {String(mt.has?.(activeMaterialType as any) ? mt(activeMaterialType as any) : activeMaterialType)}
                                    </Badge>
                                )}

                                {/* Tree path preview */}
                                {opType === "move_item" && itemDetails && (
                                    <div className="flex items-center gap-1 text-[11px] opacity-80 shrink-0">
                                        <span className="truncate max-w-[100px]">{itemDetails.sourcePath || t("root")}</span>
                                        <ArrowRight className="h-3 w-3 shrink-0 text-primary" />
                                        <span className="truncate max-w-[100px] font-medium text-foreground">{itemDetails.destPath || t("root")}</span>
                                    </div>
                                )}

                                {opType.startsWith("create_") && itemDetails?.destPath && (
                                    <div className="flex items-center gap-1 text-[11px] opacity-80">
                                        <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                        <span className="truncate max-w-[180px]">{itemDetails.destPath}</span>
                                    </div>
                                )}

                                {(opType.startsWith("edit_") || opType.startsWith("delete_")) && itemDetails?.sourcePath && (
                                    <div className="flex items-center gap-1 text-[11px] opacity-80">
                                        <MapPin className="h-3 w-3 shrink-0 opacity-60" />
                                        <span className="truncate max-w-[180px]">{itemDetails.sourcePath}</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        <ChevronDown className="chevron h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200" />
                    </AccordionPrimitive.Trigger>

                    {/* Quick Row Actions */}
                    <div
                        className="flex shrink-0 items-center gap-1.5 pr-4"
                        onClick={(e) => e.stopPropagation()}
                    >
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

                        {!isApproved && hasPreview && !opType.includes("delete") && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1.5 text-xs text-primary font-medium"
                                onClick={handleQuickPreview}
                                disabled={previewLoading}
                            >
                                {previewLoading ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Eye className="h-3.5 w-3.5" />
                                )}
                                {t("preview")}
                            </Button>
                        )}

                        {opType.includes("delete") && hasPreview && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1.5 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30"
                                onClick={handleQuickPreview}
                                disabled={previewLoading}
                            >
                                {previewLoading ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Eye className="h-3.5 w-3.5" />
                                )}
                                {t("preview")}
                            </Button>
                        )}
                    </div>
                </AccordionPrimitive.Header>

                <AccordionContent className="px-4 pb-5 pt-1 space-y-4">
                    {/* 1. Deletion Warning Banner */}
                    {opType.includes("delete") && (
                        <div className="flex items-start gap-2.5 p-3 rounded-lg border border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200 text-xs">
                            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-red-600" />
                            <div className="space-y-1">
                                <p className="font-semibold">
                                    {isDirectoryOp ? t("deleteWarningDirectory") : t("deleteWarningMaterial")}
                                </p>
                            </div>
                        </div>
                    )}

                    {/* 2. Structured Information Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        {/* Column A: Visual Thumbnail & File / Item Details */}
                        <div className="rounded-lg border bg-muted/20 p-3 flex flex-col gap-3">
                            <div className="flex items-center gap-2">
                                <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                                    {isDirectoryOp ? t("fieldType") : (isLink ? "Lien" : t("fileDetails"))}
                                </span>
                            </div>

                            <div className="flex items-start gap-3">
                                <PROperationThumbnail
                                    size="md"
                                    fileName={activeFileName}
                                    mimeType={activeMimeType}
                                    materialType={activeMaterialType}
                                    materialId={itemDetails?.materialId}
                                    stagedFileKey={rawOp.file_key ? String(rawOp.file_key) : undefined}
                                    targetUrl={targetUrl}
                                    isDirectory={isDirectoryOp}
                                    directoryIcon={String((rawOp.metadata as Record<string, unknown> | undefined)?.thumbnail_icon || itemDetails?.metadata?.thumbnail_icon || "")}
                                    directoryColor={String((rawOp.metadata as Record<string, unknown> | undefined)?.thumbnail_color || itemDetails?.metadata?.thumbnail_color || "")}
                                />

                                <div className="space-y-1.5 min-w-0 flex-1 text-xs">
                                    <div>
                                        <span className="text-muted-foreground block text-[10px] uppercase font-semibold">{t("fieldTitle")}</span>
                                        <p className="font-semibold text-xs text-foreground truncate" title={effectiveName}>{effectiveName}</p>
                                    </div>

                                    {activeFileName && !isDirectoryOp && !isLink && (
                                        <div>
                                            <span className="text-muted-foreground block text-[10px]">{t("fieldFileName")}</span>
                                            <p className="font-mono text-xs break-all font-medium text-muted-foreground">{activeFileName}</p>
                                        </div>
                                    )}

                                    <div className="flex items-center gap-2 flex-wrap pt-0.5">
                                        {activeFileName && !isDirectoryOp && !isLink && (
                                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase ${getFileBadgeColor(activeFileName)}`}>
                                                {getFileBadgeLabel(activeFileName, activeMimeType ?? undefined)}
                                            </span>
                                        )}

                                        {typeof activeFileSize === "number" && !isDirectoryOp && !isLink && (
                                            <span className="text-muted-foreground font-mono text-xs">
                                                {formatFileSize(activeFileSize)}
                                            </span>
                                        )}

                                        {activeMaterialType && !isDirectoryOp && (
                                            <Badge variant="secondary" className="text-[10px] font-normal">
                                                {String(mt.has?.(activeMaterialType as any) ? mt(activeMaterialType as any) : activeMaterialType)}
                                            </Badge>
                                        )}

                                        {isDirectoryOp && (
                                            <Badge variant="secondary" className="text-[10px] font-normal">
                                                {String(dt.has?.(String(rawOp.type || itemDetails?.itemType || "folder") as any) ? dt(String(rawOp.type || itemDetails?.itemType || "folder") as any) : (rawOp.type || itemDetails?.itemType || "Folder"))}
                                            </Badge>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {/* Link Destination */}
                            {targetUrl && (
                                <div className="pt-2 border-t border-border/50 space-y-1 text-xs">
                                    <span className="text-[10px] text-muted-foreground uppercase font-semibold">{t("fieldUrl")}</span>
                                    <div className="flex items-center gap-2">
                                        <a
                                            href={targetUrl}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline break-all"
                                        >
                                            <span className="truncate">{targetUrl}</span>
                                            <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
                                        </a>
                                    </div>
                                </div>
                            )}

                            {/* Previews Button Group */}
                            {hasPreview && (
                                <div className="pt-2 border-t border-border/50 flex items-center gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-7 text-xs flex-1 gap-1.5"
                                        onClick={handleQuickPreview}
                                        disabled={previewLoading}
                                    >
                                        {previewLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                                        {t("quickPreview")}
                                    </Button>

                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 text-xs px-2"
                                        asChild
                                    >
                                        <Link href={`/pull-requests/${prId}/preview/${index}`} title={t("openFullPreview")}>
                                            <ExternalLink className="h-3.5 w-3.5" />
                                        </Link>
                                    </Button>
                                </div>
                            )}
                        </div>

                        {/* Column B: Tree Placement & Path Details */}
                        <div className="rounded-lg border bg-muted/20 p-3 flex flex-col gap-2.5 md:col-span-2">
                            <div className="flex items-center justify-between">
                                <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                                    <MapPin className="h-3.5 w-3.5 text-primary" />
                                    {t("locationInTree")}
                                </span>
                            </div>

                            {/* Move Flow */}
                            {opType === "move_item" ? (
                                <PRMoveTransition
                                    originPath={itemDetails?.sourcePath}
                                    originUrl={itemDetails?.sourceUrl}
                                    destPath={itemDetails?.destPath}
                                    destUrl={itemDetails?.destUrl}
                                    rootLabel={t("root")}
                                    originLabel={t("originLocation")}
                                    destLabel={t("destinationLocation")}
                                />
                            ) : (
                                <div className="p-2.5 rounded-lg border bg-background text-xs space-y-1">
                                    <PRLocationBreadcrumb
                                        pathSegments={opType.startsWith("create_") ? itemDetails?.destSegments : itemDetails?.sourceSegments}
                                        pathString={opType.startsWith("create_") ? itemDetails?.destPath : itemDetails?.sourcePath}
                                        browseUrl={opType.startsWith("create_") ? itemDetails?.destUrl : itemDetails?.sourceUrl}
                                        rootLabel={t("root")}
                                        isNewFolder={itemDetails?.destIsTemp}
                                    />
                                </div>
                            )}

                            {/* Deletion details or Modifications */}
                            {opType.includes("delete") ? (
                                <div className="pt-2 border-t border-border/50 space-y-2">
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                        {t("deletedItemDetails")}
                                    </span>

                                    {/* Description */}
                                    {Boolean(itemDetails?.description || rawOp.description) && (
                                        <div className="text-xs space-y-0.5">
                                            <span className="text-[10px] text-muted-foreground uppercase font-semibold block">
                                                {t("fieldDescription")}
                                            </span>
                                            <ExpandableText text={String(itemDetails?.description || rawOp.description)} clampedLines={2} className="text-xs text-muted-foreground" />
                                        </div>
                                    )}

                                    {/* Tags */}
                                    {Boolean((itemDetails?.tags && itemDetails.tags.length > 0) || (Array.isArray(rawOp.tags) && rawOp.tags.length > 0)) && (
                                        <div className="flex items-center gap-1.5 flex-wrap text-xs pt-0.5">
                                            <Tag className="h-3 w-3 text-muted-foreground shrink-0" />
                                            <span className="text-muted-foreground text-[11px]">{t("fieldTags")}:</span>
                                            {((itemDetails?.tags && itemDetails.tags.length > 0 ? itemDetails.tags : (rawOp.tags as string[])) || []).map((tag) => (
                                                <Badge key={tag} variant="secondary" className="text-[10px] font-normal">
                                                    {tag}
                                                </Badge>
                                            ))}
                                        </div>
                                    )}

                                    {/* Cascade attachments note */}
                                    {Boolean(itemDetails?.attachmentCount && itemDetails.attachmentCount > 0) && (
                                        <div className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400 bg-amber-500/10 p-2 rounded-md border border-amber-500/20">
                                            <Paperclip className="h-3.5 w-3.5 shrink-0" />
                                            <span>
                                                {t("deletedAttachmentsCount", { count: itemDetails?.attachmentCount || 0 })}
                                            </span>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="pt-2 border-t border-border/50 space-y-2">
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                        {t("modifications")}
                                    </span>

                                    {/* Rename diff */}
                                    {isRename && (
                                        <div className="flex items-center gap-2 p-2 rounded-md bg-accent/40 text-xs flex-wrap">
                                            <span className="font-semibold text-muted-foreground uppercase text-[10px]">{t("rename")}:</span>
                                            <span className="line-through text-red-600/80 dark:text-red-400/80 font-mono">{oldTitle}</span>
                                            <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                                            <span className="text-green-600 dark:text-green-400 font-mono font-semibold">{newTitle}</span>
                                        </div>
                                    )}

                                    {/* File replacement diff */}
                                    {isFileReplaced && (
                                        <div className="flex items-center gap-2 p-2 rounded-md bg-accent/40 text-xs flex-wrap">
                                            <span className="font-semibold text-muted-foreground uppercase text-[10px]">{t("fileReplaced")}:</span>
                                            <span className="line-through text-red-600/80 dark:text-red-400/80 font-mono">
                                                {oldFileName} {typeof oldFileSize === "number" && `(${formatFileSize(oldFileSize)})`}
                                            </span>
                                            <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                                            <span className="text-green-600 dark:text-green-400 font-mono font-semibold">
                                                {newFileName} {typeof newFileSize === "number" && `(${formatFileSize(newFileSize)})`}
                                            </span>
                                        </div>
                                    )}

                                    {/* Type adjustment diff */}
                                    {isTypeChanged && (
                                        <div className="flex items-center gap-2 p-2 rounded-md bg-accent/40 text-xs flex-wrap">
                                            <span className="font-semibold text-muted-foreground uppercase text-[10px]">{t("fieldType")}:</span>
                                            <Badge variant="outline" className="line-through opacity-70 text-xs">
                                                {oldType ? String(mt.has?.(oldType as any) ? mt(oldType as any) : oldType) : "—"}
                                            </Badge>
                                            <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                                            <Badge variant="secondary" className="text-xs font-semibold">
                                                {newType ? String(mt.has?.(newType as any) ? mt(newType as any) : newType) : "—"}
                                            </Badge>
                                        </div>
                                    )}

                                    {/* Tags changes */}
                                    {(tagsAdded.length > 0 || tagsRemoved.length > 0 || (newTags && newTags.length > 0)) && (
                                        <div className="flex items-center gap-1.5 flex-wrap text-xs pt-1">
                                            <Tag className="h-3 w-3 text-muted-foreground shrink-0" />
                                            <span className="text-muted-foreground text-[11px]">{t("fieldTags")}:</span>
                                            {tagsAdded.map((tag) => (
                                                <Badge key={tag} className="bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300 text-[10px] font-normal">
                                                    +{tag}
                                                </Badge>
                                            ))}
                                            {tagsRemoved.map((tag) => (
                                                <Badge key={tag} variant="outline" className="line-through text-red-600 border-red-200 text-[10px] font-normal">
                                                    -{tag}
                                                </Badge>
                                            ))}
                                            {tagsAdded.length === 0 && tagsRemoved.length === 0 && newTags?.map((tag) => (
                                                <Badge key={tag} variant="secondary" className="text-[10px] font-normal">
                                                    {tag}
                                                </Badge>
                                            ))}
                                        </div>
                                    )}

                                    {/* Description */}
                                    {Boolean(rawOp.description) && (
                                        <div className="pt-1 text-xs">
                                            <span className="text-[10px] text-muted-foreground uppercase font-semibold block mb-0.5">
                                                {t("fieldDescription")}
                                            </span>
                                            <ExpandableText text={String(rawOp.description)} clampedLines={2} className="text-xs text-muted-foreground" />
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>

                    {/* 3. Sub-attachments Breakdown (if any) */}
                    {attachments.length > 0 && (
                        <div className="rounded-lg border bg-muted/10 p-3 space-y-2">
                            <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                                <Paperclip className="h-3.5 w-3.5 text-primary" />
                                {t("fieldAttachments")} ({attachments.length})
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                {attachments.map((att, attIdx) => (
                                    <div
                                        key={attIdx}
                                        className="flex items-center gap-2.5 p-2 rounded-md border bg-background text-xs"
                                    >
                                        <PROperationThumbnail
                                            size="sm"
                                            fileName={att.file_name}
                                            mimeType={att.file_mime_type}
                                            materialType={att.type}
                                            stagedFileKey={att.file_key}
                                            materialId={att.material_id}
                                        />
                                        <div className="min-w-0 flex-1">
                                            <p className="font-medium truncate">{att.title}</p>
                                            <p className="text-[11px] text-muted-foreground font-mono truncate">
                                                {att.file_name || "Attachment"}
                                                {typeof att.file_size === "number" && ` · ${formatFileSize(att.file_size)}`}
                                            </p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 4. Diff Summary (if present) */}
                    {diffSummary && (
                        <div className="rounded-lg border bg-muted/10 p-3 space-y-2">
                            <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
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
            </AccordionItem>
        </>
    );
}

/* ── Main Page ──────────────────────────────────────── */

function PRDetailContent() {
    const pathname = usePathname();
    const id = pathname.replace(/^\/pull-requests\//, "").replace(/\/$/, "");
    const t = useTranslations("PRDetails");
    const tPRs = useTranslations("PRs");
    const tCommon = useTranslations("Common");
    const locale = useLocale();
    const dateLocale = locale === "fr" ? fr : enUS;
    const user = useAuthStore((state) => state.user);
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

    const operations: PullRequestOperation[] = useMemo(() => {
        if (!pr) return [];
        return pr.status === "approved" && Array.isArray(pr.applied_result) && pr.applied_result.length > 0
            ? pr.applied_result
            : Array.isArray(pr.payload)
                ? pr.payload
                : [pr.payload] as PullRequestOperation[];
    }, [pr]);

    const tRoot = t("root");
    useEffect(() => {
        if (!pr) return;
        for (const op of operations) {
            const rawOp = op as unknown as Record<string, unknown>;
            const dirId = (rawOp.directory_id ?? rawOp.parent_id) as string | undefined;
            if (dirId && typeof dirId === "string" && !dirId.startsWith("$")) {
                setPreviewDirId(dirId);
                return;
            }
        }
    }, [pr]);

    useEffect(() => {
        if (previewDirId) {
            resolveDirectoryPath(previewDirId, operations, tRoot).then((info) => setPreviewPath(info.url));
        }
    }, [previewDirId, tRoot, operations]);

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

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

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
        const tKey = getEffectiveOpKey(rawOp);
        typeCounts[tKey] = (typeCounts[tKey] || 0) + 1;
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

    const updatedDate = new Date(pr.updated_at);
    const expiresDate = new Date(updatedDate.getTime() + 7 * 24 * 60 * 60 * 1000);
    const isExpiringSoon = pr.status === "open" && (expiresDate.getTime() - Date.now() < 24 * 60 * 60 * 1000);

    const isApproved = pr.status === "approved";
    const previewUrl = isApproved
        ? (previewPath || "/browse")
        : (previewPath ? `${previewPath}?preview_pr=${id}` : `/browse?preview_pr=${id}`);

    return (
        <div className="container mx-auto max-w-5xl space-y-6 px-4 py-6 pb-36 sm:px-6 sm:py-8 sm:pb-12 lg:px-8">
            <Link
                href="/pull-requests"
                className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors bg-muted/50 hover:bg-muted px-3 py-1.5 rounded-xl w-fit"
            >
                <ArrowLeft className="h-3.5 w-3.5" />
                {tPRs("contributions")}
            </Link>

            {pr.reverted_by_pr_id && (
                <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50/80 p-4.5 dark:border-amber-800 dark:bg-amber-950/20">
                    <Undo2 className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                    <div>
                        <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                            {t("revertedBanner")}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-amber-700/90 dark:text-amber-400/80">
                            {t("revertedBannerDesc")}{" "}
                            <Link
                                href={`/pull-requests/${pr.reverted_by_pr_id}`}
                                className="underline font-medium hover:text-amber-800 dark:hover:text-amber-200"
                            >
                                {t("viewRevertPR")}
                            </Link>
                        </p>
                    </div>
                </div>
            )}

            {pr.reverts_pr_id && (
                <div className="flex items-start gap-3 rounded-2xl border border-blue-200 bg-blue-50/80 p-4.5 dark:border-blue-800 dark:bg-blue-950/20">
                    <Undo2 className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                    <div>
                        <p className="text-sm font-semibold text-blue-800 dark:text-blue-300">
                            {t("revertOfBanner")}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-blue-700/90 dark:text-blue-400/80">
                            {t.rich("revertOfBannerDesc", {
                                link: (chunks) => (
                                    <Link
                                        href={`/pull-requests/${pr.reverts_pr_id}`}
                                        className="underline font-medium hover:text-blue-800 dark:hover:text-blue-200"
                                    >
                                        {t("originalPR")}
                                    </Link>
                                ),
                            })}
                        </p>
                    </div>
                </div>
            )}

            {pr.status === "rejected" && pr.rejection_reason && (
                <div className="flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50/80 p-4.5 dark:border-red-800 dark:bg-red-950/20">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                    <div>
                        <p className="text-sm font-semibold text-red-800 dark:text-red-300">
                            {t("rejectionReasonBanner")}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-red-700/90 dark:text-red-400/80">
                            {pr.rejection_reason}
                        </p>
                    </div>
                </div>
            )}

            <div className="rounded-2xl border bg-card shadow-xs">
                <div className="space-y-4 p-5 sm:p-6">
                    <div className="flex items-start justify-between gap-4">
                        <div className="space-y-4 flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                                <span
                                    className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${status.color} ${status.bg}`}
                                >
                                    <StatusIcon className="h-3.5 w-3.5" />
                                    {t(status.labelKey as any)}
                                </span>
                            </div>

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

                    {pr.description && (
                        <ExpandableText
                            text={pr.description}
                            className="text-sm leading-relaxed text-muted-foreground"
                            clampedLines={3}
                        />
                    )}

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

            {/* Proposed Changes Section */}
            <div className="overflow-hidden rounded-2xl border bg-card shadow-xs">
                <div className="flex items-center justify-between border-b bg-muted/40 px-4 py-3 sm:px-5">
                    <span className="text-sm font-semibold text-foreground">
                        {t("proposedChanges")}
                        <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                            · {t("changeCount", { count: operations.length })}
                        </span>
                    </span>
                    {operations.length > 1 && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 text-xs text-muted-foreground hover:text-foreground"
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
                            allOperations={operations}
                        />
                    ))}
                </Accordion>
            </div>

            {/* Discussion Section */}
            <div className="overflow-hidden rounded-2xl border bg-card shadow-xs">
                <div className="border-b bg-muted/40 px-4 py-3 sm:px-5 text-sm font-semibold text-foreground">
                    {t("discussion")}
                </div>
                <div className="p-4 sm:p-5">
                    <PRComments prId={pr.id} />
                </div>
            </div>

            {/* Reject Dialog */}
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
                            {tCommon("cancel")}
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

            {/* Cancel Dialog */}
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

            {/* Revert Dialog */}
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
                            {tCommon("cancel")}
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
            <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
        </div>
    );
}

export function PRDetailPageContent() {
    return <PRDetailContent />;
}
