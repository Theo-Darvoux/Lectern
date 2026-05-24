"use client";

import React from "react";
import {
  Document,
  Page,
  Text,
  View,
  Image as PdfImage,
  StyleSheet,
  pdf,
} from "@react-pdf/renderer";
import katex from "katex";
import type { QCMFile } from "./qcm-types";

// ─── KaTeX CSS with inlined fonts ────────────────────────────────────────────

let katexCssPromise: Promise<string> | null = null;

async function getKatexCssWithInlinedFonts(): Promise<string> {
  if (!katexCssPromise) {
    katexCssPromise = (async () => {
      let sheetHref: string | null = null;
      for (const sheet of document.styleSheets) {
        try {
          if (!sheet.href || !sheet.cssRules) continue;
          for (const rule of sheet.cssRules) {
            if (rule.cssText?.includes("KaTeX_Main")) {
              sheetHref = sheet.href;
              break;
            }
          }
        } catch {
          // cross-origin sheet — skip
        }
        if (sheetHref) break;
      }
      if (!sheetHref) return "";
      let css = await fetch(sheetHref).then((r) => r.text());
      const fontRe = /url\(["']?([^"')]+\.(woff2?|ttf)[^"')]*?)["']?\)/g;
      const unique = [...new Set([...css.matchAll(fontRe)].map((m) => m[1]))];
      await Promise.all(
        unique.map(async (rel) => {
          const abs = new URL(rel, sheetHref!).href;
          try {
            const blob = await fetch(abs).then((r) => r.blob());
            const b64 = await new Promise<string>((res, rej) => {
              const fr = new FileReader();
              fr.onload = () => res(fr.result as string);
              fr.onerror = rej;
              fr.readAsDataURL(blob);
            });
            css = css.replaceAll(`url("${rel}")`, `url("${b64}")`);
            css = css.replaceAll(`url('${rel}')`, `url("${b64}")`);
            css = css.replaceAll(`url(${rel})`, `url("${b64}")`);
          } catch {
            // leave as-is
          }
        }),
      );
      return css;
    })();
  }
  return katexCssPromise;
}

// ─── Math → PNG ──────────────────────────────────────────────────────────────

// Render at high resolution then scale down to match PDF body font size exactly.
const BODY_PT = 11;
const RENDER_FONT_SIZE = 24; // px — high enough for crisp rasterisation
const RETINA = 3;
// Scale factor: rendered at RENDER_FONT_SIZE px = RENDER_FONT_SIZE * 0.75 pt at 96 dpi.
// We want inline math to appear at BODY_PT, display math at BODY_PT * 1.3.
const INLINE_PDF_SCALE = BODY_PT / (RENDER_FONT_SIZE * 0.75);
const DISPLAY_PDF_SCALE = (BODY_PT * 1.3) / (RENDER_FONT_SIZE * 0.75);

interface MathImage {
  dataUrl: string;
  widthPt: number;
  heightPt: number;
}

