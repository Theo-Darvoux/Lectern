"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import hljs from "highlight.js/lib/common";
import { MarkdownRenderer } from "./markdown-renderer";

type NotebookCell = {
    cell_type?: unknown;
    source?: unknown;
    execution_count?: unknown;
    outputs?: unknown;
    attachments?: unknown;
};

type Notebook = {
    cells: NotebookCell[];
    language: string;
};

const ANSI_SEQUENCE = new RegExp("(?:\\u001B\\[|\\u009B)[0-?]*[ -/]*[@-~]", "g");

type NotebookOutput = {
    output_type?: unknown;
    text?: unknown;
    data?: unknown;
    ename?: unknown;
    evalue?: unknown;
    traceback?: unknown;
};

function joinText(value: unknown): string {
    if (typeof value === "string") return value;
    if (Array.isArray(value) && value.every((part) => typeof part === "string")) {
        return value.join("");
    }
    return "";
}

function joinLines(value: unknown): string {
    if (Array.isArray(value) && value.every((part) => typeof part === "string")) {
        return value.join("\n");
    }
    return typeof value === "string" ? value : "";
}

function stripAnsi(value: string): string {
    return value.replace(ANSI_SEQUENCE, "");
}

function parseNotebook(content: string): Notebook | null {
    try {
        const parsed: unknown = JSON.parse(content);
        if (!parsed || typeof parsed !== "object") return null;
        const candidate = parsed as {
            cells?: unknown;
            nbformat?: unknown;
            metadata?: {
                language_info?: { name?: string };
                kernelspec?: { language?: string };
            };
        };
        const cells = candidate.cells;
        if (!Number.isInteger(candidate.nbformat) || !Array.isArray(cells)) return null;
        const language =
            candidate.metadata?.language_info?.name ||
            candidate.metadata?.kernelspec?.language ||
            "python";
        return {
            cells: cells.filter((cell): cell is NotebookCell => !!cell && typeof cell === "object"),
            language,
        };
    } catch {
        return null;
    }
}

function getOutputData(output: NotebookOutput): Record<string, unknown> {
    return output.data && typeof output.data === "object" && !Array.isArray(output.data)
        ? output.data as Record<string, unknown>
        : {};
}

function imageDataUrl(data: Record<string, unknown>): string | null {
    for (const [mime, value] of [
        ["image/png", data["image/png"]],
        ["image/jpeg", data["image/jpeg"]],
        ["image/gif", data["image/gif"]],
    ] as const) {
        const encoded = joinText(value).replace(/\s/g, "");
        if (encoded && /^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
            return `data:${mime};base64,${encoded}`;
        }
    }
    return null;
}

function attachmentUrls(value: unknown): Record<string, string> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};

    const urls: Record<string, string> = {};
    for (const [name, bundle] of Object.entries(value)) {
        if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) continue;
        const url = imageDataUrl(bundle as Record<string, unknown>);
        if (!url) continue;
        urls[`attachment:${name}`] = url;
        urls[`attachment:${encodeURIComponent(name)}`] = url;
    }
    return urls;
}

function NotebookOutputView({ output }: { output: NotebookOutput }) {
    const t = useTranslations("Viewers.notebook");

    if (output.output_type === "stream") {
        const text = stripAnsi(joinText(output.text));
        return text ? <pre className="whitespace-pre-wrap break-words font-mono text-sm">{text}</pre> : null;
    }

    if (output.output_type === "error") {
        const traceback = stripAnsi(joinLines(output.traceback));
        const name = typeof output.ename === "string" ? output.ename : "";
        const value = typeof output.evalue === "string" ? output.evalue : "";
        const text = traceback || [name, value].filter(Boolean).join(": ");
        return text ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-sm text-destructive">{text}</pre>
        ) : null;
    }

    if (output.output_type === "execute_result" || output.output_type === "display_data") {
        const data = getOutputData(output);
        const imageUrl = imageDataUrl(data);
        if (imageUrl) {
            return (
                // Jupyter image outputs are restricted to inert raster data URLs.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={imageUrl} alt={t("imageOutput")} className="max-w-full rounded" />
            );
        }

        const markdown = joinText(data["text/markdown"]);
        if (markdown) {
            return (
                <div className="prose prose-sm max-w-none dark:prose-invert">
                    <MarkdownRenderer content={markdown} />
                </div>
            );
        }

        const plainText = joinText(data["text/plain"]);
        return plainText ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-sm">{plainText}</pre>
        ) : null;
    }

    return null;
}

function NotebookCodeCell({
    source,
    executionCount,
    outputs,
    language,
}: {
    source: string;
    executionCount: string;
    outputs?: unknown;
    language: string;
}) {
    const highlighted = useMemo(() => {
        if (!source.trim()) return "";
        try {
            if (language && hljs.getLanguage(language)) {
                return hljs.highlight(source, { language, ignoreIllegals: true }).value;
            }
            return hljs.highlightAuto(source).value;
        } catch {
            return "";
        }
    }, [source, language]);

    return (
        <section className="overflow-hidden rounded-lg border bg-background shadow-sm">
            <div className="flex min-w-0">
                <span className="w-14 shrink-0 select-none border-r bg-muted/40 px-2 py-4 text-right font-mono text-xs text-muted-foreground">
                    [{executionCount}]
                </span>
                <pre className="min-w-0 flex-1 overflow-x-auto p-4 text-sm leading-relaxed">
                    {highlighted ? (
                        <code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted }} />
                    ) : (
                        <code>{source}</code>
                    )}
                </pre>
            </div>
            {Array.isArray(outputs) && outputs.length > 0 && (
                <div className="space-y-3 border-t bg-muted/10 py-4 pl-[4.5rem] pr-4">
                    {outputs.map((output, outputIndex) =>
                        output && typeof output === "object" ? (
                            <NotebookOutputView key={outputIndex} output={output as NotebookOutput} />
                        ) : null,
                    )}
                </div>
            )}
        </section>
    );
}

export function NotebookRenderer({ content }: { content: string }) {
    const t = useTranslations("Viewers.notebook");
    const notebook = useMemo(() => parseNotebook(content), [content]);

    if (!notebook) {
        return (
            <div className="flex min-h-full items-center justify-center p-8 text-sm text-destructive">
                {t("invalid")}
            </div>
        );
    }

    if (notebook.cells.length === 0) {
        return (
            <div className="flex min-h-full items-center justify-center p-8 text-sm text-muted-foreground">
                {t("empty")}
            </div>
        );
    }

    return (
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4 sm:p-6">
            {notebook.cells.map((cell, index) => {
                const source = joinText(cell.source);

                if (cell.cell_type === "markdown") {
                    const images = attachmentUrls(cell.attachments);
                    return (
                        <article
                            key={index}
                            className="prose prose-sm max-w-none rounded-lg border bg-background px-5 py-4 shadow-sm dark:prose-invert"
                        >
                            <MarkdownRenderer
                                content={source}
                                resolveImageUrl={(url) => images[url] ?? null}
                            />
                        </article>
                    );
                }

                if (cell.cell_type === "code") {
                    const executionCount =
                        typeof cell.execution_count === "number" || typeof cell.execution_count === "string"
                            ? String(cell.execution_count)
                            : " ";
                    return (
                        <NotebookCodeCell
                            key={index}
                            source={source}
                            executionCount={executionCount}
                            outputs={cell.outputs}
                            language={notebook.language}
                        />
                    );
                }

                return null;
            })}
        </div>
    );
}
