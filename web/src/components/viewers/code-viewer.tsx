"use client";

import { useMemo, useRef } from "react";
import hljs from "highlight.js/lib/common";
import { List, type RowComponentProps } from "react-window";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";
import { ZoomControls } from "./zoom-controls";

// Languages from highlight.js/lib/common... (kept same as original)
import hljsDart from "highlight.js/lib/languages/dart";
import hljsElm from "highlight.js/lib/languages/elm";
import hljsElixir from "highlight.js/lib/languages/elixir";
import hljsErlang from "highlight.js/lib/languages/erlang";
import hljsFsharp from "highlight.js/lib/languages/fsharp";
import hljsGroovy from "highlight.js/lib/languages/groovy";
import hljsHaskell from "highlight.js/lib/languages/haskell";
import hljsJulia from "highlight.js/lib/languages/julia";
import hljsMatlab from "highlight.js/lib/languages/matlab";
import hljsNim from "highlight.js/lib/languages/nim";
import hljsNix from "highlight.js/lib/languages/nix";
import hljsOcaml from "highlight.js/lib/languages/ocaml";
import hljsPowershell from "highlight.js/lib/languages/powershell";
import hljsProtobuf from "highlight.js/lib/languages/protobuf";
import hljsScala from "highlight.js/lib/languages/scala";
import hljsClojure from "highlight.js/lib/languages/clojure";
import hljsTcl from "highlight.js/lib/languages/tcl";
import hljsD from "highlight.js/lib/languages/d";
import hljsX86asm from "highlight.js/lib/languages/x86asm";
import hljsCmake from "highlight.js/lib/languages/cmake";

hljs.registerLanguage("dart", hljsDart);
hljs.registerLanguage("elm", hljsElm);
hljs.registerLanguage("elixir", hljsElixir);
hljs.registerLanguage("erlang", hljsErlang);
hljs.registerLanguage("fsharp", hljsFsharp);
hljs.registerLanguage("groovy", hljsGroovy);
hljs.registerLanguage("haskell", hljsHaskell);
hljs.registerLanguage("julia", hljsJulia);
hljs.registerLanguage("matlab", hljsMatlab);
hljs.registerLanguage("nim", hljsNim);
hljs.registerLanguage("nix", hljsNix);
hljs.registerLanguage("ocaml", hljsOcaml);
hljs.registerLanguage("powershell", hljsPowershell);
hljs.registerLanguage("protobuf", hljsProtobuf);
hljs.registerLanguage("scala", hljsScala);
hljs.registerLanguage("clojure", hljsClojure);
hljs.registerLanguage("tcl", hljsTcl);
hljs.registerLanguage("d", hljsD);
hljs.registerLanguage("x86asm", hljsX86asm);
hljs.registerLanguage("cmake", hljsCmake);

/* Minimal LaTeX grammar */
hljs.registerLanguage("latex", () => ({
    name: "LaTeX",
    aliases: ["tex"],
    contains: [
        { className: "comment", begin: "%", end: "$", relevance: 0 },
        { className: "keyword", begin: /\\[a-zA-Z@]+/, relevance: 0 },
        {
            className: "params",
            begin: /\{/,
            end: /\}/,
            contains: [{ className: "keyword", begin: /\\[a-zA-Z@]+/ }, "self"],
        },
        { className: "params", begin: /\[/, end: /\]/ },
        {
            className: "formula",
            begin: /\$\$/,
            end: /\$\$/,
            contains: [{ className: "keyword", begin: /\\[a-zA-Z@]+/ }],
        },
        {
            className: "formula",
            begin: /\$/,
            end: /\$/,
            contains: [{ className: "keyword", begin: /\\[a-zA-Z@]+/ }],
        },
    ],
}));

