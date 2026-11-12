import { getFileExtension } from "./file-utils";

/**
 * Generic card identity for source code, markup, config and plain-text files.
 * These files get no rendered thumbnail: the card shows the language instead.
 */
export interface CodeLanguage {
    /** Proper name shown on the card, or null to show the file extension. */
    name: string | null;
    /** Short mark drawn as the card's watermark. */
    mark: string;
    /** Tailwind gradient classes for the card background. */
    gradient: string;
}

type LanguageSpec = readonly [extensions: readonly string[], name: string | null, mark: string, gradient: string];

// Gradients stay dark enough for white text in both themes.
const LANGUAGES: readonly LanguageSpec[] = [
    [["py", "pyw", "pyi"], "Python", "PY", "from-sky-700 to-amber-600"],
    [["ipynb"], "Jupyter", "NB", "from-orange-500 to-amber-700"],
    [["js", "mjs", "cjs", "jsx"], "JavaScript", "JS", "from-yellow-500 to-amber-700"],
    [["ts", "tsx"], "TypeScript", "TS", "from-blue-500 to-blue-800"],
    [["java"], "Java", "JAVA", "from-orange-600 to-red-800"],
    [["kt", "kts"], "Kotlin", "KT", "from-violet-500 to-fuchsia-700"],
    [["scala"], "Scala", "SCALA", "from-red-500 to-rose-800"],
    [["groovy", "gradle"], "Groovy", "GVY", "from-cyan-600 to-sky-800"],
    [["c", "h"], "C", "C", "from-slate-500 to-blue-800"],
    [["cpp", "cxx", "cc", "hpp", "hxx"], "C++", "C++", "from-blue-600 to-indigo-800"],
    [["cs"], "C#", "C#", "from-purple-600 to-violet-800"],
    [["rs"], "Rust", "RS", "from-orange-700 to-stone-800"],
    [["go"], "Go", "GO", "from-cyan-500 to-sky-700"],
    [["zig"], "Zig", "ZIG", "from-amber-500 to-orange-700"],
    [["rb"], "Ruby", "RB", "from-red-600 to-rose-800"],
    [["php"], "PHP", "PHP", "from-indigo-400 to-indigo-700"],
    [["pl", "pm"], "Perl", "PL", "from-sky-800 to-slate-800"],
    [["sh", "bash", "zsh", "fish"], "Shell", "SH", "from-emerald-600 to-slate-800"],
    [["ps1", "psm1", "psd1"], "PowerShell", "PS", "from-blue-700 to-slate-800"],
    [["lua"], "Lua", "LUA", "from-indigo-600 to-blue-900"],
    [["swift"], "Swift", "SWIFT", "from-orange-500 to-red-600"],
    [["dart"], "Dart", "DART", "from-sky-500 to-teal-700"],
    [["r", "rmd"], "R", "R", "from-blue-500 to-slate-700"],
    [["jl"], "Julia", "JL", "from-violet-500 to-purple-800"],
    [["m"], "MATLAB", "M", "from-orange-500 to-red-700"],
    [["ml", "mli"], "OCaml", "ML", "from-orange-500 to-amber-800"],
    [["hs", "lhs"], "Haskell", "HS", "from-purple-600 to-indigo-800"],
    [["ex", "exs"], "Elixir", "EX", "from-purple-500 to-violet-800"],
    [["erl", "hrl"], "Erlang", "ERL", "from-red-700 to-rose-900"],
    [["clj", "cljs", "cljc", "edn"], "Clojure", "CLJ", "from-green-600 to-blue-700"],
    [["elm"], "Elm", "ELM", "from-sky-500 to-cyan-700"],
    [["fs", "fsx"], "F#", "F#", "from-cyan-600 to-blue-800"],
    [["vb"], "Visual Basic", "VB", "from-blue-600 to-violet-700"],
    [["nim"], "Nim", "NIM", "from-yellow-600 to-amber-800"],
    [["d"], "D", "D", "from-red-600 to-red-800"],
    [["odin"], "Odin", "ODIN", "from-blue-500 to-indigo-700"],
    [["v"], null, "V", "from-slate-500 to-slate-700"],
    [["html", "htm"], "HTML", "HTML", "from-orange-500 to-red-700"],
    [["css"], "CSS", "CSS", "from-blue-500 to-indigo-700"],
    [["scss", "sass"], "Sass", "SASS", "from-pink-500 to-rose-700"],
    [["less"], "Less", "LESS", "from-indigo-500 to-blue-800"],
    [["vue"], "Vue", "VUE", "from-emerald-500 to-teal-700"],
    [["svelte"], "Svelte", "SV", "from-orange-500 to-red-600"],
    [["sql"], "SQL", "SQL", "from-amber-600 to-orange-800"],
    [["graphql", "gql"], "GraphQL", "GQL", "from-pink-500 to-fuchsia-700"],
    [["proto", "thrift", "capnp"], null, "IDL", "from-slate-500 to-indigo-800"],
    [["tex", "latex", "sty", "cls", "dtx", "ins"], "LaTeX", "TEX", "from-teal-600 to-emerald-800"],
    [["bib"], "BibTeX", "BIB", "from-teal-500 to-cyan-700"],
    [["md", "markdown"], "Markdown", "MD", "from-slate-600 to-slate-800"],
    [["rst"], "reStructuredText", "RST", "from-slate-600 to-slate-800"],
    [["adoc"], "AsciiDoc", "ADOC", "from-slate-600 to-slate-800"],
    [["json", "json5", "jsonc"], "JSON", "{ }", "from-zinc-600 to-zinc-800"],
    [["yaml", "yml"], "YAML", "YAML", "from-rose-600 to-red-800"],
    [["toml"], "TOML", "TOML", "from-stone-500 to-stone-700"],
    [["xml"], "XML", "XML", "from-orange-600 to-amber-800"],
    [["csv"], "CSV", "CSV", "from-emerald-500 to-green-700"],
    [["dockerfile"], "Dockerfile", "DOCKER", "from-sky-500 to-blue-700"],
    [["tf", "hcl"], "Terraform", "TF", "from-violet-600 to-purple-800"],
    [["nix"], "Nix", "NIX", "from-sky-600 to-indigo-700"],
    [["makefile", "cmake", "bazel", "bzl"], null, "BUILD", "from-slate-500 to-stone-700"],
    [["diff", "patch"], "Diff", "DIFF", "from-emerald-600 to-rose-700"],
];

