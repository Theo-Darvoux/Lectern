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
// Loaded lazily and cached across calls.

let katexCssPromise: Promise<string> | null = null;

async function getKatexCssWithInlinedFonts(): Promise<string> {
  if (!katexCssPromise) {
    katexCssPromise = (async () => {
      // Find the KaTeX stylesheet that the browser already loaded.
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

      // Inline every font url() so the SVG blob can use them without CORS.
      const fontRe = /url\(["']?([^"')]+\.(woff2?|ttf)[^"')]*?)["']?\)/g;
      const matches = [...css.matchAll(fontRe)];
      const unique = [...new Set(matches.map((m) => m[1]))];

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
            // leave the reference as-is if the fetch fails
          }
        }),
      );

      return css;
    })();
  }
  return katexCssPromise;
}

// ─── Math → PNG data URL ─────────────────────────────────────────────────────

const RENDER_FONT_SIZE = 18; // px — high enough for clean rasterisation
const PDF_PT_PER_PX = 0.75; // 96 dpi: 1px = 0.75pt
const RETINA = 2; // scale factor for crisp output

interface MathImage {
  dataUrl: string;
  /** Width in PDF points */
  widthPt: number;
  /** Height in PDF points */
  heightPt: number;
}

async function renderMathToImage(
  latex: string,
  displayMode: boolean,
  katexCss: string,
): Promise<MathImage> {
  // Render KaTeX to HTML string.
  let mathHtml: string;
  try {
    mathHtml = katex.renderToString(latex, {
      throwOnError: false,
      displayMode,
    });
  } catch {
    mathHtml = `<span>[${latex}]</span>`;
  }

  // Measure the rendered size using a temporary off-screen element.
  const measurer = document.createElement("div");
  measurer.style.cssText =
    `position:absolute;left:-9999px;top:0;font-size:${RENDER_FONT_SIZE}px;white-space:nowrap;visibility:hidden`;
  measurer.innerHTML = mathHtml;
  document.body.appendChild(measurer);
  const rect = measurer.getBoundingClientRect();
  document.body.removeChild(measurer);

  const w = Math.ceil(rect.width) + 8;
  const h = Math.ceil(rect.height) + 4;

  // Build an SVG that uses <foreignObject> to host the KaTeX HTML.
  // Inline the KaTeX CSS (with embedded fonts) so the canvas can draw it.
  const svgSrc = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`,
    `<style>${katexCss}</style>`,
    `<foreignObject width="${w}" height="${h}">`,
    `<div xmlns="http://www.w3.org/1999/xhtml"`,
    ` style="font-size:${RENDER_FONT_SIZE}px;padding:2px;line-height:1.2;">`,
    mathHtml,
    `</div>`,
    `</foreignObject>`,
    `</svg>`,
  ].join("");

  const svgBlob = new Blob([svgSrc], { type: "image/svg+xml" });
  const svgUrl = URL.createObjectURL(svgBlob);

  return new Promise((resolve, reject) => {
    const img = new window.Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = w * RETINA;
      canvas.height = h * RETINA;
      const ctx = canvas.getContext("2d")!;
      ctx.scale(RETINA, RETINA);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(svgUrl);
      const dataUrl = canvas.toDataURL("image/png");
      resolve({
        dataUrl,
        widthPt: w * PDF_PT_PER_PX,
        heightPt: h * PDF_PT_PER_PX,
      });
    };
    img.onerror = () => {
      URL.revokeObjectURL(svgUrl);
      reject(new Error(`Failed to render math: ${latex}`));
    };
    img.src = svgUrl;
  });
}

// ─── Segment parser ───────────────────────────────────────────────────────────

type Segment =
  | { kind: "text"; content: string; bold?: boolean; italic?: boolean }
  | { kind: "math"; latex: string; display: boolean };

function parseSegments(raw: string): Segment[] {
  const MATH =
    /\$\$([\s\S]*?)\$\$|\$([^$\n]+?)\$|\\\[([\s\S]*?)\\\]|\\\(([^)]*?)\\\)/g;
  const out: Segment[] = [];
  let cursor = 0;
  let m: RegExpExecArray | null;

  while ((m = MATH.exec(raw)) !== null) {
    if (m.index > cursor) out.push(...parseFormatting(raw.slice(cursor, m.index)));
    if (m[1] !== undefined || m[3] !== undefined) {
      out.push({ kind: "math", latex: (m[1] ?? m[3])!, display: true });
    } else {
      out.push({ kind: "math", latex: (m[2] ?? m[4])!, display: false });
    }
    cursor = MATH.lastIndex;
  }
  if (cursor < raw.length) out.push(...parseFormatting(raw.slice(cursor)));
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

// Collect every unique (latex, display) pair from the QCM.
function collectMathExpressions(qcm: QCMFile): Array<{ latex: string; display: boolean }> {
  const seen = new Set<string>();
  const result: Array<{ latex: string; display: boolean }> = [];

  const add = (latex: string, display: boolean) => {
    const key = `${display}::${latex}`;
    if (!seen.has(key)) {
      seen.add(key);
      result.push({ latex, display });
    }
  };

  for (const ch of qcm.chapters) {
    for (const q of ch.questions) {
      for (const seg of parseSegments(q.text)) {
        if (seg.kind === "math") add(seg.latex, seg.display);
      }
      for (const a of q.answers) {
        for (const seg of parseSegments(a.text)) {
          if (seg.kind === "math") add(seg.latex, seg.display);
        }
      }
      if (q.explanation) {
        for (const seg of parseSegments(q.explanation)) {
          if (seg.kind === "math") add(seg.latex, seg.display);
        }
      }
    }
  }
  return result;
}

// ─── react-pdf styles ─────────────────────────────────────────────────────────

const BODY_PT = 11;

const styles = StyleSheet.create({
  page: {
    fontFamily: "Times-Roman",
    fontSize: BODY_PT,
    lineHeight: 1.55,
    color: "#111111",
    paddingTop: 56,
    paddingBottom: 56,
    paddingHorizontal: 56,
  },
  title: { fontFamily: "Times-Bold", fontSize: 18, textAlign: "center", marginBottom: 4 },
  meta: { fontSize: 9, color: "#666666", textAlign: "center", marginBottom: 28 },
  chapterTitle: {
    fontFamily: "Times-Bold",
    fontSize: 13,
    borderBottomWidth: 0.5,
    borderBottomColor: "#999999",
    paddingBottom: 3,
    marginTop: 24,
    marginBottom: 12,
  },
  question: { marginBottom: 20 },
  questionHeader: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "flex-end",
    marginBottom: 6,
  },
  qNum: { fontFamily: "Times-Bold", fontSize: BODY_PT, marginRight: 4, flexShrink: 0 },
  answerRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "flex-end",
    marginBottom: 3,
    paddingLeft: 14,
  },
  answerLetter: {
    fontFamily: "Times-Bold",
    fontSize: BODY_PT,
    marginRight: 4,
    width: 16,
    flexShrink: 0,
  },
  answerKeySection: {
    marginTop: 40,
    borderTopWidth: 1.5,
    borderTopColor: "#333333",
    paddingTop: 16,
  },
  answerKeyTitle: { fontFamily: "Times-Bold", fontSize: 13, marginBottom: 12 },
  answerKeyGrid: { flexDirection: "row", flexWrap: "wrap" },
  answerKeyItem: { width: "25%", fontSize: 9.5, marginBottom: 5 },
  answerKeyQ: { fontFamily: "Times-Bold" },
  explanationText: {
    fontSize: 9,
    color: "#555555",
    marginTop: 2,
    marginLeft: 14,
    fontFamily: "Times-Italic",
  },
});

// ─── react-pdf components ─────────────────────────────────────────────────────

type MathCache = Map<string, MathImage>;

function mathKey(latex: string, display: boolean) {
  return `${display}::${latex}`;
}

function InlineContent({
  segments,
  cache,
}: {
  segments: Segment[];
  cache: MathCache;
}) {
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "text") {
          const style = seg.bold
            ? { fontFamily: "Times-Bold", fontSize: BODY_PT }
            : seg.italic
              ? { fontFamily: "Times-Italic", fontSize: BODY_PT }
              : { fontSize: BODY_PT };
          return <Text key={i} style={style}>{seg.content}</Text>;
        }
        const img = cache.get(mathKey(seg.latex, seg.display));
        if (!img) return <Text key={i} style={{ fontFamily: "Times-Italic", fontSize: BODY_PT }}>[{seg.latex}]</Text>;
        return (
          <PdfImage
            key={i}
            src={img.dataUrl}
            style={{ width: img.widthPt, height: img.heightPt }}
          />
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
            if (!img) return <Text key={i} style={{ fontFamily: "Times-Italic" }}>[{seg.latex}]</Text>;
            return (
              <View key={i} style={{ alignItems: "center", marginVertical: 4 }}>
                <PdfImage src={img.dataUrl} style={{ width: img.widthPt, height: img.heightPt }} />
              </View>
            );
          }
          if (seg.kind === "math") {
            const img = cache.get(mathKey(seg.latex, false));
            if (!img) return <Text key={i} style={{ fontFamily: "Times-Italic" }}>[{seg.latex}]</Text>;
            return (
              <View key={i} style={{ flexDirection: "row" }}>
                <PdfImage src={img.dataUrl} style={{ width: img.widthPt, height: img.heightPt }} />
              </View>
            );
          }
          const textStyle = seg.bold
            ? { fontFamily: "Times-Bold", fontSize: BODY_PT }
            : seg.italic
              ? { fontFamily: "Times-Italic", fontSize: BODY_PT }
              : { fontSize: BODY_PT };
          return <Text key={i} style={textStyle}>{seg.content}</Text>;
        })}
      </View>
    );
  }

  return (
    <View style={[{ flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end" }, outerStyle] as never}>
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
        <Text style={styles.title}>{title || "QCM"}</Text>
        <Text style={styles.meta}>
          {totalQ} question{totalQ !== 1 ? "s" : ""}
        </Text>

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
                    <RichText text={q.text} cache={cache} outerStyle={{ flex: 1 }} />
                  </View>
                  {q.answers.map((a, ai) => (
                    <View key={a.id} style={styles.answerRow}>
                      <Text style={styles.answerLetter}>{ANSWER_LETTERS[ai]})</Text>
                      <RichText text={a.text} cache={cache} outerStyle={{ flex: 1 }} />
                    </View>
                  ))}
                </View>
              );
            })}
          </View>
        ))}

        <View style={styles.answerKeySection} break>
          <Text style={styles.answerKeyTitle}>Corrigé</Text>
          <View style={styles.answerKeyGrid}>
            {answerKey.map((entry) => (
              <View key={entry.num} style={styles.answerKeyItem}>
                <Text>
                  <Text style={styles.answerKeyQ}>Q{entry.num}</Text>
                  {" : "}{entry.letters}
                </Text>
                {entry.explanation ? (
                  <Text style={styles.explanationText}>
                    {entry.explanation.replace(/(\$\$?)[^$]*\1/g, "[math]")}
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
  // 1. Load KaTeX CSS with inlined fonts (cached after first call).
  const katexCss = await getKatexCssWithInlinedFonts();

  // 2. Collect all unique math expressions and render them to PNGs.
  const expressions = collectMathExpressions(qcm);
  const cache: MathCache = new Map();
  await Promise.all(
    expressions.map(async ({ latex, display }) => {
      try {
        const img = await renderMathToImage(latex, display, katexCss);
        cache.set(mathKey(latex, display), img);
      } catch {
        // Gracefully skip — fallback text is shown by the components.
      }
    }),
  );

  // 3. Build the PDF document and return the blob.
  const instance = pdf(<QCMDocument qcm={qcm} title={title} cache={cache} />);
  return instance.toBlob();
}
