"use client";

import { useState } from "react";
import { MessageCircle, AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AnnotationThread,
  AnnotationForm,
} from "@/components/annotations/annotation-thread";
import { useAnnotationsContext } from "@/hooks/use-annotations";
import { useAuthStore } from "@/lib/stores";
import { isGuest } from "@/lib/guest";
import { useTranslations } from "next-intl";

interface SidebarTarget {
  type: "directory" | "material";
  id: string;
  data: Record<string, unknown>;
}

interface AnnotationsTabProps {
  target: SidebarTarget | null;
  disabled?: boolean;
}

import { ScrollArea } from "@/components/ui/scroll-area";

export function AnnotationsTab({ target, disabled = false }: AnnotationsTabProps) {
  const t = useTranslations("Sidebar");
  const { user } = useAuthStore();
  // Guests are read-only: treat them as anonymous so reply/edit/delete
  // affordances (which require a current user id) never render.
  const guest = isGuest(user);
  const ctx = useAnnotationsContext();
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");

  if (!ctx || !target || target.type !== "material") {
    return (
      <div className="p-4">
        <p className="text-sm text-muted-foreground">
          {t("annotationsOnlyForMaterials")}
        </p>
      </div>
    );
  }

  const {
    threads,
    loading,
    error,
    hasMore,
    loadMore,
    fetchAnnotations,
    createAnnotation,
    editAnnotation,
    deleteAnnotation,
  } = ctx;

  const handleReply = (annotationId: string) => {
    if (disabled) return;
    setReplyingTo(annotationId);
    setEditingId(null);
  };

  const handleSubmitReply = async (body: string) => {
    if (!replyingTo || disabled) return;
    await createAnnotation(body, undefined, undefined, undefined, replyingTo);
    setReplyingTo(null);
  };

  const handleStartEdit = (id: string, body: string) => {
    if (disabled) return;
    setEditingId(id);
    setEditBody(body);
    setReplyingTo(null);
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editBody.trim() || disabled) return;
    await editAnnotation(editingId, editBody.trim());
    setEditingId(null);
    setEditBody("");
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditBody("");
  };

  const handleDelete = async (id: string) => {
    if (disabled) return;
    await deleteAnnotation(id);
  };

  return (
    <div className="flex h-full flex-col bg-background">
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-0 divide-y divide-border/40">
          {loading && threads.length === 0 && (
            <div className="space-y-3 py-2">
              {Array.from({ length: 3 }, (_, i) => (
                <div key={i} className="space-y-1.5 rounded-md border p-2">
                  <div className="flex gap-2">
                    <Skeleton className="h-6 w-6 rounded-full" />
                    <div className="flex-1 space-y-1">
                      <Skeleton className="h-3 w-20" />
                      <Skeleton className="h-3 w-full" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {error && (
            <div className="flex flex-col items-center justify-center py-8 text-center gap-3">
              <AlertCircle className="h-6 w-6 text-destructive/60" />
              <p className="text-sm text-muted-foreground">{t("failedToLoadAnnotations")}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => fetchAnnotations(true)}
                className="gap-1.5"
              >
                <RefreshCw className="h-3 w-3" />
                {t("retry")}
              </Button>
            </div>
          )}

          {!loading && !error && threads.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <MessageCircle className="mb-3 h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                {t("noAnnotationsYet")}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("selectTextToAnnotate")}
              </p>
            </div>
          )}

          {threads.map((thread) => (
            <div key={thread.root.id} className="py-3">
              <AnnotationThread
                thread={thread}
                currentUserId={guest ? null : (user?.id ?? null)}
                currentUserRole={guest ? null : (user?.role ?? null)}
                onReply={handleReply}
                onEdit={handleStartEdit}
                onDelete={handleDelete}
                editingId={editingId}
                editBody={editBody}
                onEditBodyChange={setEditBody}
                onSaveEdit={handleSaveEdit}
                onCancelEdit={handleCancelEdit}
              />
              {replyingTo &&
                (thread.root.id === replyingTo ||
                  thread.replies.some((r) => r.id === replyingTo)) && (
                  <div className="ml-4 mt-2">
                    <AnnotationForm
                      onSubmit={handleSubmitReply}
                      placeholder={t("writeAReply")}
                      submitLabel={t("reply")}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-1"
                      onClick={() => setReplyingTo(null)}
                    >
                      {t("cancel")}
                    </Button>
                  </div>
                )}
            </div>
          ))}

          {hasMore && (
            <div className="py-3 flex justify-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={loadMore}
                disabled={loading}
                className="text-xs text-muted-foreground"
              >
                {loading ? t("loading") : t("loadMore")}
              </Button>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