/* Register additional aliases */
hljs.registerAliases("toml", { languageName: "ini" });
hljs.registerAliases(["kts", "groovy", "gradle"], { languageName: "groovy" });
hljs.registerAliases(["lhs"], { languageName: "haskell" });
hljs.registerAliases(["rmd"], { languageName: "r" });
hljs.registerAliases(["exs"], { languageName: "elixir" });
hljs.registerAliases(["hrl", "erlang-repl"], { languageName: "erlang" });
hljs.registerAliases(["cljs", "cljc", "edn"], { languageName: "clojure" });
hljs.registerAliases(["mli"], { languageName: "ocaml" });
hljs.registerAliases(["psm1", "psd1"], { languageName: "powershell" });
hljs.registerAliases(["gql"], { languageName: "graphql" });
hljs.registerAliases(["patch"], { languageName: "diff" });
hljs.registerAliases(["fsx"], { languageName: "fsharp" });
hljs.registerAliases(["pyw", "pyi"], { languageName: "python" });
hljs.registerAliases(["cxx", "cc", "hxx"], { languageName: "cpp" });
hljs.registerAliases(["mjs", "cjs"], { languageName: "javascript" });
hljs.registerAliases(["htm"], { languageName: "xml" });
hljs.registerAliases(["pm"], { languageName: "perl" });
hljs.registerAliases(["yml"], { languageName: "yaml" });
hljs.registerAliases(["cfg", "conf", "env"], { languageName: "ini" });
hljs.registerAliases(["json5", "jsonc"], { languageName: "json" });
hljs.registerAliases(["md", "markdown"], { languageName: "markdown" });
hljs.registerAliases(["s"], { languageName: "x86asm" });

/* highlight.js theme CSS — loaded once */
import "highlight.js/styles/github.css";

const MIN_ZOOM = 50;
const MAX_ZOOM = 200;
const ZOOM_STEP = 10;

// Base monospace metrics at 100% zoom. Row height and the horizontal content
// width are derived from these so the virtualised list can size rows ahead of
// time (no per-row measurement pass — far cheaper than dynamic heights).
const BASE_FONT_PX = 13;
const LINE_HEIGHT_RATIO = 1.5;
const CHAR_WIDTH_RATIO = 0.62; // ~advance width of a monospace glyph per font px
const OVERSCAN_ROWS = 8;

