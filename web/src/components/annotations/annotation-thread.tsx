"use client";

import { useState } from "react";
import { Edit2, Reply, Send } from "lucide-react";
import { FlagButton } from "@/components/flags/flag-button";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import Link from "next/link";
import { useIsMobile } from "@/hooks/use-media-query";
import { toast } from "sonner";
import type { AnnotationData, ThreadData } from "@/hooks/use-annotations";
import { API_BASE } from "@/lib/api-client";
import { useTranslations } from "next-intl";

function getInitials(name: string | null): string {
    if (!name) return "?";
    return name
        .split(" ")
        .map((s) => s[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);
}

import { ExpandableText } from "@/components/ui/expandable-text";

function AnnotationItem({
    annotation,
    currentUserId,
    currentUserRole,
    onReply,
    onEdit,
    onDelete,
    replyToAnnotation,
    editingId,
    editBody,
    onEditBodyChange,
    onSaveEdit,
    onCancelEdit,
}: {
    annotation: AnnotationData;
    currentUserId: string | null;
    currentUserRole: string | null;
    onReply: (id: string) => void;
    onEdit: (id: string, body: string) => void;
    onDelete: (id: string) => void;
    replyToAnnotation?: AnnotationData | null;
    editingId: string | null;
    editBody: string;
    onEditBodyChange: (v: string) => void;
    onSaveEdit: () => Promise<void>;
    onCancelEdit: () => void;
}) {
    const isAuthor = !!currentUserId && annotation.author_id === currentUserId;
    const isModerator =
        currentUserRole === "moderator" ||
        currentUserRole === "bureau" ||
        currentUserRole === "vieux";
    const canEdit = isAuthor;
    const canDelete = isAuthor || isModerator;
    const t = useTranslations("Annotations");
    const tSidebar = useTranslations("Sidebar");

    const isEditing = editingId === annotation.id;

    const authorName = annotation.author?.display_name ?? t("deletedUser");
    const date = new Date(annotation.created_at);
    const isEdited = annotation.updated_at !== annotation.created_at;

    return (
        <div className="group flex gap-2 py-3">
            {annotation.author_id ? (
                <Link href={`/profile/${annotation.author_id}`} className="shrink-0 mt-0.5">
                    <Avatar className="h-6 w-6 shrink-0 transition-opacity hover:opacity-80">
                        <AvatarImage
                            src={
                                annotation.author?.avatar_url && annotation.author_id
                                    ? `${API_BASE}/users/${annotation.author_id}/avatar?v=${encodeURIComponent(annotation.author.avatar_url)}`
                                    : undefined
                            }
                        />
                        <AvatarFallback className="text-[9px]">
                            {getInitials(annotation.author?.display_name ?? null)}
                        </AvatarFallback>
                    </Avatar>
                </Link>
            ) : (
                <Avatar className="h-6 w-6 shrink-0 mt-0.5">
                    <AvatarFallback className="text-[9px]">
                        {getInitials(annotation.author?.display_name ?? null)}
                    </AvatarFallback>
                </Avatar>
            )}
            <div className="min-w-0 flex-1">
                {replyToAnnotation && (
                    <p className="mb-0.5 text-[10px] text-muted-foreground italic">
                        {t("replyingTo", { name: replyToAnnotation.author?.display_name ?? t("deletedUser") })}
                    </p>
                )}
                <div className="flex items-baseline gap-2">
                    {annotation.author_id ? (
                        <Link href={`/profile/${annotation.author_id}`} className="text-xs font-semibold truncate hover:underline">
                            {authorName}
                        </Link>
                    ) : (
                        <span className="text-xs font-semibold truncate">{authorName}</span>
                    )}
                    <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums opacity-80">
                        {date.toLocaleDateString()}
                        {isEdited && (
                            <span className="ml-1 italic opacity-70">({t("edited")})</span>
                        )}
                    </span>
                </div>

                {isEditing ? (
                    <div className="mt-1.5 space-y-2 rounded-lg border bg-muted/30 p-2 shadow-sm">
                        <Textarea
                            value={editBody}
                            onChange={(e) => onEditBodyChange(e.target.value.slice(0, 1000))}
                            className="min-h-[80px] text-xs focus-visible:ring-1 bg-background py-2"
                            autoFocus
                        />
                        <div className="flex items-center justify-between">
                            <span
                                className={`text-[10px] ${editBody.length >= 1000 ? "text-destructive font-bold" : "text-muted-foreground"}`}
                            >
                                {editBody.length.toLocaleString()}/1,000
                            </span>
                            <div className="flex gap-2">
                                <Button
                                    size="sm"
                                    onClick={onSaveEdit}
                                    disabled={!editBody.trim() || editBody.length > 1000}
                                >
                                    {tSidebar("saveChanges")}
                                </Button>
                                <Button variant="ghost" size="sm" onClick={onCancelEdit}>
                                    {t("cancel")}
                                </Button>
                            </div>
                        </div>
                    </div>
                ) : (
                    <ExpandableText text={annotation.body} threshold={180} clampedLines={5} className="text-xs text-foreground/90 leading-normal" />
                )}

                {!isEditing && (
                    <div className="mt-1.5 flex flex-wrap gap-2 items-center">
                        {currentUserId && (
                            <button
                                onClick={() => onReply(annotation.id)}
                                className="flex items-center gap-1.5 rounded px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                            >
                                <Reply className="h-3 w-3" />
                                {t("reply")}
                            </button>
                        )}
                        {canEdit && (
                            <button
                                onClick={() => onEdit(annotation.id, annotation.body)}
                                className="flex items-center gap-1.5 rounded px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                            >
                                <Edit2 className="h-3 w-3" />
                                {t("edit")}
                            </button>
                        )}
                        {canDelete && (
                            <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center">
                                <ConfirmDeleteDialog
                                    onConfirm={() => onDelete(annotation.id)}
                                    title={t("deleteAnnotation")}
                                    description={t("deleteAnnotationConfirm")}
                                />
                            </div>
                        )}
                        {!isAuthor && (
                            <FlagButton
                                targetType="annotation"
                                targetId={annotation.id}
                                variant="ghost"
                                className="h-auto p-0 px-1.5 py-1 text-[10px] font-normal text-muted-foreground hover:bg-muted hover:text-foreground gap-1 transition-colors"
                                iconClassName="h-3 w-3"
                            />
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

interface AnnotationThreadProps {
    thread: ThreadData;
    currentUserId: string | null;
    currentUserRole: string | null;
    onReply: (annotationId: string) => void;
    onEdit: (annotationId: string, body: string) => void;
    onDelete: (annotationId: string) => void;
    editingId: string | null;
    editBody: string;
    onEditBodyChange: (v: string) => void;
    onSaveEdit: () => Promise<void>;
    onCancelEdit: () => void;
}

export function AnnotationThread({
    thread,
    currentUserId,
    currentUserRole,
    onReply,
    onEdit,
    onDelete,
    editingId,
    editBody,
    onEditBodyChange,
    onSaveEdit,
    onCancelEdit,
}: AnnotationThreadProps) {
    const t = useTranslations("Annotations");
    const allAnnotations = [thread.root, ...thread.replies];
    const annotationMap = new Map(allAnnotations.map((a) => [a.id, a]));

    const editProps = { editingId, editBody, onEditBodyChange, onSaveEdit, onCancelEdit };

    return (
        <div className="rounded-lg border bg-muted/10 p-3 shadow-sm hover:border-primary/20 transition-colors">
            {thread.root.selection_text && (
                <div className="mb-2.5 border-l-2 border-yellow-400 bg-yellow-400/5 px-2 py-1 rounded-r-md">
                    <span className="block text-[9px] font-bold text-yellow-600 uppercase tracking-tight mb-0.5">{t("selection")}</span>
                    <ExpandableText
                        text={thread.root.selection_text}
                        threshold={150}
                        clampedLines={3}
                        className="text-[10px] italic text-muted-foreground leading-relaxed"
                    />
                </div>
            )}
            <AnnotationItem
                annotation={thread.root}
                currentUserId={currentUserId}
                currentUserRole={currentUserRole}
                onReply={onReply}
                onEdit={onEdit}
                onDelete={onDelete}
                {...editProps}
            />
            {thread.replies.length > 0 && (
                <div className="ml-5 border-l-2 pl-3.5 space-y-1 mt-1 border-muted">
                    {thread.replies.map((reply) => (
                        <AnnotationItem
                            key={reply.id}
                            annotation={reply}
                            currentUserId={currentUserId}
                            currentUserRole={currentUserRole}
                            onReply={onReply}
                            onEdit={onEdit}
                            onDelete={onDelete}
                            replyToAnnotation={
                                reply.reply_to_id
                                    ? annotationMap.get(reply.reply_to_id)
                                    : undefined
                            }
                            {...editProps}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

interface AnnotationFormProps {
    onSubmit: (body: string) => Promise<void>;
    maxLength?: number;
    placeholder?: string;
    submitLabel?: string;
}

export function AnnotationForm({
    onSubmit,
    maxLength = 1000,
    placeholder,
    submitLabel,
}: AnnotationFormProps) {
    const t = useTranslations("Annotations");
    const isMobile = useIsMobile();
    const [body, setBody] = useState("");
    const [submitting, setSubmitting] = useState(false);

    const effectivePlaceholder = placeholder ?? t("writeAnAnnotation");

    const handleSubmit = async () => {
        if (!body.trim() || submitting) return;
        setSubmitting(true);
        try {
            await onSubmit(body.trim());
            setBody("");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failedToSubmit"));
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="space-y-2 group">
            <Textarea
                value={body}
                onChange={(e) => setBody(e.target.value.slice(0, maxLength))}
                placeholder={effectivePlaceholder}
                className="min-h-[50px] text-xs bg-muted/30 focus-visible:bg-background transition-all py-2"
                onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !isMobile) {
                        e.preventDefault();
                        handleSubmit();
                    }
                }}
            />
            <div className="flex items-center justify-between">
                <div className="flex flex-col gap-0.5">
                    <span className={`text-[10px] ${body.length >= maxLength ? "text-destructive font-bold" : "text-muted-foreground"}`}>
                        {body.length}/{maxLength}
                    </span>
                    {!isMobile && (
                        <span className="text-[9px] text-muted-foreground italic opacity-70">
                            {t("shiftEnterForNewLine")}
                        </span>
                    )}
                </div>
                <Button
                    size="sm"
                    onClick={handleSubmit}
                    disabled={submitting || !body.trim()}
                    className="h-8 shadow-sm"
                >
                    {submitLabel ?? <Send className="h-3.5 w-3.5" />}
                </Button>
            </div>
        </div>
    );
}
