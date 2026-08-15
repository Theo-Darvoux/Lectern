"use client";
// NOTE: uses apiRequest (not raw fetch) so Bearer tokens are injected automatically.

import dynamic from "next/dynamic";
import { useEffect, useRef, useState, useCallback } from "react";
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
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
    FilePenLine,
    FolderPen,
    Plus,
    Send,
    Loader2,
    UploadCloud,
    X,
    CheckCircle2,
    AlertCircle,
    FileCode2,
    RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import { useStagingStore, type Operation } from "@/lib/staging-store";
import { submitDirectOperations } from "@/lib/pr-client";
import { TagInput } from "@/components/ui/tag-input";
import { apiRequest } from "@/lib/api-client";
import { uploadFile, logicalFileSize } from "@/lib/upload-client";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import type { Monaco } from "@monaco-editor/react";
import { useTranslations } from "next-intl";

// Monaco is lazy-loaded to keep initial bundle tight
const MonacoEditor = dynamic(
    () => import("@monaco-editor/react").then((m) => m.default),
    { ssr: false, loading: () => <EditorSkeleton /> },
);

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function EditorSkeleton() {
    return (
        <div className="flex h-full items-center justify-center rounded-md border bg-muted/20">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
    );
}

/** MIME types / filename patterns that can be edited as plain text */
const TEXT_MIME_PREFIXES = ["text/"];
const TEXT_MIME_EXACT = new Set([
    "application/json",
    "application/xml",
    "application/javascript",
    "application/typescript",
    "application/x-yaml",
    "application/x-sh",
    "application/sql",
]);

function isTextMime(mime: string): boolean {
    const m = (mime || "").toLowerCase();
    if (TEXT_MIME_PREFIXES.some((p) => m.startsWith(p))) return true;
    return TEXT_MIME_EXACT.has(m);
}

function isTextEditable(mimeType: string, fileName: string): boolean {
    const m = (mimeType || "").toLowerCase();
    const name = (fileName || "").toLowerCase();
    // Previously gzip-compressed text is still editable
    if (m === "application/gzip" || name.endsWith(".gz")) return true;
    return isTextMime(m);
}

/** Register a Monarch tokenizer for LaTeX (not built into Monaco). */
function registerLatex(monaco: Monaco) {
    monaco.languages.register({ id: "latex", aliases: ["LaTeX", "TeX", "tex"] });
    monaco.languages.setMonarchTokensProvider("latex", {
        tokenizer: {
            root: [
                [/%.*$/, "comment"],
                [/\$\$/, { token: "string", next: "@mathDouble" }],
                [/\$/, { token: "string", next: "@mathSingle" }],
                [/\\[a-zA-Z@*]+/, "keyword"],
                [/[{}]/, "delimiter.curly"],
                [/\[|\]/, "delimiter.square"],
            ],
            mathDouble: [
                [/\$\$/, { token: "string", next: "@pop" }],
                [/\\[a-zA-Z@*]+/, "string.escape"],
                [/./, "string"],
            ],
            mathSingle: [
                [/\$/, { token: "string", next: "@pop" }],
                [/\\[a-zA-Z@*]+/, "string.escape"],
                [/./, "string"],
            ],
        },
    });
}

/** Map file extension → Monaco language id */
function monacoLanguage(fileName: string, mimeType: string): string {
    const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
    const m = (mimeType || "").toLowerCase();

    const byExt: Record<string, string> = {
        ts: "typescript",
        tsx: "typescript",
        js: "javascript",
        jsx: "javascript",
        py: "python",
        java: "java",
        c: "c",
        cpp: "cpp",
        h: "c",
        hpp: "cpp",
        rs: "rust",
        go: "go",
        rb: "ruby",
        php: "php",
        cs: "csharp",
        swift: "swift",
        kt: "kotlin",
        scala: "scala",
        html: "html",
        css: "css",
        scss: "scss",
        json: "json",
        yaml: "yaml",
        yml: "yaml",
        toml: "ini",
        xml: "xml",
        sql: "sql",
        sh: "shell",
        bash: "shell",
        zsh: "shell",
        md: "markdown",
        markdown: "markdown",
        csv: "plaintext",
        txt: "plaintext",
        log: "plaintext",
        ini: "ini",
        cfg: "ini",
        conf: "ini",
        tex: "latex",
        latex: "latex",
        lua: "lua",
        r: "r",
        ml: "plaintext",
        hs: "plaintext",
        ex: "elixir",
        exs: "elixir",
        clj: "clojure",
        gz: "plaintext",
    };

    if (byExt[ext]) return byExt[ext];
    if (m.includes("json")) return "json";
    if (m.includes("xml")) return "xml";
    if (m.includes("yaml")) return "yaml";
    if (m.includes("markdown")) return "markdown";
    if (m.includes("javascript")) return "javascript";
    if (m.includes("python")) return "python";
    return "plaintext";
}

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------