const BY_EXTENSION = new Map<string, CodeLanguage>(
    LANGUAGES.flatMap(([extensions, name, mark, gradient]) =>
        extensions.map((ext) => [ext, { name, mark, gradient }] as const),
    ),
);

const PLAIN_TEXT: Omit<CodeLanguage, "mark"> = {
    name: null,
    gradient: "from-slate-700 to-slate-900",
};

/** MIME types of text formats outside `text/*` (JSON, scripts, TeX…). */
const TEXT_APPLICATION_MIMES = new Set([
    "application/json",
    "application/json5",
    "application/jsonc",
    "application/xml",
    "application/x-yaml",
    "application/toml",
    "application/x-toml",
    "application/x-tex",
    "application/javascript",
    "application/typescript",
    "application/x-python",
    "application/x-python-code",
    "application/x-sh",
    "application/x-bash",
    "application/x-powershell",
    "application/x-ruby",
    "application/x-php",
    "application/sql",
    "application/graphql",
    "application/x-nix",
    "application/protobuf",
    "application/csv",
]);

/**
 * Return the generic card identity for a text-like file, or null for files that
 * get a real thumbnail or another card (images, PDFs, Office, QCM…).
 */
export function getCodeLanguage(fileName: string | null | undefined, mimeType: string | null | undefined): CodeLanguage | null {
    const ext = fileName ? getFileExtension(fileName) : "";
    const known = BY_EXTENSION.get(ext);
    if (known) return known;
    const mime = (mimeType ?? "").split(";", 1)[0].trim().toLowerCase();
    if (!mime.startsWith("text/") && !TEXT_APPLICATION_MIMES.has(mime)) return null;
    return { ...PLAIN_TEXT, mark: ext ? ext.toUpperCase().slice(0, 5) : "TXT" };
}
