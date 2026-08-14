"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Edit2, Send } from "lucide-react";
import { FlagButton } from "@/components/flags/flag-button";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch, API_BASE } from "@/lib/api-client";
import { useAuthStore } from "@/lib/stores";
import { isGuest } from "@/lib/guest";
import { useIsMobile } from "@/hooks/use-media-query";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

interface CommentAuthor {
  id: string;
  display_name: string | null;
  avatar_url: string | null;
}

interface Comment {
  id: string;
  target_type: string;
  target_id: string;
  author_id: string | null;
  author: CommentAuthor | null;
  body: string;
  created_at: string;
  updated_at: string;
}

interface PaginatedComments {
  items: Comment[];
  total: number;
  page: number;
  pages: number;
}

interface SidebarTarget {
  type: "directory" | "material";
  id: string;
  data: Record<string, unknown>;
}

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

function CommentItem({
  comment,
  currentUserId,
  currentUserRole,
  guest = false,
  onEdit,
  onDelete,
}: {
  comment: Comment;
  currentUserId: string | null;
  currentUserRole: string | null;
  guest?: boolean;
  onEdit: (id: string, body: string) => void;
  onDelete: (id: string) => void;
}) {
  const isAuthor = currentUserId && comment.author_id === currentUserId;
  const isModerator =
    currentUserRole === "member" ||
    currentUserRole === "bureau" ||
    currentUserRole === "vieux";
  const canEdit = isAuthor;
  const canDelete = isAuthor || isModerator;
  const t = useTranslations("Sidebar");

  const authorName = comment.author?.display_name ?? t("deletedUser");
  const date = new Date(comment.created_at);

  return (
    <div className="group flex gap-2.5 py-3">
      {comment.author_id ? (
        <Link href={`/profile/${comment.author_id}`} className="shrink-0">
          <Avatar className="h-7 w-7 shrink-0 transition-opacity hover:opacity-80">
            <AvatarImage
              src={
                comment.author?.avatar_url && comment.author_id
                  ? `${API_BASE}/users/${comment.author_id}/avatar?v=${encodeURIComponent(comment.author.avatar_url)}`
                  : undefined
              }
            />
            <AvatarFallback className="text-[10px]">
              {getInitials(comment.author?.display_name ?? null)}
            </AvatarFallback>
          </Avatar>
        </Link>
      ) : (
        <Avatar className="h-7 w-7 shrink-0">
          <AvatarFallback className="text-[10px]">
            {getInitials(comment.author?.display_name ?? null)}
          </AvatarFallback>
        </Avatar>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          {comment.author_id ? (
            <Link
              href={`/profile/${comment.author_id}`}
              className="text-xs font-semibold truncate hover:underline"
            >
              {authorName}
            </Link>
          ) : (
            <span className="text-xs font-semibold truncate">{authorName}</span>
          )}
          <span className="text-[10px] text-muted-foreground shrink-0 opacity-80">
            {date.toLocaleDateString()}
          </span>
        </div>
        <ExpandableText
          text={comment.body}
          className="text-xs text-foreground/90 leading-relaxed mt-0.5"
        />
        <div className={`mt-1 flex flex-wrap gap-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 ${guest ? "hidden" : ""}`}>
          {canEdit && (
            <button
              onClick={() => onEdit(comment.id, comment.body)}
              className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              <Edit2 className="h-3 w-3" />
              {t("edit")}
            </button>
          )}
          {canDelete ? (
            <ConfirmDeleteDialog
              onConfirm={() => onDelete(comment.id)}
              title={t("deleteComment")}
              description={t("deleteCommentConfirm")}
            />
          ) : (
            <FlagButton
              targetType="comment"
              targetId={comment.id}
              variant="ghost"
              className="h-auto p-0 px-1.5 py-1 text-[10px] font-normal text-muted-foreground hover:bg-muted hover:text-foreground gap-1 transition-colors"
              iconClassName="h-3 w-3"
            />
          )}
        </div>
      </div>
    </div>
  );
}

const MAX_COMMENT_LENGTH = 1000;

interface ChatTabProps {
  target: SidebarTarget | null;
  disabled?: boolean;
}

import { ScrollArea } from "@/components/ui/scroll-area";

export function ChatTab({ target, disabled = false }: ChatTabProps) {
  const t = useTranslations("Sidebar");
  const isMobile = useIsMobile();
  const user = useAuthStore((state) => state.user);
  const guest = isGuest(user);
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(false);
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    if (comments.length > 0 && !loading) {
      scrollToBottom();
    }
  }, [comments, loading]);

  const fetchComments = useCallback(
    async () => {
      if (!target) return;
      setLoading(true);
      try {
        const data = await apiFetch<PaginatedComments>(
          `/comments?targetType=${target.type}&targetId=${target.id}&page=1&limit=1000`,
        );
        setComments(data.items);
      } catch {
        // silent
      } finally {
        setLoading(false);
      }
    },
    [target],
  );

  useEffect(() => {
    setComments([]);
    if (target) fetchComments();
  }, [target, fetchComments]);

  const handleSubmit = async () => {
    if (!target || !body.trim() || disabled) return;
    if (body.length > MAX_COMMENT_LENGTH) {
      toast.error(
        t("commentExceedsLimit", { max: MAX_COMMENT_LENGTH.toLocaleString() })
      );
      return;
    }
    setSubmitting(true);
    try {
      const newComment = await apiFetch<Comment>("/comments", {
        method: "POST",
        body: JSON.stringify({
          target_type: target.type,
          target_id: target.id,
          body: body.trim(),
        }),
      });
      setBody("");
      setComments((prev) => [...prev, newComment]);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("failedToPostComment"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleEdit = async (id: string) => {
    if (!editBody.trim() || disabled) return;
    if (editBody.length > MAX_COMMENT_LENGTH) {
      toast.error(
        t("commentExceedsLimit", { max: MAX_COMMENT_LENGTH.toLocaleString() })
      );
      return;
    }
    try {
      const updatedComment = await apiFetch<Comment>(`/comments/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ body: editBody.trim() }),
      });
      setEditingId(null);
      setEditBody("");
      setComments((prev) =>
        prev.map((c) => (c.id === id ? updatedComment : c)),
      );
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("failedToEditComment"),
      );
    }
  };

  const handleDelete = async (id: string) => {
    if (disabled) return;
    try {
      await apiFetch<void>(`/comments/${id}`, { method: "DELETE" });
      setComments((prev) => prev.filter((c) => c.id !== id));
    } catch {
      // silent
    }
  };

  const startEdit = (id: string, currentBody: string) => {
    if (disabled) return;
    setEditingId(id);
    setEditBody(currentBody);
  };

  if (!target) {
    return (
      <div className="p-4">
        <p className="text-sm text-muted-foreground">
          {t("selectItemToViewChat")}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-background">
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-0 pb-12 divide-y divide-border/40">
          {loading && comments.length === 0 && (
            <div className="space-y-3 py-2">
              {Array.from({ length: 3 }, (_, i) => (
                <div key={i} className="flex gap-2.5">
                  <Skeleton className="h-7 w-7 rounded-full" />
                  <div className="flex-1 space-y-1">
                    <Skeleton className="h-3 w-24" />
                    <Skeleton className="h-3 w-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && comments.length === 0 && (
            <p className="py-12 text-center text-sm text-muted-foreground italic px-4">
              {t("noCommentsYet", { type: t(target.type) })}
            </p>
          )}

          {comments.map((c) =>
            editingId === c.id ? (
              <div
                key={c.id}
                className="space-y-3 py-3 px-2 rounded-lg border bg-muted/30 mb-2"
              >
                <div className="max-h-[300px] overflow-y-auto pr-1">
                  <Textarea
                    value={editBody}
                    onChange={(e) => setEditBody(e.target.value)}
                    className="min-h-[120px] text-sm focus-visible:ring-1"
                    autoFocus
                    disabled={disabled}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <span
                    className={`text-[10px] ${editBody.length > MAX_COMMENT_LENGTH ? "text-destructive font-bold" : "text-muted-foreground"}`}
                  >
                    {editBody.length.toLocaleString()}/
                    {MAX_COMMENT_LENGTH.toLocaleString()}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => handleEdit(c.id)}
                      disabled={
                        disabled || !editBody.trim() || editBody.length > MAX_COMMENT_LENGTH
                      }
                    >
                      {t("saveChanges")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditingId(null)}
                      disabled={disabled}
                    >
                      {t("cancel")}
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <CommentItem
                key={c.id}
                comment={c}
                currentUserId={user?.id ?? null}
                currentUserRole={user?.role ?? null}
                guest={guest}
                onEdit={startEdit}
                onDelete={handleDelete}
              />
            ),
          )}
          <div ref={messagesEndRef} />
        </div>
      </ScrollArea>

      {guest ? (
        <div className="shrink-0 border-t bg-background p-3">
          <p className="text-[11px] text-center text-muted-foreground italic">
            {t("guestReadOnly")}
          </p>
        </div>
      ) : (
      <div className="shrink-0 border-t bg-background p-3 pt-3 pb-3 md:pb-4 space-y-1.5 shadow-[0_-8px_20px_-10px_rgba(0,0,0,0.1)]">
        {disabled && (
          <p className="text-[10px] text-center text-muted-foreground bg-muted/30 py-1 rounded-sm mb-1">
            {t("chattingDisabledForPreview")}
          </p>
        )}
        <div className="flex gap-2 min-w-0">
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={disabled ? t("chattingDisabled") : t("writeAComment")}
            className="min-h-[40px] flex-1 text-xs bg-muted/40 focus-visible:bg-background transition-all resize-none overflow-hidden py-2"
            disabled={disabled}
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
          <Button
            size="icon"
            onClick={handleSubmit}
            disabled={
              disabled || submitting || !body.trim() || body.length > MAX_COMMENT_LENGTH
            }
            className="shrink-0 self-end h-10 w-10 shadow-sm transition-transform active:scale-95"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex justify-between items-center px-0.5">
          <span
            className={`text-[9px] ${body.length > MAX_COMMENT_LENGTH ? "text-destructive font-bold" : "text-muted-foreground"}`}
          >
            {body.length.toLocaleString()} /{" "}
            {MAX_COMMENT_LENGTH.toLocaleString()}
          </span>
          {!isMobile && (
            <span className="text-[9px] text-muted-foreground italic opacity-70">
              {t("shiftEnterForNewLine")}
            </span>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
