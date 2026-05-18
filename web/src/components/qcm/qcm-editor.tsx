"use client";

import React, { useState, useRef, useCallback, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import "katex/dist/katex.min.css";
import {
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Eye,
  EyeOff,
  Loader2,
  Upload,
  MonitorPlay,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { QCMViewer } from "@/components/viewers/qcm-viewer";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import type { QCMFile, QCMChapter, QCMQuestion, QCMAnswer, QCMMeta } from "@/lib/qcm-types";
import {
  MAX_ANSWERS_PER_QUESTION,
  MAX_QUESTIONS_PER_QCM,
  MAX_CHAPTERS_PER_QCM,
  MAX_LEN_TITLE,
  MAX_LEN_DESCRIPTION,
  MAX_LEN_CHAPTER_TITLE,
  MAX_LEN_QUESTION,
  MAX_LEN_ANSWER,
  MAX_LEN_EXPLANATION,
} from "@/lib/qcm-types";
import {
  createEmptyChapter,
  createEmptyQuestion,
  createEmptyAnswer,
  countQCMQuestions,
  generateQCMId,
} from "@/lib/qcm-utils";
import { apiFetch } from "@/lib/api-client";
import { useUIStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface QCMEditorProps {
  initialData?: QCMFile;
  initialMeta?: Partial<QCMMeta>;
  materialId?: string;
  targetDirectoryId?: string;
  onSubmit: (qcm: QCMFile, meta: QCMMeta) => Promise<void>;
  isSubmitting?: boolean;
  onSaveDraft?: (qcm: QCMFile, meta: QCMMeta) => Promise<void>;
  isSavingDraft?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Mini markdown preview
// ─────────────────────────────────────────────────────────────────────────────

// Normalise bare LaTeX environments so remark-math picks them up.
// \begin{equation}...\end{equation}  →  $$...$$  (equation IS display math)
// \begin{aligned}...\end{aligned}    →  $$\begin{aligned}...\end{aligned}$$
function wrapBareEnvironments(content: string): string {
  // equation / equation* — strip wrapper, keep content in $$
  let out = content.replace(
    /(?<!\$)\\begin\{(equation\*?)\}([\s\S]*?)\\end\{equation\*?\}/g,
    (_match, _env, inner: string) => `$$\n${inner.trim()}\n$$`,
  );
  // All other bare environments — keep env, wrap in $$
  out = out.replace(
    /(?<!\$)(\\begin\{(?!equation)[\w*]+\}[\s\S]*?\\end\{[\w*]+\})/g,
    "$$\n$1\n$$",
  );
  return out;
}

function CharCount({ value, max }: { value: string; max: number }) {
  const at = value.length >= max;
  const near = value.length >= max * 0.9;
  return (
    <span
      className={`text-[10px] tabular-nums ${at ? "text-destructive font-bold" : near ? "text-amber-500 dark:text-amber-400" : "text-muted-foreground"}`}
    >
      {value.length}/{max}
    </span>
  );
}

function MarkdownPreview({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none rounded-md border bg-muted/30 px-3 py-2 text-sm min-h-[2rem]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: "#c00" }]]}
      >
        {wrapBareEnvironments(content) || "*empty*"}
      </ReactMarkdown>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Answer row
// ─────────────────────────────────────────────────────────────────────────────

interface AnswerRowProps {
  answer: QCMAnswer;
  onChange: (updated: QCMAnswer) => void;
  onDelete: () => void;
  canDelete: boolean;
}

function AnswerRow({ answer, onChange, onDelete, canDelete }: AnswerRowProps) {
  const t = useTranslations("QCM.editor");
  const [preview, setPreview] = useState(false);

  return (
    <div className="space-y-1">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => onChange({ ...answer, correct: !answer.correct })}
          title={t("correct")}
          className={cn(
            "mt-2 h-4 w-4 shrink-0 rounded border-2 transition-colors",
            answer.correct
              ? "border-green-500 bg-green-500"
              : "border-muted-foreground/40 hover:border-green-400",
          )}
          aria-label={t("correct")}
        />
        <div className="flex-1 space-y-0.5">
          <Textarea
            value={answer.text}
            onChange={(e) => onChange({ ...answer, text: e.target.value.slice(0, MAX_LEN_ANSWER) })}
            placeholder={t("answerText")}
            className="min-h-[2.5rem] resize-none text-sm"
            rows={1}
            maxLength={MAX_LEN_ANSWER}
          />
          <div className="flex justify-end">
            <CharCount value={answer.text} max={MAX_LEN_ANSWER} />
          </div>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0 mt-1 text-muted-foreground hover:text-primary"
          onClick={() => setPreview((p) => !p)}
          title={t("preview")}
        >
          {preview ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
        </Button>
        {canDelete && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 shrink-0 mt-1 text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            title={t("deleteAnswer")}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        )}
      </div>
      {preview && answer.text && (
        <div className="ml-6">
          <MarkdownPreview content={answer.text} />
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Question card
// ─────────────────────────────────────────────────────────────────────────────

interface QuestionEditorProps {
  question: QCMQuestion;
  questionNumber: number;
  onChange: (updated: QCMQuestion) => void;
  onDelete: () => void;
  onMoveUp: (() => void) | null;
  onMoveDown: (() => void) | null;
}

function QuestionEditor({
  question,
  questionNumber,
  onChange,
  onDelete,
  onMoveUp,
  onMoveDown,
}: QuestionEditorProps) {
  const t = useTranslations("QCM.editor");
  const [previewText, setPreviewText] = useState(false);
  const [showExplanation, setShowExplanation] = useState(
    !!question.explanation,
  );
  const [previewExplanation, setPreviewExplanation] = useState(false);

  const updateAnswer = (index: number, updated: QCMAnswer) => {
    const answers = [...question.answers];
    answers[index] = updated;
    onChange({ ...question, answers });
  };

  const deleteAnswer = (index: number) => {
    if (question.answers.length <= 1) return;
    const answers = question.answers.filter((_, i) => i !== index);
    onChange({ ...question, answers });
  };

  const addAnswer = () => {
    if (question.answers.length >= MAX_ANSWERS_PER_QUESTION) {
      toast.warning(t("maxAnswers"));
      return;
    }
    onChange({ ...question, answers: [...question.answers, createEmptyAnswer()] });
  };

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-muted-foreground">
          Q{questionNumber}
        </span>
        <div className="flex items-center gap-1 ml-auto">
          {onMoveUp && (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-6 w-6"
              onClick={onMoveUp}
            >
              <ChevronUp className="h-3 w-3" />
            </Button>
          )}
          {onMoveDown && (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-6 w-6"
              onClick={onMoveDown}
            >
              <ChevronDown className="h-3 w-3" />
            </Button>
          )}
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-6 w-6 text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            title={t("deleteQuestion")}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* Question text */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Label className="text-xs">{t("questionText")}</Label>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-5 px-1 text-xs text-muted-foreground"
            onClick={() => setPreviewText((p) => !p)}
          >
            {previewText ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
          </Button>
        </div>
        <Textarea
          value={question.text}
          onChange={(e) => onChange({ ...question, text: e.target.value.slice(0, MAX_LEN_QUESTION) })}
          placeholder={t("questionText")}
          className="min-h-[4rem] text-sm"
          rows={2}
          maxLength={MAX_LEN_QUESTION}
        />
        <div className="flex justify-end">
          <CharCount value={question.text} max={MAX_LEN_QUESTION} />
        </div>
        {previewText && <MarkdownPreview content={question.text} />}
      </div>

      {/* Answers */}
      <div className="space-y-2">
        {question.answers.map((answer, i) => (
          <AnswerRow
            key={answer.id}
            answer={answer}
            onChange={(updated) => updateAnswer(i, updated)}
            onDelete={() => deleteAnswer(i)}
            canDelete={question.answers.length > 1}
          />
        ))}
        {question.answers.length < MAX_ANSWERS_PER_QUESTION && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-xs h-7 gap-1 text-muted-foreground"
            onClick={addAnswer}
          >
            <Plus className="h-3 w-3" />
            {t("addAnswer")}
          </Button>
        )}
      </div>

      {/* Explanation */}
      <div>
        {!showExplanation ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-xs h-6 text-muted-foreground"
            onClick={() => setShowExplanation(true)}
          >
            + {t("explanation")}
          </Button>
        ) : (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Label className="text-xs">{t("explanation")}</Label>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-5 px-1 text-xs text-muted-foreground"
                onClick={() => setPreviewExplanation((p) => !p)}
              >
                {previewExplanation ? (
                  <EyeOff className="h-3 w-3" />
                ) : (
                  <Eye className="h-3 w-3" />
                )}
              </Button>
            </div>
            <Textarea
              value={question.explanation ?? ""}
              onChange={(e) =>
                onChange({
                  ...question,
                  explanation: e.target.value.slice(0, MAX_LEN_EXPLANATION) || undefined,
                })
              }
              placeholder={t("explanation")}
              className="min-h-[3rem] text-sm"
              rows={2}
              maxLength={MAX_LEN_EXPLANATION}
            />
            <div className="flex justify-end">
              <CharCount value={question.explanation ?? ""} max={MAX_LEN_EXPLANATION} />
            </div>
            {previewExplanation && (
              <MarkdownPreview content={question.explanation ?? ""} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Chapter editor
// ─────────────────────────────────────────────────────────────────────────────

interface ChapterEditorProps {
  chapter: QCMChapter;
  chapterIndex: number;
  globalOffset: number; // question number offset before this chapter
  onChange: (updated: QCMChapter) => void;
  onDelete: () => void;
  onMoveUp: (() => void) | null;
  onMoveDown: (() => void) | null;
  totalQuestions: number;
}

function ChapterEditor({
  chapter,
  chapterIndex,
  globalOffset,
  onChange,
  onDelete,
  onMoveUp,
  onMoveDown,
  totalQuestions,
}: ChapterEditorProps) {
  const t = useTranslations("QCM.editor");

  const addQuestion = () => {
    if (totalQuestions >= MAX_QUESTIONS_PER_QCM) {
      toast.warning(t("maxQuestions"));
      return;
    }
    onChange({ ...chapter, questions: [...chapter.questions, createEmptyQuestion()] });
  };

  const updateQuestion = (index: number, updated: QCMQuestion) => {
    const questions = [...chapter.questions];
    questions[index] = updated;
    onChange({ ...chapter, questions });
  };

  const deleteQuestion = (index: number) => {
    onChange({
      ...chapter,
      questions: chapter.questions.filter((_, i) => i !== index),
    });
  };

  const moveQuestion = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= chapter.questions.length) return;
    const questions = [...chapter.questions];
    [questions[index], questions[target]] = [questions[target], questions[index]];
    onChange({ ...chapter, questions });
  };

  return (
    <div className="rounded-xl border border-muted bg-muted/20 p-4 space-y-4">
      {/* Chapter header */}
      <div className="flex items-center gap-2">
        <div className="flex-1 space-y-0.5">
          <Input
            value={chapter.title}
            onChange={(e) => onChange({ ...chapter, title: e.target.value.slice(0, MAX_LEN_CHAPTER_TITLE) })}
            placeholder={t("chapterTitle")}
            className="font-semibold text-sm"
            maxLength={MAX_LEN_CHAPTER_TITLE}
          />
          <div className="flex justify-end">
            <CharCount value={chapter.title} max={MAX_LEN_CHAPTER_TITLE} />
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {onMoveUp && (
            <Button type="button" size="icon" variant="ghost" className="h-7 w-7" onClick={onMoveUp}>
              <ChevronUp className="h-4 w-4" />
            </Button>
          )}
          {onMoveDown && (
            <Button type="button" size="icon" variant="ghost" className="h-7 w-7" onClick={onMoveDown}>
              <ChevronDown className="h-4 w-4" />
            </Button>
          )}
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            title={t("deleteChapter")}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Questions */}
      <div className="space-y-3">
        {chapter.questions.map((q, qi) => (
          <QuestionEditor
            key={q.id}
            question={q}
            questionNumber={globalOffset + qi + 1}
            onChange={(updated) => updateQuestion(qi, updated)}
            onDelete={() => deleteQuestion(qi)}
            onMoveUp={qi > 0 ? () => moveQuestion(qi, -1) : null}
            onMoveDown={qi < chapter.questions.length - 1 ? () => moveQuestion(qi, 1) : null}
          />
        ))}
      </div>

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="gap-1 text-xs"
        onClick={addQuestion}
      >
        <Plus className="h-3 w-3" />
        {t("addQuestion")}
      </Button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main editor
// ─────────────────────────────────────────────────────────────────────────────

export function QCMEditor({
  initialData,
  initialMeta,
  onSubmit,
  isSubmitting = false,
  onSaveDraft,
  isSavingDraft = false,
}: QCMEditorProps) {
  const t = useTranslations("QCM.editor");
  const { setHideFooter } = useUIStore();

  useEffect(() => {
    setHideFooter(true);
    return () => setHideFooter(false);
  }, [setHideFooter]);

  const [qcm, setQcm] = useState<QCMFile>(
    initialData ?? { version: 1, chapters: [createEmptyChapter("Chapitre 1")] },
  );

  const [meta, setMeta] = useState<QCMMeta>({
    title: initialMeta?.title ?? "",
    type: "qcm",
    description: initialMeta?.description ?? "",
    tags: initialMeta?.tags ?? [],
  });

  const [previewOpen, setPreviewOpen] = useState(false);

  // Moodle import state
  const [importConfirmData, setImportConfirmData] = useState<QCMFile | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const moodleInputRef = useRef<HTMLInputElement>(null);

  // ── Chapter helpers ──

  const addChapter = () => {
    if (qcm.chapters.length >= MAX_CHAPTERS_PER_QCM) {
      toast.warning(t("maxChapters"));
      return;
    }
    setQcm((prev) => ({
      ...prev,
      chapters: [
        ...prev.chapters,
        createEmptyChapter(`Chapitre ${prev.chapters.length + 1}`),
      ],
    }));
  };

  const updateChapter = useCallback((index: number, updated: QCMChapter) => {
    setQcm((prev) => {
      const chapters = [...prev.chapters];
      chapters[index] = updated;
      return { ...prev, chapters };
    });
  }, []);

  const deleteChapter = (index: number) => {
    setQcm((prev) => ({
      ...prev,
      chapters: prev.chapters.filter((_, i) => i !== index),
    }));
  };

  const moveChapter = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= qcm.chapters.length) return;
    setQcm((prev) => {
      const chapters = [...prev.chapters];
      [chapters[index], chapters[target]] = [chapters[target], chapters[index]];
      return { ...prev, chapters };
    });
  };

  // ── Moodle import ──

  const handleMoodleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsImporting(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await apiFetch<QCMFile>("/qcm/parse-moodle", {
        method: "POST",
        body: formData,
      });
      const hasContent = countQCMQuestions(qcm) > 0;
      if (hasContent) {
        setImportConfirmData(result);
      } else {
        setQcm(result);
        toast.success("Import Moodle réussi");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur lors de l'import");
    } finally {
      setIsImporting(false);
      if (moodleInputRef.current) moodleInputRef.current.value = "";
    }
  };

  const handleImportConfirm = () => {
    if (importConfirmData) {
      setQcm(importConfirmData);
      setImportConfirmData(null);
      toast.success("Import Moodle appliqué");
    }
  };

  // ── Submit ──

  const handleSubmit = async () => {
    if (!meta.title.trim()) {
      toast.error("Veuillez saisir un titre");
      return;
    }
    if (countQCMQuestions(qcm) === 0) {
      toast.error("Le QCM doit contenir au moins une question");
      return;
    }
    await onSubmit(qcm, meta);
  };

  const handleSaveDraft = async () => {
    if (!meta.title.trim()) {
      toast.error("Veuillez saisir un titre");
      return;
    }
    await onSaveDraft!(qcm, meta);
  };

  const totalQuestions = countQCMQuestions(qcm);

  // Compute per-chapter global offsets
  const chapterOffsets: number[] = [];
  let offset = 0;
  for (const ch of qcm.chapters) {
    chapterOffsets.push(offset);
    offset += ch.questions.length;
  }

  return (
    <div className="flex flex-col gap-6 pb-16">
      {/* ── Metadata ── */}
      <div className="rounded-xl border bg-card p-4 space-y-4">
        <div className="space-y-1">
          <Label htmlFor="qcm-title" className="text-sm font-medium">
            {t("title")} *
          </Label>
          <Input
            id="qcm-title"
            value={meta.title}
            onChange={(e) => setMeta((m) => ({ ...m, title: e.target.value.slice(0, MAX_LEN_TITLE) }))}
            placeholder={t("title")}
            maxLength={MAX_LEN_TITLE}
          />
          <div className="flex justify-end">
            <CharCount value={meta.title} max={MAX_LEN_TITLE} />
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="qcm-desc" className="text-sm font-medium">
            Description
          </Label>
          <Textarea
            id="qcm-desc"
            value={meta.description ?? ""}
            onChange={(e) => setMeta((m) => ({ ...m, description: e.target.value.slice(0, MAX_LEN_DESCRIPTION) }))}
            placeholder="Description optionnelle..."
            rows={2}
            className="text-sm"
            maxLength={MAX_LEN_DESCRIPTION}
          />
          <div className="flex justify-end">
            <CharCount value={meta.description ?? ""} max={MAX_LEN_DESCRIPTION} />
          </div>
        </div>

        {/* Moodle import */}
        <div className="flex items-center gap-3 pt-1">
          <input
            ref={moodleInputRef}
            type="file"
            accept=".xml"
            className="hidden"
            onChange={handleMoodleFileChange}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="gap-2 text-xs"
            disabled={isImporting}
            onClick={() => moodleInputRef.current?.click()}
          >
            {isImporting ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Upload className="h-3 w-3" />
            )}
            {t("importMoodle")}
          </Button>
          {totalQuestions > 0 && (
            <Badge variant="outline" className="text-xs">
              {totalQuestions} question{totalQuestions > 1 ? "s" : ""}
            </Badge>
          )}
        </div>
      </div>

      <Separator />

      {/* ── Chapters ── */}
      <div className="space-y-4">
        {qcm.chapters.map((ch, ci) => (
          <ChapterEditor
            key={ch.id}
            chapter={ch}
            chapterIndex={ci}
            globalOffset={chapterOffsets[ci]}
            onChange={(updated) => updateChapter(ci, updated)}
            onDelete={() => deleteChapter(ci)}
            onMoveUp={ci > 0 ? () => moveChapter(ci, -1) : null}
            onMoveDown={ci < qcm.chapters.length - 1 ? () => moveChapter(ci, 1) : null}
            totalQuestions={totalQuestions}
          />
        ))}

        <Button
          type="button"
          variant="outline"
          className="gap-2 w-full border-dashed"
          onClick={addChapter}
        >
          <Plus className="h-4 w-4" />
          {t("addChapter")}
        </Button>
      </div>

      {/* ── Bottom toolbar ── */}
      {/* Desktop: full-width bar. Mobile: compact floating pill above the nav bar. */}
      <div className="fixed bottom-0 max-sm:bottom-[72px] left-0 right-0 z-[60] max-sm:flex max-sm:justify-center max-sm:px-4 max-sm:pointer-events-none sm:flex sm:items-center sm:justify-end sm:gap-3 sm:border-t sm:bg-background/95 sm:backdrop-blur sm:px-6 sm:py-3 sm:pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))]">
        <div className="max-sm:pointer-events-auto max-sm:flex max-sm:items-center max-sm:gap-2 max-sm:rounded-2xl max-sm:bg-background/90 max-sm:backdrop-blur-xl max-sm:shadow-lg max-sm:shadow-black/10 max-sm:border max-sm:border-border/30 max-sm:px-3 max-sm:py-2 sm:contents">
          <Button
            type="button"
            variant="ghost"
            disabled={isSubmitting || isSavingDraft}
            onClick={() => window.history.back()}
            className="max-sm:hidden"
          >
            Annuler
          </Button>
          <Button
            type="button"
            variant="outline"
            className="gap-2"
            disabled={totalQuestions === 0}
            onClick={() => setPreviewOpen(true)}
          >
            <MonitorPlay className="h-4 w-4" />
            <span className="max-sm:hidden">{t("preview")}</span>
          </Button>
          {onSaveDraft && (
            <Button
              type="button"
              variant="outline"
              onClick={handleSaveDraft}
              disabled={isSavingDraft || isSubmitting || !meta.title.trim()}
              className="gap-2"
            >
              {isSavingDraft && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("saveToDraft")}
            </Button>
          )}
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitting || isSavingDraft || !meta.title.trim()}
            className="gap-2"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSubmitting ? t("submitting") : t("submit")}
          </Button>
        </div>
      </div>

      {/* ── Live preview dialog ── */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-2xl h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="shrink-0 px-6 pt-5 pb-3 border-b">
            <DialogTitle>{meta.title || t("preview")}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-hidden">
            {previewOpen && <QCMViewer initialData={qcm} />}
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Moodle import confirmation dialog ── */}
      <Dialog
        open={importConfirmData !== null}
        onOpenChange={(open) => !open && setImportConfirmData(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirmer l&apos;import</DialogTitle>
            <DialogDescription>{t("importMoodleConfirm")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setImportConfirmData(null)}>
              Annuler
            </Button>
            <Button onClick={handleImportConfirm}>Importer</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
