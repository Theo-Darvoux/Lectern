"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import "katex/dist/katex.min.css";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  ChevronRight,
  ChevronLeft,
  Eye,
  Trophy,
  RotateCcw,
  MessageSquare,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { validateQCMFile } from "@/lib/qcm-utils";
import type { QCMFile, QCMQuestion } from "@/lib/qcm-types";
import { getMaterialFileUrl } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import {
  useAnnotationsContext,
  type ThreadData,
  type AnnotationsAPI,
} from "@/hooks/use-annotations";
import {
  AnnotationThread,
  AnnotationForm,
} from "@/components/annotations/annotation-thread";
import { useAuthStore } from "@/lib/stores";

// ─────────────────────────────────────────────────────────────────────────────
// Markdown + KaTeX renderer
// ─────────────────────────────────────────────────────────────────────────────

function wrapBareEnvironments(content: string): string {
  let out = content.replace(
    /(?<!\$)\\begin\{(equation\*?)\}([\s\S]*?)\\end\{equation\*?\}/g,
    (_match, _env, inner: string) => `$$\n${inner.trim()}\n$$`,
  );
  out = out.replace(
    /(?<!\$)(\\begin\{(?!equation)[\w*]+\}[\s\S]*?\\end\{[\w*]+\})/g,
    "$$\n$1\n$$",
  );
  return out;
}

function MathMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: "#c00" }]]}
    >
      {wrapBareEnvironments(content)}
    </ReactMarkdown>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface QCMViewerProps {
  fileKey?: string;
  materialId?: string;
  /** Skip the /inline presigned-URL step and fetch directly from this URL. */
  directUrl?: string;
  /** Skip all fetching and render from this pre-loaded data directly. */
  initialData?: QCMFile;
}

interface QuestionState {
  selected: Set<string>;
  revealed: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Question card
// ─────────────────────────────────────────────────────────────────────────────

interface QuestionAnnotationsProps {
  questionId: string;
  threads: ThreadData[];
  api: AnnotationsAPI;
  currentUserId: string | null;
  currentUserRole: string | null;
}

function QuestionAnnotations({
  questionId,
  threads,
  api,
  currentUserId,
  currentUserRole,
}: QuestionAnnotationsProps) {
  const t = useTranslations("QCM.viewer");
  const tAnn = useTranslations("Annotations");
  const [open, setOpen] = useState(false);
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");

  const handleCreate = async (body: string) => {
    await api.createAnnotation(body, undefined, { question_id: questionId });
    setOpen(true);
  };

  const handleReply = (annotationId: string) => {
    setReplyingTo(annotationId);
    setEditingId(null);
  };

  const handleStartEdit = (id: string, body: string) => {
    setEditingId(id);
    setEditBody(body);
    setReplyingTo(null);
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editBody.trim()) return;
    await api.editAnnotation(editingId, editBody.trim());
    setEditingId(null);
    setEditBody("");
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditBody("");
  };

  const handleSubmitReply = async (body: string) => {
    if (!replyingTo) return;
    await api.createAnnotation(body, undefined, { question_id: questionId }, undefined, replyingTo);
    setReplyingTo(null);
  };

  return (
    <div className="pl-6 mt-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors py-1"
      >
        <MessageSquare className="h-3.5 w-3.5" />
        <span>{t("notes")}</span>
        {threads.length > 0 && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium leading-none">
            {threads.length}
          </span>
        )}
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>

      {open && (
        <div className="mt-2 space-y-3">
          {threads.length === 0 && (
            <p className="text-xs text-muted-foreground italic">{t("noNotes")}</p>
          )}
          {threads.map((thread) => (
            <div key={thread.root.id}>
              <AnnotationThread
                thread={thread}
                currentUserId={currentUserId}
                currentUserRole={currentUserRole}
                onReply={handleReply}
                onEdit={handleStartEdit}
                onDelete={api.deleteAnnotation}
                editingId={editingId}
                editBody={editBody}
                onEditBodyChange={setEditBody}
                onSaveEdit={handleSaveEdit}
                onCancelEdit={handleCancelEdit}
              />
              {replyingTo &&
                (thread.root.id === replyingTo ||
                  thread.replies.some((r) => r.id === replyingTo)) && (
                  <div className="ml-4 mt-2 space-y-1">
                    <AnnotationForm
                      onSubmit={handleSubmitReply}
                      placeholder={tAnn("writeAnAnnotation")}
                      submitLabel={tAnn("reply")}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={() => setReplyingTo(null)}
                    >
                      {tAnn("cancel")}
                    </Button>
                  </div>
                )}
            </div>
          ))}
          {currentUserId && (
            <AnnotationForm onSubmit={handleCreate} placeholder={t("addNote")} />
          )}
        </div>
      )}
    </div>
  );
}

