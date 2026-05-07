"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { formatDistanceToNow } from "date-fns";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Edit2, MessageSquare, Send } from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/lib/stores";
import { useIsMobile } from "@/hooks/use-media-query";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { FlagButton } from "@/components/flags/flag-button";
import { ExpandableText } from "@/components/ui/expandable-text";
import { useTranslations, useLocale } from "next-intl";
import { fr, enUS } from "date-fns/locale";

interface PRComment {
    id: string;
    body: string;
    author_id: string | null;
    author: { id: string; display_name: string } | null;
    created_at: string;
}

const MAX_COMMENT_LENGTH = 1000;

export function PRComments({ prId }: { prId: string }) {
    const t = useTranslations("Comments");
    const tCommon = useTranslations("Common");
    const locale = useLocale();
    const dateLocale = locale === "fr" ? fr : enUS;
    const isMobile = useIsMobile();
    const { user } = useAuthStore();
    const [comments, setComments] = useState<PRComment[]>([]);
    const [body, setBody] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editBody, setEditBody] = useState("");

    const fetchComments = async () => {
        try {
            const res = await apiFetch<PRComment[]>(
                `/pull-requests/${prId}/comments`,
            );
            setComments(res);
        } catch {}
    };

    useEffect(() => {
        fetchComments();
         
    }, [prId]);

    const handleSubmit = async () => {
        if (!body.trim()) return;
        if (body.length > MAX_COMMENT_LENGTH) {
            toast.error(t("characterLimit", { limit: MAX_COMMENT_LENGTH.toLocaleString() }));
            return;
        }
        setSubmitting(true);
        try {
            const newComment = await apiFetch<PRComment>(`/pull-requests/${prId}/comments`, {
                method: "POST",
                body: JSON.stringify({ body }),
            });
            setBody("");
            setComments((prev) => [...prev, newComment]);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failedToPost"));
        } finally {
            setSubmitting(false);
        }
    };

    const handleEdit = async (id: string) => {
        if (!editBody.trim()) return;
        if (editBody.length > MAX_COMMENT_LENGTH) {
            toast.error(t("characterLimit", { limit: MAX_COMMENT_LENGTH.toLocaleString() }));
            return;
        }
        try {
            const updated = await apiFetch<PRComment>(`/pr-comments/${id}`, {
                method: "PATCH",
                body: JSON.stringify({ body: editBody.trim() }),
            });
            setEditingId(null);
            setEditBody("");
            setComments((prev) => prev.map((c) => (c.id === id ? updated : c)));
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failedToEdit"));
        }
    };

    const handleDelete = async (id: string) => {
        try {
            await apiFetch(`/pr-comments/${id}`, { method: "DELETE" });
            setComments((prev) => prev.filter((c) => c.id !== id));
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failedToDelete"));
        }
    };

    const startEdit = (id: string, currentBody: string) => {
        setEditingId(id);
        setEditBody(currentBody);
    };

    const getInitials = (name: string) =>
        name
            .split(" ")
            .map((w) => w[0])
            .join("")
            .slice(0, 2)
            .toUpperCase();

    const currentUserId = user?.id ?? null;
    const currentUserRole = user?.role ?? null;
    const isModerator =
        currentUserRole === "moderator" ||
        currentUserRole === "bureau" ||
        currentUserRole === "vieux";

    return (
        <div className="space-y-4">
            {comments.length === 0 && (
                <div className="flex flex-col items-center gap-2 py-6 text-muted-foreground">
                    <MessageSquare className="h-8 w-8 opacity-40" />
                    <p className="text-sm">
                        {t("noComments")}
                    </p>
                </div>
            )}

            {comments.map((c) => {
                const isAuthor = currentUserId && c.author_id === currentUserId;
                const canEdit = isAuthor;
                const canDelete = isAuthor || isModerator;

                if (editingId === c.id) {
                    return (
                        <div key={c.id} className="flex gap-3">
                            <Avatar size="sm" className="mt-0.5 shrink-0">
                                <AvatarFallback className="text-[10px]">
                                    {c.author?.display_name
                                        ? getInitials(c.author.display_name)
                                        : "?"}
                                </AvatarFallback>
                            </Avatar>
                            <div className="min-w-0 flex-1 space-y-2">
                                <Textarea
                                    value={editBody}
                                    onChange={(e) => setEditBody(e.target.value)}
                                    className="min-h-[60px] text-sm"
                                />
                                <span className={`text-[10px] ${editBody.length > MAX_COMMENT_LENGTH ? "text-destructive font-medium" : "text-muted-foreground"}`}>
                                    {editBody.length.toLocaleString()}/{MAX_COMMENT_LENGTH.toLocaleString()}
                                </span>
                                <div className="flex gap-2">
                                    <Button
                                        size="sm"
                                        onClick={() => handleEdit(c.id)}
                                        disabled={!editBody.trim() || editBody.length > MAX_COMMENT_LENGTH}
                                    >
                                        {t("save")}
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => setEditingId(null)}
                                    >
                                        {t("cancel")}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    );
                }

                return (
                    <div key={c.id} className="group flex gap-3">
                        <Avatar size="sm" className="mt-0.5 shrink-0">
                            <AvatarFallback className="text-[10px]">
                                {c.author?.display_name
                                    ? getInitials(c.author.display_name)
                                    : "?"}
                            </AvatarFallback>
                        </Avatar>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-baseline gap-2">
                                <span className="text-sm font-medium">
                                    {c.author?.display_name || tCommon("deletedUser")}
                                </span>
                                <span className="text-xs text-muted-foreground">
                                    {formatDistanceToNow(new Date(c.created_at), {
                                        addSuffix: true,
                                        locale: dateLocale,
                                    })}
                                </span>
                            </div>
                            <ExpandableText
                                text={c.body}
                                clampedLines={4}
                                className="mt-1 text-sm leading-relaxed"
                                showMoreLabel={t("seeMore")}
                                showLessLabel={t("seeLess")}
                            />
                            <div className="mt-1 flex flex-wrap gap-1">
                                {canEdit && (
                                    <button
                                        onClick={() => startEdit(c.id, c.body)}
                                        className="flex items-center gap-1 rounded px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground"
                                    >
                                        <Edit2 className="h-3 w-3" />
                                        {t("edit")}
                                    </button>
                                )}
                                {canDelete ? (
                                    <ConfirmDeleteDialog
                                        onConfirm={() => handleDelete(c.id)}
                                        title={t("delete")}
                                        description={t("deleteConfirm")}
                                    />
                                ) : (
                                    <FlagButton
                                        targetType="pr_comment"
                                        targetId={c.id}
                                        variant="ghost"
                                        className="h-auto p-0 px-1 py-0.5 text-[10px] font-normal text-muted-foreground hover:bg-muted hover:text-foreground gap-1"
                                        iconClassName="h-3 w-3"
                                    />
                                )}
                            </div>
                        </div>
                    </div>
                );
            })}

            {/* Comment form */}
            <div className="space-y-2 pt-2">
                <Textarea
                    placeholder={t("placeholder")}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    maxLength={MAX_COMMENT_LENGTH}
                    className="flex-1 text-sm bg-muted/40 focus-visible:bg-background transition-all resize-none overflow-hidden py-2"
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !isMobile) {
                            e.preventDefault();
                            handleSubmit();
                        }
                    }}
                    style={{ height: "auto" }}
                    onInput={(e) => {
                        const target = e.target as HTMLTextAreaElement;
                        target.style.height = "auto";
                        target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
                    }}
                />
                <div className="flex items-center justify-between px-0.5">
                    <div className="flex flex-col gap-0.5">
                        <span className={`text-[10px] ${body.length > MAX_COMMENT_LENGTH ? "text-destructive font-medium" : "text-muted-foreground"}`}>
                            {body.length.toLocaleString()}/{MAX_COMMENT_LENGTH.toLocaleString()}
                        </span>
                        {!isMobile && (
                            <span className="text-[9px] text-muted-foreground italic opacity-70">
                                {tCommon("shiftEnterForNewLine")}
                            </span>
                        )}
                    </div>
                    <Button
                        size="sm"
                        disabled={!body.trim() || submitting || body.length > MAX_COMMENT_LENGTH}
                        onClick={handleSubmit}
                        className="gap-1.5"
                    >
                        <Send className="h-3.5 w-3.5" />
                        {submitting ? t("posting") : t("post")}
                    </Button>
                </div>
            </div>
        </div>
    );
}