interface SidebarTarget {
    type: "material" | "directory";
    id: string;
    data: Record<string, unknown>;
}

interface FileEditDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    target: SidebarTarget;
}

// --------------------------------------------------------------------------
// Binary replacement sub-component
// --------------------------------------------------------------------------

interface BinaryReplaceTabProps {
    materialId: string;
    originalMime: string;
    onFileReady: (result: {
        fileKey: string;
        fileName: string;
        fileSize: number;
        fileMimeType: string;
    }) => void;
    onClear: () => void;
    t: any;
    tCommon: any;
    tUpload: any;
}

function BinaryReplaceTab({ onFileReady, onClear, t, tCommon, tUpload }: BinaryReplaceTabProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [uploadState, setUploadState] = useState<
        "idle" | "uploading" | "done" | "error"
    >("idle");
    const [progress, setProgress] = useState(0);
    const [fileName, setFileName] = useState("");
    const [errorMsg, setErrorMsg] = useState("");
    const abortRef = useRef<AbortController | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFile = useCallback(
        async (file: File) => {
            setUploadState("uploading");
            setProgress(0);
            setFileName(file.name);
            setErrorMsg("");
            const ctrl = new AbortController();
            abortRef.current = ctrl;
            try {
                const result = await uploadFile(file, {
                    signal: ctrl.signal,
                    onProgress: setProgress,
                    t: tUpload,
                });
                setUploadState("done");
                onFileReady({
                    fileKey: result.file_key,
                    fileName: result.correctedName ?? file.name,
                    fileSize: logicalFileSize(result),
                    fileMimeType: result.mime_type,
                });
            } catch (err) {
                if ((err as Error)?.message === "Upload cancelled") return;
                setUploadState("error");
                setErrorMsg((err as Error)?.message ?? "Upload failed");
            } finally {
                abortRef.current = null;
            }
        },
        [onFileReady],
    );

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
    };

    const reset = () => {
        abortRef.current?.abort();
        setUploadState("idle");
        setProgress(0);
        setFileName("");
        setErrorMsg("");
        onClear();
    };

    return (
        <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                        {t("replaceDesc")}
                    </p>

                    {uploadState === "idle" && (
                        <div
                            role="button"
                            tabIndex={0}
                            onDragOver={(e) => {
                                e.preventDefault();
                                setIsDragging(true);
                            }}
                            onDragLeave={() => setIsDragging(false)}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current?.click()}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ")
                                    fileInputRef.current?.click();
                            }}
                            className={cn(
                                "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 transition-colors",
                                isDragging
                                    ? "border-primary bg-primary/5"
                                    : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30",
                            )}
                        >
                            <UploadCloud
                                className={cn(
                                    "h-8 w-8",
                                    isDragging
                                        ? "text-primary"
                                        : "text-muted-foreground",
                                )}
                            />
                            <p className="text-sm text-muted-foreground">
                                {isDragging
                                    ? t("dropActive")
                                    : t("dropDefault")}
                            </p>
                    <input
                        ref={fileInputRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) handleFile(f);
                            e.target.value = "";
                        }}
                    />
                </div>
            )}

            {uploadState === "uploading" && (
                <div className="space-y-2 rounded-lg border bg-muted/20 px-4 py-3">
                    <div className="flex items-center gap-2 text-sm">
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate font-medium">
                            {fileName}
                        </span>
                        <span className="text-muted-foreground">
                            {progress}%
                        </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${progress}%` }}
                        />
                    </div>
                </div>
            )}

            {uploadState === "done" && (
                <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50/50 px-4 py-3 text-sm dark:border-green-800/40 dark:bg-green-950/20">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
                    <span className="min-w-0 flex-1 truncate font-medium">
                        {fileName}
                    </span>
                    <button
                        onClick={reset}
                        className="ml-auto rounded p-0.5 text-muted-foreground hover:text-foreground"
                        title={tCommon("remove")}
                    >
                        <X className="h-3.5 w-3.5" />
                    </button>
                </div>
            )}

            {uploadState === "error" && (
                <div className="space-y-2">
                    <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{errorMsg}</span>
                    </div>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={reset}
                        className="gap-1.5"
                    >
                        <RefreshCw className="h-3.5 w-3.5" />
                        {tCommon("tryAgain")}
                    </Button>
                </div>
            )}
        </div>
    );
}

// --------------------------------------------------------------------------
// Main dialog
// --------------------------------------------------------------------------

export function FileEditDialog({
    open,
    onOpenChange,
    target,
}: FileEditDialogProps) {
    const t = useTranslations("FileEdit");
    const tCommon = useTranslations("Common");
    const tWizard = useTranslations("PRWizard");
    const tAuto = useTranslations("AutoTitle");
    const tUpload = useTranslations("Upload");

    const addOperation = useStagingStore((s) => s.addOperation);
    const { resolvedTheme } = useTheme();
    const isDark = resolvedTheme === "dark";

    const isMaterial = target.type === "material";

    // ── Current metadata ──────────────────────────────────────────────────
    const currentTitle = String(
        isMaterial ? target.data.title ?? "" : target.data.name ?? "",
    );
    const currentDescription = String(target.data.description ?? "");
    const rawTags = (target.data.tags ?? []) as unknown[];
    const currentTags = Array.isArray(rawTags)
        ? rawTags.map(String).filter(Boolean)
        : [];

    const versionInfo = target.data.current_version_info as Record<
        string,
        unknown
    > | null;
    const currentMime = String(versionInfo?.file_mime_type ?? "");
    const currentFileName = String(versionInfo?.file_name ?? "");
    const currentVersionLock = versionInfo?.version_lock as number | undefined;
    const canEditText = isMaterial && isTextEditable(currentMime, currentFileName);
    const logicalFileName = currentFileName.endsWith(".gz")
        ? currentFileName.slice(0, -3)
        : currentFileName;

    // ── Tab state ─────────────────────────────────────────────────────────
    const [activeTab, setActiveTab] = useState<"metadata" | "content">(
        isMaterial ? "content" : "metadata",
    );

    // ── Metadata form state ───────────────────────────────────────────────
    const [title, setTitle] = useState(currentTitle);
    const [description, setDescription] = useState(currentDescription);
    const [tags, setTags] = useState<string[]>(currentTags);

    // ── Text editor state ─────────────────────────────────────────────────
    const [editorText, setEditorText] = useState<string>("");
    const [loadingText, setLoadingText] = useState(false);
    const [loadError, setLoadError] = useState("");
    const [diffSummary, setDiffSummary] = useState("");
    const [savingText, setSavingText] = useState(false);
    const originalTextRef = useRef<string>("");

    // ── Binary replacement state ──────────────────────────────────────────
    const [replacementFile, setReplacementFile] = useState<{
        fileKey: string;
        fileName: string;
        fileSize: number;
        fileMimeType: string;
    } | null>(null);

    // ── Submission state ──────────────────────────────────────────────────
    const [submitting, setSubmitting] = useState(false);

    // ── Reset on open ─────────────────────────────────────────────────────
    const handleOpenChange = (next: boolean) => {
        if (next) {
            setTitle(currentTitle);
            setDescription(currentDescription);
            setTags(currentTags);
            setActiveTab(isMaterial ? "content" : "metadata");
            setEditorText("");
            setLoadError("");
            setDiffSummary("");
            setReplacementFile(null);
            originalTextRef.current = "";
        }
        onOpenChange(next);
    };

    // ── Load text when content tab is selected ────────────────────────────
    useEffect(() => {
        if (!open || activeTab !== "content" || !canEditText) return;
        if (originalTextRef.current !== "") return; // already loaded

        setLoadingText(true);
        setLoadError("");
        apiRequest(`/materials/${target.id}/text-content`)
            .then((res) => res.text())
            .then((text) => {
                originalTextRef.current = text;
                setEditorText(text);
            })
            .catch((e: Error) => setLoadError(e.message))
            .finally(() => setLoadingText(false));
    }, [open, activeTab, canEditText, target.id]);

    // ── Metadata dirty check ──────────────────────────────────────────────
    const hasTagsChanged = () => {
        if (tags.length !== currentTags.length) return true;
        return tags.some((t, i) => t !== currentTags[i]);
    };
    const metadataChanged =
        title !== currentTitle ||
        description !== currentDescription ||
        hasTagsChanged();

    // ── Content dirty check ───────────────────────────────────────────────
    const textChanged =
        canEditText &&
        originalTextRef.current !== "" &&
        editorText !== originalTextRef.current;
    const binaryChanged = !canEditText && replacementFile !== null;
    const contentChanged = textChanged || binaryChanged;

    const hasChanges = metadataChanged || contentChanged;
    const canSubmit = hasChanges && title.trim().length > 0 && !submitting && !savingText;
    const isDraftTarget = target.id.startsWith("$");

    // ── Build the operation ───────────────────────────────────────────────
    const buildMetadataOp = (): Operation | null => {
        if (!metadataChanged) return null;
        if (isMaterial) {
            return {
                op: "edit_material",
                material_id: target.id,
                ...(title !== currentTitle ? { title: title.trim() } : {}),
                ...(description !== currentDescription
                    ? { description: description.trim() || null }
                    : {}),
                ...(hasTagsChanged() ? { tags } : {}),
                version_lock: currentVersionLock,
            };
        } else {
            return {
                op: "edit_directory",
                directory_id: target.id,
                ...(title !== currentTitle ? { name: title.trim() } : {}),
                ...(description !== currentDescription
                    ? { description: description.trim() || null }
                    : {}),
                ...(hasTagsChanged() ? { tags } : {}),
            };
        }
    };

    const buildContentOp = (fileKey: string, fileName: string, fileSize: number, fileMimeType: string, diffText?: string): Operation => {
        let combinedDiff = diffSummary.trim();
        if (diffText) {
            combinedDiff = combinedDiff ? `${combinedDiff}\n\n${diffText}` : diffText;
        }
        return {
            op: "edit_material",
            material_id: target.id,
            file_key: fileKey,
            file_name: fileName,
            file_size: fileSize,
            file_mime_type: fileMimeType,
            diff_summary: combinedDiff || undefined,
            version_lock: currentVersionLock,
        };
    };

    // ── Save text to backend, get file_key, stage op ──────────────────────
    const handleSaveText = async (mode: "draft" | "direct") => {
        setSavingText(true);
        try {
            const res = await apiRequest(
                `/materials/${target.id}/text-content`,
                {
                    method: "POST",
                    headers: { "Content-Type": "text/plain; charset=utf-8" },
                    body: editorText,
                },
            );
            if (!res.ok) {
                const txt = await res.text();
                throw new Error(txt || res.statusText);
            }
            const data = (await res.json()) as {
                file_key: string;
                file_name: string;
                file_size: number;
                file_mime_type: string;
                diff?: string;
            };

            const contentOp = buildContentOp(
                data.file_key,
                data.file_name,
                data.file_size,
                data.file_mime_type,
                data.diff,
            );
            const metaOp = buildMetadataOp();

            const ops = metaOp ? [metaOp, contentOp] : [contentOp];

            if (mode === "draft") {
                ops.forEach((op) => addOperation(op));
                toast.success(t("saveSuccess"));
                onOpenChange(false);
            } else {
                setSubmitting(true);
                const result = await submitDirectOperations(ops, undefined, undefined, tAuto);
                setSubmitting(false);
                if (!result) return;
                onOpenChange(false);
            }
        } catch (e) {
            toast.error(
                (e as Error)?.message ?? t("saveError"),
            );
        } finally {
            setSavingText(false);
            setSubmitting(false);
        }
    };

    // ── Stage / submit button handlers ────────────────────────────────────
    const handleDraft = async () => {
        if (!canSubmit) return;

        if (contentChanged && canEditText && textChanged) {
            await handleSaveText("draft");
            return;
        }

        const ops: Operation[] = [];
        const metaOp = buildMetadataOp();
        if (metaOp) ops.push(metaOp);

        if (binaryChanged && replacementFile) {
            ops.push(
                buildContentOp(
                    replacementFile.fileKey,
                    replacementFile.fileName,
                    replacementFile.fileSize,
                    replacementFile.fileMimeType,
                ),
            );
        }

        ops.forEach((op) => addOperation(op));
        toast.success(t("saveSuccess"));
        onOpenChange(false);
    };

    const handleDirectSubmit = async () => {
        if (!canSubmit) return;

        if (contentChanged && canEditText && textChanged) {
            await handleSaveText("direct");
            return;
        }

        setSubmitting(true);
        const ops: Operation[] = [];
        const metaOp = buildMetadataOp();
        if (metaOp) ops.push(metaOp);
        if (binaryChanged && replacementFile) {
            ops.push(
                buildContentOp(
                    replacementFile.fileKey,
                    replacementFile.fileName,
                    replacementFile.fileSize,
                    replacementFile.fileMimeType,
                ),
            );
        }
        const result = await submitDirectOperations(ops, undefined, undefined, tAuto);
        setSubmitting(false);
        if (!result) return;
        onOpenChange(false);
    };

    const Icon = isMaterial ? FilePenLine : FolderPen;
    const isLoading = submitting || savingText;
    const monacoLang = monacoLanguage(logicalFileName, currentMime);

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent
                className={cn(
                    "flex flex-col overflow-hidden transition-all duration-200",
                    activeTab === "content" && canEditText
                        ? "sm:max-w-4xl h-[90vh]"
                        : "sm:max-w-md",
                )}
            >
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Icon className="h-5 w-5 text-blue-600" />
                        {t("title", { type: isMaterial ? t("material") : t("folder") })}
                    </DialogTitle>
                    <DialogDescription>
                        {t.rich("description", {
                            name: currentTitle,
                            foreground: (chunks) => <span className="font-medium text-foreground">{chunks}</span>
                        })}
                    </DialogDescription>
                </DialogHeader>

                <Tabs
                    value={activeTab}
                    onValueChange={(v) =>
                        setActiveTab(v as "metadata" | "content")
                    }
                    className="flex min-h-0 flex-1 flex-col"
                >
                    <TabsList className="shrink-0 self-start">
                        {isMaterial && (
                            <TabsTrigger value="content" className="gap-1.5">
                                <FileCode2 className="h-3.5 w-3.5" />
                                {canEditText ? t("editText") : t("replaceFile")}
                            </TabsTrigger>
                        )}
                        <TabsTrigger value="metadata">{t("metadata")}</TabsTrigger>
                    </TabsList>

                    {/* ── Metadata tab ──────────────────────────────────── */}
                    <TabsContent value="metadata" className="mt-4 space-y-4">
                        <div className="space-y-1.5">
                            <label
                                htmlFor="edit-title"
                                className="text-sm font-medium"
                            >
                                {isMaterial ? tWizard("nameTitle") : tWizard("createDirectory")}
                            </label>
                            <Input
                                id="edit-title"
                                value={title}
                                onChange={(e) => setTitle(e.target.value)}
                                maxLength={100}
                                disabled={isLoading}
                                autoFocus
                            />
                        </div>
                        <div className="space-y-1.5">
                            <label
                                htmlFor="edit-desc"
                                className="text-sm font-medium"
                            >
                                {tWizard("description")}{" "}
                                <span className="text-muted-foreground">
                                    {tCommon("optional")}
                                </span>
                            </label>
                            <Textarea
                                id="edit-desc"
                                value={description}
                                onChange={(e) =>
                                    setDescription(e.target.value)
                                }
                                maxLength={1000}
                                disabled={isLoading}
                                rows={3}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <label
                                htmlFor="edit-tags"
                                className="text-sm font-medium"
                            >
                                {tWizard("tags")}
                            </label>
                            <TagInput
                                key={target.id}
                                tags={tags}
                                onChange={setTags}
                                placeholder={tWizard("tagsPlaceholder")}
                            />
                        </div>
                    </TabsContent>

                    {/* ── Content tab ───────────────────────────────────── */}
                    {isMaterial && (
                        <TabsContent
                            value="content"
                            className="mt-4 flex min-h-0 flex-1 flex-col gap-3"
                        >
                            {canEditText ? (
                                <>
                                    {loadingText ? (
                                        <div className="flex flex-1 items-center justify-center">
                                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                                        </div>
                                    ) : loadError ? (
                                        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
                                            <AlertCircle className="h-8 w-8 text-destructive/70" />
                                            <p className="text-sm text-destructive">
                                                {loadError}
                                            </p>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => {
                                                    originalTextRef.current = "";
                                                    setLoadError("");
                                                    setActiveTab("metadata");
                                                    setTimeout(
                                                        () =>
                                                            setActiveTab(
                                                                "content",
                                                            ),
                                                        50,
                                                    );
                                                }}
                                            >
                                                {tCommon("tryAgain")}
                                            </Button>
                                        </div>
                                    ) : (
                                        <div className="min-h-0 flex-1 overflow-hidden rounded-md border">
                                            <MonacoEditor
                                                height="100%"
                                                language={monacoLang}
                                                value={editorText}
                                                beforeMount={registerLatex}
                                                onChange={(v) =>
                                                    setEditorText(v ?? "")
                                                }
                                                theme={
                                                    isDark
                                                        ? "vs-dark"
                                                        : "vs"
                                                }
                                                options={{
                                                    fontSize: 13,
                                                    minimap: {
                                                        enabled: false,
                                                    },
                                                    wordWrap: "on",
                                                    lineNumbers: "on",
                                                    scrollBeyondLastLine: false,
                                                    renderWhitespace: "none",
                                                    tabSize: 4,
                                                    automaticLayout: true,
                                                    padding: { top: 8, bottom: 8 },
                                                }}
                                            />
                                        </div>
                                    )}

                                    <div className="shrink-0 space-y-1.5">
                                        <label
                                            htmlFor="edit-diff-summary"
                                            className="text-sm font-medium"
                                        >
                                            {t("changeDesc")}{" "}
                                            <span className="text-muted-foreground">
                                                {tCommon("optional")}
                                            </span>
                                        </label>
                                        <Input
                                            id="edit-diff-summary"
                                            value={diffSummary}
                                            onChange={(e) =>
                                                setDiffSummary(e.target.value)
                                            }
                                            maxLength={200}
                                            disabled={isLoading}
                                            placeholder={t("changeDescPlaceholderText")}
                                        />
                                    </div>
                                </>
                            ) : (
                                <>
                                    <BinaryReplaceTab
                                        materialId={target.id}
                                        originalMime={currentMime}
                                        onFileReady={setReplacementFile}
                                        onClear={() =>
                                            setReplacementFile(null)
                                        }
                                        t={t}
                                        tCommon={tCommon}
                                        tUpload={tUpload}
                                    />
                                    <div className="space-y-1.5">
                                        <label
                                            htmlFor="edit-replace-desc"
                                            className="text-sm font-medium"
                                        >
                                            {t("changeDesc")}{" "}
                                            <span className="text-muted-foreground">
                                                {tCommon("optional")}
                                            </span>
                                        </label>
                                        <Input
                                            id="edit-replace-desc"
                                            value={diffSummary}
                                            onChange={(e) =>
                                                setDiffSummary(e.target.value)
                                            }
                                            maxLength={200}
                                            disabled={isLoading}
                                            placeholder={t("changeDescPlaceholderBinary")}
                                        />
                                    </div>
                                </>
                            )}
                        </TabsContent>
                    )}
                </Tabs>

                <DialogFooter className="mt-2 shrink-0 flex-wrap gap-2">
                    <Button
                        variant="ghost"
                        onClick={() => onOpenChange(false)}
                        disabled={isLoading}
                        className="sm:mr-auto"
                    >
                        {tCommon("cancel")}
                    </Button>
                    <Button
                        variant="outline"
                        onClick={handleDraft}
                        disabled={!canSubmit}
                        className="gap-2 border-dashed border-primary/50 text-primary hover:bg-primary/5"
                    >
                        {savingText ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Plus className="h-4 w-4" />
                        )}
                        {t("addToDraft")}
                    </Button>
                    {!isDraftTarget && (
                        <Button
                            onClick={handleDirectSubmit}
                            disabled={!canSubmit}
                            className="gap-2"
                        >
                            {isLoading ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                            {t("submitDirectly")}
                        </Button>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
