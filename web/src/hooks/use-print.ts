"use client";

import { useState, useCallback } from "react";
import { fetchMaterialBlob, fetchMaterialFile } from "@/lib/api-client";
import { isPrintable, printInIframe } from "@/lib/print-utils";
import { getViewerPrint } from "@/lib/viewer-print-registry";
import { toast } from "sonner";
import type { QCMFile } from "@/lib/qcm-types";

/** Print CSS for the rendered markdown iframe — prose typography + hljs github theme. */
const MARKDOWN_PRINT_CSS = `
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #1a1a1a; max-width: 860px; margin: 0 auto; padding: 24px; }
  h1, h2, h3, h4, h5, h6 { font-weight: 600; line-height: 1.25; margin: 1.5em 0 0.5em; }
  h1 { font-size: 2em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3em; }
  h2 { font-size: 1.5em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3em; }
  h3 { font-size: 1.25em; }
  h4 { font-size: 1em; }
  p { margin: 0.75em 0; }
  a { color: #0969da; text-decoration: none; }
  ul, ol { padding-left: 2em; margin: 0.75em 0; }
  li { margin: 0.25em 0; }
  blockquote { margin: 1em 0; padding: 0 1em; color: #57606a; border-left: 4px solid #d0d7de; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 1.5em 0; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; }
  th, td { border: 1px solid #d0d7de; padding: 6px 13px; text-align: left; }
  th { background: #f6f8fa; font-weight: 600; }
  tr:nth-child(even) { background: #f6f8fa; }
  img { max-width: 100%; height: auto; }
  mark { background: #fff3b0; color: inherit; padding: 0 2px; border-radius: 2px; }
  code { font-family: "SF Mono", "Fira Code", Consolas, monospace; font-size: 0.875em; background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 3px; padding: 0.1em 0.3em; }
  pre { background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px 16px; overflow-x: auto; margin: 1em 0; }
  pre code { background: none; border: none; padding: 0; font-size: 0.8125em; }
  /* hljs github theme */
  .hljs { color: #24292e; }
  .hljs-comment, .hljs-quote { color: #6a737d; font-style: italic; }
  .hljs-keyword, .hljs-selector-tag, .hljs-subst { color: #d73a49; }
  .hljs-string, .hljs-doctag, .hljs-attr, .hljs-addition { color: #032f62; }
  .hljs-number, .hljs-literal, .hljs-variable, .hljs-template-variable, .hljs-link { color: #005cc5; }
  .hljs-title, .hljs-section, .hljs-selector-id { color: #6f42c1; font-weight: bold; }
  .hljs-type, .hljs-class .hljs-title { color: #6f42c1; }
  .hljs-built_in, .hljs-builtin-name { color: #005cc5; }
  .hljs-meta, .hljs-symbol { color: #e36209; }
  .hljs-deletion { color: #b31d28; background: #ffeef0; }
  .hljs-emphasis { font-style: italic; }
  .hljs-strong { font-weight: bold; }
  /* Callouts */
  .callout { border-left: 4px solid; border-radius: 4px; padding: 10px 14px; margin: 1em 0; background: #f9fafb; }
`;

const ANSWER_LETTERS = ["A", "B", "C", "D"];

function escHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildQcmPrintHtml(qcm: QCMFile, title: string): string {
  let questionsHtml = "";
  let answerKeyHtml = "";
  let globalQ = 0;

  for (const chapter of qcm.chapters) {
    if (chapter.title) {
      questionsHtml += `<h2 class="chapter-title">${escHtml(chapter.title)}</h2>`;
    }
    for (const question of chapter.questions) {
      globalQ++;
      const correctLetters = question.answers
        .map((a, i) => (a.correct ? ANSWER_LETTERS[i] : null))
        .filter(Boolean)
        .join(", ");

      const explanationHtml = question.explanation
        ? `<p class="explanation"><em>Explication&nbsp;:</em> ${escHtml(question.explanation)}</p>`
        : "";

      questionsHtml += `
        <div class="question">
          <p class="question-text"><span class="q-num">Q${globalQ}.</span>&nbsp;${escHtml(question.text)}</p>
          <ul class="answers">
            ${question.answers
              .map(
                (a, i) =>
                  `<li class="answer"><span class="answer-letter">${ANSWER_LETTERS[i]})</span>&nbsp;${escHtml(a.text)}</li>`,
              )
              .join("")}
          </ul>
        </div>`;

      answerKeyHtml += `<li><span class="q-num">Q${globalQ}</span>: <strong>${escHtml(correctLetters)}</strong>${explanationHtml}</li>`;
    }
  }

  const totalQ = globalQ;

  return `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>${escHtml(title || "QCM")}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}]});setTimeout(()=>window.print(),600);"></script>
  <style>
    body{font-family:Georgia,'Times New Roman',serif;font-size:11pt;line-height:1.6;color:#111;max-width:720px;margin:0 auto;padding:24px}
    h1{font-size:18pt;text-align:center;margin-bottom:4px}
    .meta{text-align:center;color:#666;font-size:10pt;margin-bottom:28px}
    h2.chapter-title{font-size:13pt;border-bottom:1px solid #bbb;padding-bottom:4px;margin:28px 0 12px}
    .question{margin-bottom:22px;page-break-inside:avoid}
    .question-text{margin:0 0 7px;font-weight:500}
    .q-num{font-weight:700}
    .answers{list-style:none;margin:0;padding-left:18px}
    .answer{margin:3px 0}
    .answer-letter{font-weight:600;display:inline-block;width:20px}
    .answer-key-section{margin-top:48px;border-top:2px solid #333;padding-top:18px}
    .answer-key-section h2{font-size:14pt;margin-bottom:14px}
    .answer-key-list{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:5px 20px}
    .answer-key-list li{font-size:10pt}
    .explanation{font-size:9.5pt;color:#444;margin:2px 0 0 16px}
    @media print{body{padding:0}.answer-key-section{page-break-before:always}}
  </style>
</head>
<body>
  <h1>${escHtml(title || "QCM")}</h1>
  <p class="meta">${totalQ} question${totalQ > 1 ? "s" : ""}</p>
  ${questionsHtml}
  <div class="answer-key-section">
    <h2>Corrigé</h2>
    <ul class="answer-key-list">${answerKeyHtml}</ul>
  </div>
</body>
</html>`;
}