interface QuestionCardProps {
  question: QCMQuestion;
  questionNumber: number;
  state: QuestionState;
  onToggleAnswer: (answerId: string) => void;
  onReveal: () => void;
  annotationThreads?: ThreadData[];
  annotationsApi?: AnnotationsAPI | null;
  currentUserId?: string | null;
  currentUserRole?: string | null;
}

function QuestionCard({
  question,
  questionNumber,
  state,
  onToggleAnswer,
  onReveal,
  annotationThreads,
  annotationsApi,
  currentUserId,
  currentUserRole,
}: QuestionCardProps) {
  const t = useTranslations("QCM.viewer");
  const { selected, revealed } = state;

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="flex items-start gap-2">
        <span className="text-xs font-semibold text-muted-foreground shrink-0 mt-1">
          Q{questionNumber}
        </span>
        <div className="prose prose-sm dark:prose-invert max-w-none flex-1 text-sm">
          <MathMarkdown content={question.text} />
        </div>
      </div>

      <div className="space-y-2 pl-6">
        {question.answers.map((answer) => {
          const isSelected = selected.has(answer.id);

          let variantClass =
            "border-muted bg-background text-foreground hover:bg-accent/50 cursor-pointer";

          if (revealed) {
            if (answer.correct && isSelected) {
              variantClass =
                "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-200 dark:border-green-600";
            } else if (answer.correct && !isSelected) {
              variantClass =
                "border-green-400 bg-green-50/50 text-green-800 dark:bg-green-950/20 dark:text-green-300 dark:border-green-700";
            } else {
              // wrong answer (selected or not) — always red
              variantClass =
                "border-red-400 bg-red-50/60 text-red-800 dark:bg-red-950/20 dark:text-red-300 dark:border-red-700";
            }
          } else if (isSelected) {
            variantClass =
              "border-primary bg-primary/5 text-foreground dark:bg-primary/10";
          }

          return (
            <button
              key={answer.id}
              onClick={() => !revealed && onToggleAnswer(answer.id)}
              className={cn(
                "w-full text-left rounded-md border px-3 py-2 text-sm transition-colors",
                variantClass,
                revealed && "cursor-default",
              )}
              disabled={revealed}
            >
              <div className="flex items-start gap-2">
                {revealed ? (
                  answer.correct ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0 mt-0.5" />
                  ) : (
                    <XCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                  )
                ) : (
                  <span
                    className={cn(
                      "mt-0.5 h-4 w-4 shrink-0 rounded-sm border-2 transition-colors",
                      isSelected
                        ? "border-primary bg-primary"
                        : "border-muted-foreground/40",
                    )}
                  />
                )}
                <div className="prose prose-sm dark:prose-invert max-w-none flex-1 select-text">
                  <MathMarkdown content={answer.text} />
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {!revealed && (
        <div className="pl-6 flex items-center gap-2">
          <Button
            size="sm"
            variant="default"
            className="text-xs h-7"
            disabled={selected.size === 0}
            onClick={onReveal}
          >
            <CheckCircle2 className="h-3 w-3 mr-1" />
            {t("validate")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-xs h-7"
            onClick={onReveal}
          >
            <Eye className="h-3 w-3 mr-1" />
            {t("reveal")}
          </Button>
        </div>
      )}

      {revealed && question.explanation && (
        <div className="pl-6 rounded-md bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 p-3">
          <p className="text-xs font-semibold text-blue-700 dark:text-blue-300 mb-1">
            {t("explanation")}
          </p>
          <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-blue-900 dark:text-blue-200">
            <MathMarkdown content={question.explanation} />
          </div>
        </div>
      )}

      {revealed && annotationsApi && (
        <QuestionAnnotations
          questionId={question.id}
          threads={annotationThreads ?? []}
          api={annotationsApi}
          currentUserId={currentUserId ?? null}
          currentUserRole={currentUserRole ?? null}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Results view
// ─────────────────────────────────────────────────────────────────────────────

interface ResultsViewProps {
  qcm: QCMFile;
  questionStates: Record<string, QuestionState>;
  onRetry: () => void;
}

function ResultsView({ qcm, questionStates, onRetry }: ResultsViewProps) {
  const t = useTranslations("QCM.viewer");

  const chapterStats = qcm.chapters.map((ch) => {
    let correct = 0;
    for (const q of ch.questions) {
      const state = questionStates[q.id];
      const correctIds = new Set(
        q.answers.filter((a) => a.correct).map((a) => a.id),
      );
      const isCorrect =
        state &&
        state.selected.size === correctIds.size &&
        [...state.selected].every((id) => correctIds.has(id));
      if (isCorrect) correct++;
    }
    return { chapter: ch, correct, total: ch.questions.length };
  });

  const totalCorrect = chapterStats.reduce((s, c) => s + c.correct, 0);
  const totalQuestions = chapterStats.reduce((s, c) => s + c.total, 0);
  const pct =
    totalQuestions > 0 ? Math.round((totalCorrect / totalQuestions) * 100) : 0;

  return (
    <div className="flex flex-col items-center gap-8 p-4 sm:p-8 w-full max-w-lg mx-auto">
      <div className="flex flex-col items-center gap-2">
        <Trophy className="h-10 w-10 text-amber-500" />
        <h2 className="text-2xl font-bold">{t("results")}</h2>
        <p className="text-5xl font-bold tabular-nums mt-2">
          {totalCorrect}
          <span className="text-muted-foreground text-3xl">/{totalQuestions}</span>
        </p>
        <p className="text-muted-foreground text-sm">{pct}%</p>
        <Progress value={pct} className="w-48 mt-1" />
      </div>

      {qcm.chapters.length > 1 && (
        <div className="w-full space-y-2">
          {chapterStats.map(({ chapter, correct, total }, i) => {
            const chPct = total > 0 ? Math.round((correct / total) * 100) : 0;
            return (
              <div key={chapter.id} className="rounded-md border bg-card px-4 py-3">
                <div className="flex items-center justify-between gap-3 mb-1.5">
                  <span className="text-sm font-medium truncate">
                    {chapter.title || `${t("chapter")} ${i + 1}`}
                  </span>
                  <span className="text-sm font-semibold shrink-0 tabular-nums">
                    {correct}/{total}
                  </span>
                </div>
                <Progress value={chPct} className="h-1.5" />
              </div>
            );
          })}
        </div>
      )}

      <Button onClick={onRetry} variant="outline" className="gap-2">
        <RotateCcw className="h-4 w-4" />
        {t("retry")}
      </Button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main viewer
// ─────────────────────────────────────────────────────────────────────────────

export function QCMViewer({ fileKey, materialId, directUrl, initialData }: QCMViewerProps) {
  const t = useTranslations("QCM.viewer");
  const annotationsApi = useAnnotationsContext();
  const { user } = useAuthStore();

  const [qcm, setQcm] = useState<QCMFile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [questionStates, setQuestionStates] = useState<
    Record<string, QuestionState>
  >({});
  const [page, setPage] = useState(0);
  const [finished, setFinished] = useState(false);

  // Prevent external initialData changes from wiping in-progress user state.
  const hasStartedRef = useRef(false);

  useEffect(() => {
    if (initialData) {
      // If the user has already started answering, silently ignore upstream changes.
      if (hasStartedRef.current) return;
      setQcm(initialData);
      const states: Record<string, QuestionState> = {};
      for (const ch of initialData.chapters) {
        for (const q of ch.questions) {
          states[q.id] = { selected: new Set(), revealed: false };
        }
      }
      setQuestionStates(states);
      setPage(0);
      setFinished(false);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const url = directUrl ?? (materialId ? await getMaterialFileUrl(materialId) : null);
        if (!url) throw new Error("No URL available");
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!validateQCMFile(json)) throw new Error("Invalid QCM file format");
        if (!cancelled) {
          setQcm(json);
          const states: Record<string, QuestionState> = {};
          for (const ch of json.chapters) {
            for (const q of ch.questions) {
              states[q.id] = { selected: new Set(), revealed: false };
            }
          }
          setQuestionStates(states);
          setPage(0);
          setFinished(false);
        }
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load QCM");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [materialId, fileKey, directUrl, initialData]);

  const handleToggleAnswer = useCallback(
    (questionId: string, answerId: string) => {
      hasStartedRef.current = true;
      setQuestionStates((prev) => {
        const current = prev[questionId];
        if (!current || current.revealed) return prev;
        const next = new Set(current.selected);
        if (next.has(answerId)) next.delete(answerId);
        else next.add(answerId);
        return { ...prev, [questionId]: { ...current, selected: next } };
      });
    },
    [],
  );

  const handleRevealQuestion = useCallback((questionId: string) => {
    hasStartedRef.current = true;
    setQuestionStates((prev) => {
      const current = prev[questionId];
      if (!current) return prev;
      return { ...prev, [questionId]: { ...current, revealed: true } };
    });
  }, []);

  const handleRetry = useCallback(() => {
    if (!qcm) return;
    hasStartedRef.current = false;
    const states: Record<string, QuestionState> = {};
    for (const ch of qcm.chapters) {
      for (const q of ch.questions) {
        states[q.id] = { selected: new Set(), revealed: false };
      }
    }
    setQuestionStates(states);
    setPage(0);
    setFinished(false);
  }, [qcm]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !qcm) {
    return (
      <div className="flex items-center justify-center h-64 text-destructive text-sm">
        {error ?? "Failed to load QCM"}
      </div>
    );
  }

  if (qcm.chapters.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
        {t("noChapters")}
      </div>
    );
  }

  if (finished) {
    return (
      <div className="flex-1 overflow-y-auto">
        <ResultsView
          qcm={qcm}
          questionStates={questionStates}
          onRetry={handleRetry}
        />
      </div>
    );
  }

  const totalPages = qcm.chapters.length;
  const isLastPage = page === totalPages - 1;
  const currentChapter = qcm.chapters[page];

  // Global question offset for numbering
  let questionOffset = 0;
  for (let i = 0; i < page; i++) {
    questionOffset += qcm.chapters[i].questions.length;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Progress header */}
      <div className="shrink-0 px-4 pt-3 pb-2 border-b space-y-1.5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {t("chapter")} {page + 1} / {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <span>
              {currentChapter.questions.length}{" "}
              {currentChapter.questions.length === 1
                ? t("question")
                : t("questions")}
            </span>
            <button
              onClick={handleRetry}
              title={t("retry")}
              className="rounded p-0.5 hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <Progress
          value={((page + 1) / totalPages) * 100}
          className="h-1.5"
        />
        {currentChapter.title && (
          <p className="text-sm font-semibold pt-0.5">{currentChapter.title}</p>
        )}
      </div>

      {/* Questions */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {currentChapter.questions.map((q, i) => {
          const qThreads = annotationsApi?.threads.filter(
            (t) => t.root.position_data?.question_id === q.id,
          ) ?? [];
          return (
            <QuestionCard
              key={q.id}
              question={q}
              questionNumber={questionOffset + i + 1}
              state={
                questionStates[q.id] ?? { selected: new Set(), revealed: false }
              }
              onToggleAnswer={(answerId) => handleToggleAnswer(q.id, answerId)}
              onReveal={() => handleRevealQuestion(q.id)}
              annotationThreads={qThreads}
              annotationsApi={annotationsApi}
              currentUserId={user?.id ?? null}
              currentUserRole={user?.role ?? null}
            />
          );
        })}
      </div>

      {/* Navigation footer */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-t gap-3">
        <Button
          variant="ghost"
          size="sm"
          className="gap-1 shrink-0"
          disabled={page === 0}
          onClick={() => setPage((p) => p - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
          <span className="hidden sm:inline">{t("previous")}</span>
        </Button>

        {/* Dot indicators */}
        {totalPages > 1 && totalPages <= 12 && (
          <div className="flex flex-wrap justify-center gap-1.5 min-w-0">
            {qcm.chapters.map((_, i) => (
              <button
                key={i}
                onClick={() => setPage(i)}
                className={cn(
                  "h-2 rounded-full transition-all shrink-0",
                  i === page
                    ? "w-4 bg-primary"
                    : "w-2 bg-muted-foreground/30 hover:bg-muted-foreground/60",
                )}
              />
            ))}
          </div>
        )}

        {isLastPage ? (
          <Button
            size="sm"
            className="gap-1 shrink-0"
            onClick={() => setFinished(true)}
          >
            <span className="hidden sm:inline">{t("finishQcm")}</span>
            <Trophy className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="sm"
            className="gap-1 shrink-0"
            onClick={() => setPage((p) => p + 1)}
          >
            <span className="hidden sm:inline">{t("next")}</span>
            <ChevronRight className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
