"use client";

import { useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ExternalLink, Plus, Send, Loader2, Link as LinkIcon } from "lucide-react";
import { toast } from "sonner";
import { useStagingStore, type Operation, type CreateMaterialOp } from "@/lib/staging-store";
import { TagInput } from "@/components/ui/tag-input";
import { submitDirectOperations } from "@/lib/pr-client";
import { useAuthStore } from "@/lib/stores";
import { isStaff } from "@/lib/guest";
import { sanitizeNameInput } from "@/lib/utils";
import { normalizeTargetUrl } from "@/lib/url-utils";
import { useTranslations } from "next-intl";

interface NewLinkDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** UUID of the parent directory (null for root) */
    directoryId: string | null;
    parentName?: string;
    parentMaterialId?: string | null;
}

export function NewLinkDialog({
    open,
    onOpenChange,
    directoryId,
    parentName,
    parentMaterialId,
}: NewLinkDialogProps) {
    const t = useTranslations("NewLink");
    const tAuto = useTranslations("AutoTitle");
    const addOperation = useStagingStore((s) => s.addOperation);
    const nextTempId = useStagingStore((s) => s.nextTempId);

    const [title, setTitle] = useState("");
    const [url, setUrl] = useState("");
    const [description, setDescription] = useState("");
    const [tags, setTags] = useState<string[]>([]);
    const [submitting, setSubmitting] = useState(false);

    const staff = isStaff(useAuthStore((s) => s.user));
    const NAME_MAX = 128;
    const isDraftParent = directoryId?.startsWith("$") ?? false;
    const canCreateDirectly = staff && !isDraftParent;

    const trimmedTitle = title.trim();
    const trimmedUrl = url.trim();
    const canSubmit =
        trimmedTitle.length >= 1 &&
        trimmedTitle.length <= NAME_MAX &&
        trimmedUrl.length >= 1 &&
        !submitting;

    const buildOp = (): CreateMaterialOp => {
        const tempId = nextTempId("mat");
        const normalizedUrl = normalizeTargetUrl(trimmedUrl);
        return {
            op: "create_material",
            temp_id: tempId,
            directory_id: directoryId,
            parent_material_id: parentMaterialId ?? undefined,
            title: trimmedTitle,
            type: "link",
            description: description.trim() || undefined,
            tags: tags.length > 0 ? tags : undefined,
            metadata: {
                url: normalizedUrl,
            },
        };
    };

    const handleDraft = () => {
        if (!canSubmit) return;
        addOperation(buildOp());
        toast.success(t("addedToDraft", { name: trimmedTitle }));
        resetForm();
        onOpenChange(false);
    };

    const handleDirectSubmit = async () => {
        if (!canSubmit) return;
        setSubmitting(true);
        const result = await submitDirectOperations([buildOp()], undefined, undefined, tAuto);
        setSubmitting(false);
        if (!result) return;
        resetForm();
        onOpenChange(false);
    };

    const resetForm = () => {
        setTitle("");
        setUrl("");
        setDescription("");
        setTags([]);
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey && canSubmit && e.target instanceof HTMLInputElement) {
            e.preventDefault();
            if (canCreateDirectly) {
                handleDirectSubmit();
            } else {
                handleDraft();
            }
        }
    };

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next) resetForm();
                onOpenChange(next);
            }}
        >
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <LinkIcon className="h-5 w-5 text-sky-600 dark:text-sky-400" />
                        {t("title")}
                    </DialogTitle>
                    <DialogDescription>
                        {t("descBase")}
                        {parentName ? t("descIn", { name: parentName }) : t("descRoot")}.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2" onKeyDown={handleKeyDown}>
                    {/* Title */}
                    <div className="space-y-1.5">
                        <label htmlFor="link-title" className="text-sm font-medium">
                            {t("titleLabel")}
                        </label>
                        <Input
                            id="link-title"
                            value={title}
                            onChange={(e) => setTitle(sanitizeNameInput(e.target.value))}
                            placeholder={t("titlePlaceholder")}
                            maxLength={NAME_MAX}
                            disabled={submitting}
                            autoFocus
                        />
                        <p
                            className={`text-[11px] text-right ${
                                title.length >= NAME_MAX
                                    ? "text-destructive font-semibold"
                                    : "text-muted-foreground"
                            }`}
                        >
                            {title.length}/{NAME_MAX}
                        </p>
                    </div>

                    {/* Destination URL */}
                    <div className="space-y-1.5">
                        <label htmlFor="link-url" className="text-sm font-medium">
                            {t("urlLabel")}
                        </label>
                        <div className="relative">
                            <Input
                                id="link-url"
                                value={url}
                                onChange={(e) => setUrl(e.target.value)}
                                placeholder={t("urlPlaceholder")}
                                disabled={submitting}
                                className="font-mono text-xs pl-8"
                            />
                            <ExternalLink className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                        </div>
                    </div>

                    {/* Description */}
                    <div className="space-y-1.5">
                        <label htmlFor="link-desc" className="text-sm font-medium">
                            {t("description")}{" "}
                            <span className="text-xs text-muted-foreground font-normal">
                                {t("optional")}
                            </span>
                        </label>
                        <Textarea
                            id="link-desc"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={t("descriptionPlaceholder")}
                            rows={2}
                            disabled={submitting}
                        />
                    </div>

                    {/* Tags */}
                    <div className="space-y-1.5">
                        <label className="text-sm font-medium">
                            {t("tags")}{" "}
                            <span className="text-xs text-muted-foreground font-normal">
                                {t("optional")}
                            </span>
                        </label>
                        <TagInput
                            tags={tags}
                            onChange={setTags}
                            placeholder={t("tagsPlaceholder")}
                        />
                    </div>
                </div>

                <DialogFooter className="gap-2 sm:gap-2 mt-2">
                    <Button
                        variant="ghost"
                        onClick={() => {
                            resetForm();
                            onOpenChange(false);
                        }}
                        disabled={submitting}
                        className="sm:mr-auto"
                    >
                        {t("cancel")}
                    </Button>
                    <Button
                        variant="outline"
                        onClick={handleDraft}
                        disabled={!canSubmit}
                        className="gap-2 border-dashed border-primary/50 text-primary hover:bg-primary/5"
                    >
                        <Plus className="h-4 w-4" />
                        {t("draft")}
                    </Button>
                    {canCreateDirectly && (
                        <Button
                            onClick={handleDirectSubmit}
                            disabled={!canSubmit}
                            className="gap-2"
                        >
                            {submitting ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                            {t("createDirectly")}
                        </Button>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
