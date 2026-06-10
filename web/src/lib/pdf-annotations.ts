// Annotation overlay painting for the pdf.js viewer.
//
// pdf.js owns the page DOM imperatively, so instead of rendering React overlay
// elements per page (as the old react-pdf viewer did) we inject absolutely
// positioned <div>s directly into each page's element. This is re-run on every
// `textlayerrendered` event, so overlays stay correct across zoom/re-renders.

export interface PageAnnotation {
    selection_text: string | null;
    page: number | null;
    occurrenceIndex: number | null;
    threadId: string;
}

interface HighlightBounds {
    x: number;
    y: number;
    w: number;
    h: number;
    threadId: string;
}

/** Class on overlay nodes we inject, so we can find and clear our own nodes. */
const OVERLAY_CLASS = "pdf-annotation-overlay";

/**
 * Resolves DOM ranges for each annotation's `selection_text` inside a pdf.js
 * page's text layer. Walks the text nodes in DOM order and matches by occurrence
 * index, mirroring the selection-time logic in annotation-selection-tooltip.
 */
function buildHighlightRanges(
    pageEl: HTMLElement,
    annotations: PageAnnotation[],
): Array<{ range: Range; threadId: string }> {
    const textLayer = pageEl.querySelector(".textLayer");
    if (!textLayer) return [];

    const textNodes: { node: Text; start: number; end: number }[] = [];
    let fullText = "";

    function walk(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) {
            const t = node.textContent ?? "";
            if (t.length > 0) {
                textNodes.push({ node: node as Text, start: fullText.length, end: fullText.length + t.length });
                fullText += t;
            }
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            const el = node as Element;
            if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
            for (const child of el.childNodes) walk(child);
        }
    }
    walk(textLayer);
    if (textNodes.length === 0) return [];

    const results: Array<{ range: Range; threadId: string }> = [];

    for (const ann of annotations) {
        if (!ann.selection_text) continue;
        const targetOcc = ann.occurrenceIndex;
        let currentOcc = 0;
        let searchFrom = 0;
        let idx: number;

        while ((idx = fullText.indexOf(ann.selection_text, searchFrom)) !== -1) {
            const matchEnd = idx + ann.selection_text.length;
            if (targetOcc === null || currentOcc === targetOcc) {
                let startEntry: typeof textNodes[0] | null = null;
                let startOffset = 0;
                let endEntry: typeof textNodes[0] | null = null;
                let endOffset = 0;

                for (const entry of textNodes) {
                    if (!startEntry && entry.end > idx) { startEntry = entry; startOffset = idx - entry.start; }
                    if (entry.end >= matchEnd) { endEntry = entry; endOffset = matchEnd - entry.start; break; }
                }

                if (startEntry && endEntry) {
                    try {
                        const range = document.createRange();
                        range.setStart(startEntry.node, startOffset);
                        range.setEnd(endEntry.node, Math.min(endOffset, endEntry.node.length));
                        results.push({ range, threadId: ann.threadId });
                    } catch { /* ignore */ }
                }
                if (targetOcc !== null) break;
            }
            currentOcc++;
            searchFrom = matchEnd;
        }
    }
    return results;
}

/**
 * (Re)paints annotation highlight + click-target overlays for a single pdf.js
 * page. Clears any overlays we previously injected, then re-derives them from
 * the current text-layer geometry.
 */
export function paintPageHighlights(
    pageDiv: HTMLDivElement,
    pageNumber: number,
    annotations: PageAnnotation[],
    onAnnotationClick?: (threadId: string, e: MouseEvent) => void,
): void {
    // Clear previously injected overlays for this page.
    pageDiv.querySelectorAll(`.${OVERLAY_CLASS}`).forEach((n) => n.remove());

    const pageAnns = annotations.filter((a) => a.page === pageNumber || a.page == null);
    if (pageAnns.length === 0) return;

    const ranges = buildHighlightRanges(pageDiv, pageAnns);
    if (ranges.length === 0) return;

    const pageRect = pageDiv.getBoundingClientRect();
    const bounds: HighlightBounds[] = [];
    for (const { range, threadId } of ranges) {
        for (const r of range.getClientRects()) {
            if (r.width <= 0 || r.height <= 0) continue;
            bounds.push({
                x: r.left - pageRect.left,
                y: r.top - pageRect.top,
                w: r.width,
                h: r.height,
                threadId,
            });
        }
    }
    if (bounds.length === 0) return;

    const frag = document.createDocumentFragment();
    for (const b of bounds) {
        // Visual highlight (non-interactive).
        const hl = document.createElement("div");
        hl.className = `${OVERLAY_CLASS} annotation-highlight rounded-sm`;
        Object.assign(hl.style, {
            position: "absolute",
            left: `${b.x}px`,
            top: `${b.y}px`,
            width: `${b.w}px`,
            height: `${b.h}px`,
            zIndex: "1",
            pointerEvents: "none",
        } satisfies Partial<CSSStyleDeclaration>);
        frag.appendChild(hl);

        // Click target.
        if (onAnnotationClick) {
            const hit = document.createElement("div");
            hit.className = OVERLAY_CLASS;
            Object.assign(hit.style, {
                position: "absolute",
                left: `${b.x}px`,
                top: `${b.y}px`,
                width: `${b.w}px`,
                height: `${b.h}px`,
                zIndex: "10",
                cursor: "pointer",
            } satisfies Partial<CSSStyleDeclaration>);
            hit.addEventListener("mousedown", (e) => e.preventDefault());
            hit.addEventListener("click", (e) => onAnnotationClick(b.threadId, e));
            frag.appendChild(hit);
        }
    }
    pageDiv.appendChild(frag);
}