async function renderMathToImage(
  latex: string,
  displayMode: boolean,
  katexCss: string,
): Promise<MathImage> {
  let mathHtml: string;
  try {
    mathHtml = katex.renderToString(latex, { throwOnError: false, displayMode });
  } catch {
    mathHtml = `<span>[${latex}]</span>`;
  }

  const measurer = document.createElement("div");
  measurer.style.cssText = `position:absolute;left:-9999px;top:0;font-size:${RENDER_FONT_SIZE}px;white-space:nowrap;visibility:hidden`;
  measurer.innerHTML = mathHtml;
  document.body.appendChild(measurer);
  const rect = measurer.getBoundingClientRect();
  document.body.removeChild(measurer);

  const w = Math.max(Math.ceil(rect.width) + 12, 20);
  const h = Math.max(Math.ceil(rect.height) + 8, 20);

  const svgSrc = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`,
    `<style>${katexCss}</style>`,
    `<foreignObject width="${w}" height="${h}">`,
    `<div xmlns="http://www.w3.org/1999/xhtml"`,
    ` style="font-size:${RENDER_FONT_SIZE}px;padding:4px 6px;line-height:1.2;color:#111111;background:white;">`,
    mathHtml,
    `</div>`,
    `</foreignObject>`,
    `</svg>`,
  ].join("");

  const svgBlob = new Blob([svgSrc], { type: "image/svg+xml" });
  const svgUrl = URL.createObjectURL(svgBlob);

  const pdfScale = displayMode ? DISPLAY_PDF_SCALE : INLINE_PDF_SCALE;

  return new Promise((resolve, reject) => {
    const img = new window.Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = w * RETINA;
      canvas.height = h * RETINA;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(RETINA, RETINA);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(svgUrl);
      resolve({
        dataUrl: canvas.toDataURL("image/png"),
        widthPt: w * pdfScale,
        heightPt: h * pdfScale,
      });
    };
    img.onerror = () => {
      URL.revokeObjectURL(svgUrl);
      reject(new Error(`Failed to render math: ${latex}`));
    };
    img.src = svgUrl;
  });
}

// ─── Markdown + LaTeX parser ──────────────────────────────────────────────────

type Segment =
  | { kind: "text"; content: string; bold?: boolean; italic?: boolean }
  | { kind: "math"; latex: string; display: boolean };

// Strip markdown heading markers and trim.
function stripHeadings(raw: string): string {
  return raw
    .split("\n")
    .map((line) => line.replace(/^#{1,6}\s*/, ""))
    .join("\n")
    .trim();
}

function parseSegments(raw: string): Segment[] {
  const text = stripHeadings(raw);
  // $$...$$  $...$  \[...\]  \(...\)  \begin{env}...\end{env} (same env via \5 backref)
  const MATH =
    /\$\$([\s\S]*?)\$\$|\$([^$\n]+?)\$|\\\[([\s\S]*?)\\\]|\\\(([^)]*?)\\\)|\\begin\{([^}]+)\}([\s\S]*?)\\end\{\5\}/g;
  const out: Segment[] = [];
  let cursor = 0;
  let m: RegExpExecArray | null;

  while ((m = MATH.exec(text)) !== null) {
    if (m.index > cursor) out.push(...parseFormatting(text.slice(cursor, m.index)));
    // Groups: 1=$$, 2=$, 3=\[, 4=\(, 6=\begin{env} content — all display except 2 and 4
    const isDisplay = m[1] !== undefined || m[3] !== undefined || m[6] !== undefined;
    const latex = (m[1] ?? m[2] ?? m[3] ?? m[4] ?? m[6] ?? "").trim();
    if (latex) out.push({ kind: "math", latex, display: isDisplay });
    cursor = MATH.lastIndex;
  }
  if (cursor < text.length) out.push(...parseFormatting(text.slice(cursor)));
  return out;
}

function parseFormatting(text: string): Segment[] {
  const FMT = /\*\*([^*]+)\*\*|\*([^*]+)\*/g;
  const out: Segment[] = [];
  let cursor = 0;
  let m: RegExpExecArray | null;

  while ((m = FMT.exec(text)) !== null) {
    if (m.index > cursor) out.push({ kind: "text", content: text.slice(cursor, m.index) });
    if (m[1] !== undefined) out.push({ kind: "text", content: m[1], bold: true });
    else out.push({ kind: "text", content: m[2]!, italic: true });
    cursor = FMT.lastIndex;
  }
  if (cursor < text.length) out.push({ kind: "text", content: text.slice(cursor) });
  return out.length ? out : [{ kind: "text", content: text }];
}

function collectMathExpressions(qcm: QCMFile): Array<{ latex: string; display: boolean }> {
  const seen = new Set<string>();
  const result: Array<{ latex: string; display: boolean }> = [];

  const add = (latex: string, display: boolean) => {
    const key = `${display}::${latex}`;
    if (!seen.has(key)) { seen.add(key); result.push({ latex, display }); }
  };

  for (const ch of qcm.chapters) {
    for (const q of ch.questions) {
      for (const seg of parseSegments(q.text)) if (seg.kind === "math") add(seg.latex, seg.display);
      for (const a of q.answers)
        for (const seg of parseSegments(a.text)) if (seg.kind === "math") add(seg.latex, seg.display);
      if (q.explanation)
        for (const seg of parseSegments(q.explanation)) if (seg.kind === "math") add(seg.latex, seg.display);
    }
  }
  return result;
}

// Strip all math environments from explanation text (used in corrigé footnotes).
function stripAllMath(text: string): string {
  return text
    .replace(/\\begin\{[^}]+\}[\s\S]*?\\end\{[^}]+\}/g, "[formule]")
    .replace(/\$\$[\s\S]*?\$\$/g, "[formule]")
    .replace(/\$[^$\n]+?\$/g, "[formule]")
    .replace(/\\\[[\s\S]*?\\\]/g, "[formule]")
    .replace(/\\\([^)]*?\\\)/g, "[formule]")
    .replace(/^#{1,6}\s*/gm, "")
    .trim();
}

// ─── Styles ──────────────────────────────────────────────────────────────────

const ACCENT = "#1e3a8a";
const ACCENT_LIGHT = "#dbeafe";

const styles = StyleSheet.create({
  page: {
    fontFamily: "Helvetica",
    fontSize: BODY_PT,
    lineHeight: 1.5,
    color: "#111111",
    paddingTop: 52,
    paddingBottom: 52,
    paddingHorizontal: 52,
  },
  // ── Header
  headerContainer: { alignItems: "center", marginBottom: 22 },
  title: {
    fontFamily: "Helvetica-Bold",
    fontSize: 22,
    textAlign: "center",
    color: ACCENT,
    marginBottom: 4,
    letterSpacing: 0.3,
  },
  meta: { fontSize: 9, color: "#888888", textAlign: "center" },
  headerRule: { width: "50%", height: 2, backgroundColor: ACCENT, marginTop: 10 },
  // ── Chapter
  chapterTitle: {
    fontFamily: "Helvetica-Bold",
    fontSize: 11,
    color: ACCENT,
    backgroundColor: ACCENT_LIGHT,
    paddingVertical: 4,
    paddingHorizontal: 8,
    marginTop: 20,
    marginBottom: 12,
  },
  // ── Question
  question: { marginBottom: 16 },
  questionHeader: { flexDirection: "row", alignItems: "flex-start", marginBottom: 6 },
  qNum: {
    fontFamily: "Helvetica-Bold",
    fontSize: BODY_PT,
    color: ACCENT,
    marginRight: 5,
    flexShrink: 0,
  },
  questionText: { flex: 1 },
  // ── Answer
  answerRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginBottom: 4,
    paddingLeft: 20,
  },
  answerCheckbox: {
    width: 9,
    height: 9,
    borderWidth: 0.75,
    borderColor: "#9ca3af",
    marginRight: 6,
    marginTop: 1.5,
    flexShrink: 0,
  },
  answerLetter: {
    fontFamily: "Helvetica-Bold",
    fontSize: BODY_PT,
    width: 16,
    flexShrink: 0,
    color: "#374151",
  },
  answerContent: { flex: 1 },
  // ── Corrigé
  answerKeySection: {
    marginTop: 32,
    paddingTop: 14,
    borderTopWidth: 1.5,
    borderTopColor: "#374151",
  },
  answerKeyTitle: {
    fontFamily: "Helvetica-Bold",
    fontSize: 14,
    marginBottom: 14,
    color: "#111111",
  },
  answerKeyGrid: { flexDirection: "row", flexWrap: "wrap" },
  answerKeyItem: { width: "25%", marginBottom: 10, paddingRight: 8 },
  answerKeyQ: { fontFamily: "Helvetica-Bold", fontSize: 9.5, color: ACCENT },
  answerKeyLetters: { fontSize: 9.5, marginTop: 1 },
  explanationText: {
    fontSize: 8,
    color: "#6b7280",
    marginTop: 3,
    fontFamily: "Helvetica-Oblique",
    lineHeight: 1.4,
  },
  // ── Page number
  pageNumber: {
    position: "absolute",
    bottom: 24,
    right: 52,
    fontSize: 8,
    color: "#aaaaaa",
  },
});

// ─── react-pdf components ─────────────────────────────────────────────────────

type MathCache = Map<string, MathImage>;

function mathKey(latex: string, display: boolean) {
  return `${display}::${latex}`;
}

function InlineContent({ segments, cache }: { segments: Segment[]; cache: MathCache }) {
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "text") {
          const style = seg.bold
            ? { fontFamily: "Helvetica-Bold" as const, fontSize: BODY_PT }
            : seg.italic
              ? { fontFamily: "Helvetica-Oblique" as const, fontSize: BODY_PT }
              : { fontSize: BODY_PT };
          return <Text key={i} style={style}>{seg.content}</Text>;
        }
        const img = cache.get(mathKey(seg.latex, seg.display));
        if (!img)
          return (
            <Text key={i} style={{ fontFamily: "Helvetica-Oblique", fontSize: BODY_PT }}>
              [{seg.latex}]
            </Text>
          );
        return (
          <PdfImage key={i} src={img.dataUrl} style={{ width: img.widthPt, height: img.heightPt }} />
        );
      })}
    </>
  );
}

function RichText({
  text,
  cache,
  outerStyle,
}: {
  text: string;
  cache: MathCache;
  outerStyle?: object;
}) {
  const segments = parseSegments(text);
  const hasDisplayMath = segments.some((s) => s.kind === "math" && s.display);

  if (hasDisplayMath) {
    return (
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <View style={outerStyle as any}>
        {segments.map((seg, i) => {
          if (seg.kind === "math" && seg.display) {
            const img = cache.get(mathKey(seg.latex, true));
            if (!img)
              return (
                <Text key={i} style={{ fontFamily: "Helvetica-Oblique" }}>[{seg.latex}]</Text>
              );
            return (
              <View key={i} style={{ alignItems: "center", marginVertical: 6 }}>
                <PdfImage src={img.dataUrl} style={{ width: img.widthPt, height: img.heightPt }} />
              </View>
            );
          }
          if (seg.kind === "math") {
            const img = cache.get(mathKey(seg.latex, false));
            if (!img)
              return (
                <Text key={i} style={{ fontFamily: "Helvetica-Oblique" }}>[{seg.latex}]</Text>
              );
            return (
              <View key={i} style={{ flexDirection: "row", alignItems: "center" }}>
                <PdfImage src={img.dataUrl} style={{ width: img.widthPt, height: img.heightPt }} />
              </View>
            );
          }
          const textStyle = seg.bold
            ? { fontFamily: "Helvetica-Bold" as const, fontSize: BODY_PT }
            : seg.italic
              ? { fontFamily: "Helvetica-Oblique" as const, fontSize: BODY_PT }
              : { fontSize: BODY_PT };
          return <Text key={i} style={textStyle}>{seg.content}</Text>;
        })}
      </View>
    );
  }

  return (
    <View
      style={[{ flexDirection: "row", flexWrap: "wrap", alignItems: "center" }, outerStyle] as never}
    >
      <InlineContent segments={segments} cache={cache} />
    </View>
  );
}

// ─── PDF Document ─────────────────────────────────────────────────────────────

const ANSWER_LETTERS = ["A", "B", "C", "D"] as const;

function QCMDocument({ qcm, title, cache }: { qcm: QCMFile; title: string; cache: MathCache }) {
  let globalQ = 0;
  const answerKey: Array<{ num: number; letters: string; explanation?: string }> = [];

  for (const ch of qcm.chapters) {
    for (const q of ch.questions) {
      globalQ++;
      const letters = q.answers
        .map((a, i) => (a.correct ? ANSWER_LETTERS[i] : null))
        .filter(Boolean)
        .join(", ");
      answerKey.push({ num: globalQ, letters: letters || "—", explanation: q.explanation });
    }
  }

  const totalQ = globalQ;
  let qCounter = 0;

  return (
    <Document>
      <Page size="A4" style={styles.page}>
        <Text
          style={styles.pageNumber}
          render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`}
          fixed
        />

        {/* Header */}
        <View style={styles.headerContainer}>
          <Text style={styles.title}>{title || "QCM"}</Text>
          <Text style={styles.meta}>
            {totalQ} question{totalQ !== 1 ? "s" : ""}
          </Text>
          <View style={styles.headerRule} />
        </View>

        {/* Questions */}
        {qcm.chapters.map((ch) => (
          <View key={ch.id}>
            {ch.title ? <Text style={styles.chapterTitle}>{ch.title}</Text> : null}

            {ch.questions.map((q) => {
              qCounter++;
              const num = qCounter;
              return (
                <View key={q.id} style={styles.question} wrap={false}>
                  <View style={styles.questionHeader}>
                    <Text style={styles.qNum}>Q{num}.</Text>
                    <RichText text={q.text} cache={cache} outerStyle={styles.questionText} />
                  </View>
                  {q.answers.map((a, ai) => (
                    <View key={a.id} style={styles.answerRow}>
                      <View style={styles.answerCheckbox} />
                      <Text style={styles.answerLetter}>{ANSWER_LETTERS[ai]})</Text>
                      <RichText text={a.text} cache={cache} outerStyle={styles.answerContent} />
                    </View>
                  ))}
                </View>
              );
            })}
          </View>
        ))}

        {/* Answer key */}
        <View style={styles.answerKeySection} break>
          <Text style={styles.answerKeyTitle}>Corrigé</Text>
          <View style={styles.answerKeyGrid}>
            {answerKey.map((entry) => (
              <View key={entry.num} style={styles.answerKeyItem}>
                <Text style={styles.answerKeyQ}>Q{entry.num}</Text>
                <Text style={styles.answerKeyLetters}>{entry.letters}</Text>
                {entry.explanation ? (
                  <Text style={styles.explanationText}>
                    {stripAllMath(entry.explanation)}
                  </Text>
                ) : null}
              </View>
            ))}
          </View>
        </View>
      </Page>
    </Document>
  );
}

// ─── Public API ───────────────────────────────────────────────────────────────

export async function generateQcmPdfBlob(qcm: QCMFile, title: string): Promise<Blob> {
  const katexCss = await getKatexCssWithInlinedFonts();
  const expressions = collectMathExpressions(qcm);
  const cache: MathCache = new Map();
  await Promise.all(
    expressions.map(async ({ latex, display }) => {
      try {
        const img = await renderMathToImage(latex, display, katexCss);
        cache.set(mathKey(latex, display), img);
      } catch {
        // fallback text shown by components
      }
    }),
  );
  const instance = pdf(<QCMDocument qcm={qcm} title={title} cache={cache} />);
  return instance.toBlob();
}