interface UsePrintOptions {
  viewerType: string;
  materialId: string;
  fileName: string;
  mimeType: string;
}

export function usePrint({ viewerType, materialId, fileName }: UsePrintOptions) {
  const [isPrinting, setIsPrinting] = useState(false);
  const canPrint = isPrintable(viewerType);

  const print = useCallback(async () => {
    if (!canPrint) return;
    setIsPrinting(true);

    try {
      switch (viewerType) {
        case "pdf": {
          const blob = await fetchMaterialBlob(materialId);
          const blobUrl = URL.createObjectURL(blob);
          printInIframe(blobUrl, { isBlobUrl: true, title: fileName });
          // Revoke after a delay to allow the iframe to use it
          setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
          break;
        }

        case "image": {
          const blob = await fetchMaterialBlob(materialId);
          const blobUrl = URL.createObjectURL(blob);
          const html = `
            <div style="display:flex;align-items:center;justify-content:center;min-height:100vh;">
              <img src="${blobUrl}" alt="${fileName}" style="max-width:100%;max-height:100vh;object-fit:contain;" />
            </div>
          `;
          printInIframe(html, { title: fileName });
          setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
          break;
        }

        case "code": {
          const response = await fetchMaterialFile(materialId);
          const text = await response.text();
          // Escape HTML in the source code
          const escaped = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          const html = `<pre><code>${escaped}</code></pre>`;
          const css = `
            pre { font-size: 11px; line-height: 1.5; }
            code { font-family: "SF Mono", "Fira Code", "Consolas", monospace; }
          `;
          printInIframe(html, { title: fileName, css });
          break;
        }

        case "markdown": {
          const entry = getViewerPrint(materialId);
          const renderedHtml = entry?.getContent?.();
          if (renderedHtml) {
            printInIframe(renderedHtml, { title: fileName, css: MARKDOWN_PRINT_CSS });
          } else {
            // Fallback: viewer not mounted yet, print raw text
            const response = await fetchMaterialFile(materialId);
            const text = await response.text();
            const escaped = text
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;");
            printInIframe(`<pre style="white-space:pre-wrap;">${escaped}</pre>`, { title: fileName });
          }
          break;
        }

        case "office": {
          const entry = getViewerPrint(materialId);
          if (entry?.print) {
            entry.print();
          } else {
            toast.info("Document is still loading. Please try again in a moment.");
            return;
          }
          break;
        }

        case "qcm": {
          const response = await fetchMaterialFile(materialId);
          const json = (await response.json()) as QCMFile;
          const html = buildQcmPrintHtml(json, fileName);
          const win = window.open("", "_blank");
          if (!win) {
            toast.error("Pop-up bloqué. Autorisez les pop-ups pour imprimer le QCM.");
            return;
          }
          win.document.write(html);
          win.document.close();
          break;
        }

        default:
          toast.info("Printing is not supported for this file type.");
          return;
      }

      toast.success("Print dialog opened");
    } catch (error) {
      console.error("Print failed:", error);
      toast.error("Failed to prepare document for printing.");
    } finally {
      setIsPrinting(false);
    }
  }, [viewerType, materialId, fileName, canPrint]);

  return { print, isPrinting, canPrint };
}