function escapeHtml(s: string): string {
    return s.replace(/[&<>]/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"));
}

interface CodeViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

const EXT_TO_LANG: Record<string, string> = {
    tex: "latex", latex: "latex", sty: "latex", cls: "latex", bib: "latex", dtx: "latex", ins: "latex",
    c: "c", h: "c", cpp: "cpp", cxx: "cpp", cc: "cpp", hpp: "cpp", hxx: "cpp",
    py: "python", pyw: "python", pyi: "python",
    java: "java", kt: "kotlin", kts: "kotlin", scala: "scala", groovy: "groovy", gradle: "groovy",
    sh: "bash", bash: "bash", zsh: "bash", fish: "shell",
    ps1: "powershell", psm1: "powershell", psd1: "powershell",
    js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
    html: "html", htm: "html", css: "css", scss: "scss", sass: "scss", less: "less",
    vue: "xml", svelte: "xml",
    json: "json", json5: "json", jsonc: "json", yaml: "yaml", yml: "yaml", toml: "ini", xml: "xml", sql: "sql",
    ini: "ini", cfg: "ini", conf: "ini", env: "ini", tf: "ini", hcl: "ini", nix: "nix", cmake: "cmake",
    rs: "rust", go: "go", zig: "plaintext", v: "plaintext", nim: "nim", d: "d", asm: "x86asm", s: "x86asm",
    rb: "ruby", php: "php", pl: "perl", pm: "perl", lua: "lua", tcl: "tcl",
    cs: "csharp", vb: "vbnet", fs: "fsharp", fsx: "fsharp", swift: "swift",
    r: "r", rmd: "r", jl: "julia", m: "matlab", ml: "ocaml", mli: "ocaml",
    hs: "haskell", lhs: "haskell", ex: "elixir", exs: "elixir", erl: "erlang", hrl: "erlang",
    clj: "clojure", cljs: "clojure", cljc: "clojure", edn: "clojure", elm: "elm",
    dart: "dart", graphql: "graphql", gql: "graphql", proto: "protobuf",
    diff: "diff", patch: "diff", md: "markdown", markdown: "markdown",
    rst: "plaintext", adoc: "plaintext", txt: "plaintext", log: "plaintext",
};

function getLang(fileName: string): string {
    const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
    return EXT_TO_LANG[ext] ?? "";
}

interface CodeRowProps {
    lines: string[];
    lang: string;
    rowHeight: number;
    gutterWidth: number;
    contentWidth: number;
    /** Per-line highlight cache, keyed by line index. Reset when content/lang change. */
    cache: Map<number, string>;
}

// Top-level component so react-window never remounts rows on parent re-render.
// Each line is highlighted lazily on first paint and memoised in `cache`, so
// scrolling back over a line never re-tokenises it.
function CodeRow({
    index,
    style,
    lines,
    lang,
    rowHeight,
    gutterWidth,
    contentWidth,
    cache,
}: RowComponentProps<CodeRowProps>) {
    const line = lines[index] ?? "";

    let html = cache.get(index);
    if (html === undefined) {
        html =
            lang && hljs.getLanguage(lang)
                ? hljs.highlight(line, { language: lang, ignoreIllegals: true }).value
                : escapeHtml(line);
        cache.set(index, html);
    }

    return (
        <div
            style={{ ...style, width: contentWidth, minWidth: "100%", display: "flex" }}
        >
            <span
                className="sticky left-0 z-10 shrink-0 select-none bg-background px-3 text-right text-muted-foreground tabular-nums"
                style={{ width: gutterWidth, lineHeight: `${rowHeight}px` }}
            >
                {index + 1}
            </span>
            <code
                className="block pr-4"
                style={{ whiteSpace: "pre", lineHeight: `${rowHeight}px` }}
                // hljs output is already HTML-escaped; plain lines go through escapeHtml.
                // Zero-width space keeps empty lines from collapsing.
                dangerouslySetInnerHTML={{ __html: html || "​" }}
            />
        </div>
    );
}

export function CodeViewer({ materialId, fileKey, fileName }: CodeViewerProps) {
    const scrollRef = useRef<HTMLDivElement>(null);

    const { content, loading, error } = useMaterialFile({
        materialId,
        fileKey,
        mode: "text",
    });

    const { zoom, zoomIn, zoomOut, resetZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: scrollRef,
        handleKeyboard: true,
    });

    const lang = useMemo(() => getLang(fileName), [fileName]);
    const lines = useMemo(() => content.split("\n"), [content]);

    // Highlight results are independent of zoom, so the cache only needs to be
    // recreated when the source text or the detected language changes.
    const cache = useMemo<Map<number, string>>(() => new Map(), [content, lang]);

    // Derive sizing from the zoom level. A fixed row height lets react-window
    // place every row without measuring it.
    const fontSize = (BASE_FONT_PX * zoom) / 100;
    const rowHeight = Math.round(fontSize * LINE_HEIGHT_RATIO);
    const charWidth = fontSize * CHAR_WIDTH_RATIO;

    // Gutter is wide enough for the largest line number; content width spans the
    // longest line so the list scrolls horizontally instead of wrapping.
    const gutterWidth = Math.ceil(String(lines.length).length * charWidth) + 24;
    const maxLineLen = useMemo(
        () => lines.reduce((max, l) => Math.max(max, l.length), 0),
        [lines],
    );
    const contentWidth = gutterWidth + Math.ceil(maxLineLen * charWidth) + 16;

    const rowProps = useMemo<CodeRowProps>(
        () => ({ lines, lang, rowHeight, gutterWidth, contentWidth, cache }),
        [lines, lang, rowHeight, gutterWidth, contentWidth, cache],
    );

    return (
        <ViewerShell
            scrollRef={scrollRef}
            loading={loading}
            error={error}
            toolbarLeft={
                lang && (
                    <span className="text-xs font-medium uppercase text-muted-foreground px-1.5 py-0.5 bg-muted rounded truncate">
                        {lang}
                    </span>
                )
            }
            toolbarRight={
                <ZoomControls
                    zoom={zoom}
                    onZoomIn={zoomIn}
                    onZoomOut={zoomOut}
                    onReset={resetZoom}
                    min={MIN_ZOOM}
                    max={MAX_ZOOM}
                    disabled={loading}
                />
            }
        >
            {!loading && !error && (
                <List
                    rowCount={lines.length}
                    rowHeight={rowHeight}
                    rowComponent={CodeRow}
                    rowProps={rowProps}
                    overscanCount={OVERSCAN_ROWS}
                    className="h-full w-full bg-background font-mono"
                    style={{ fontSize }}
                />
            )}
        </ViewerShell>
    );
}
