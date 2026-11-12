import {
    siApachegroovy,
    siAsciidoctor,
    siBazel,
    siC,
    siClojure,
    siCmake,
    siCplusplus,
    siCss,
    siD,
    siDart,
    siDocker,
    siElixir,
    siElm,
    siErlang,
    siFsharp,
    siGit,
    siGnubash,
    siGo,
    siGradle,
    siGraphql,
    siHaskell,
    siHtml5,
    siJavascript,
    siJson,
    siJulia,
    siJupyter,
    siKotlin,
    siLatex,
    siLess,
    siLua,
    siMarkdown,
    siNim,
    siNixos,
    siOcaml,
    siOdin,
    siOpenjdk,
    siPerl,
    siPhp,
    siPython,
    siR,
    siRuby,
    siRust,
    siSass,
    siScala,
    siSvelte,
    siSwift,
    siTerraform,
    siToml,
    siTypescript,
    siVuedotjs,
    siXml,
    siYaml,
    siZig,
    type SimpleIcon,
} from "simple-icons";
import { getFileExtension } from "./file-utils";

/**
 * Generic card identity for source code, markup, config and plain-text files.
 * These files get no rendered thumbnail: the card shows the language instead.
 */
export interface CodeLanguage {
    /** Proper name shown on the card, or null to show the file extension. */
    name: string | null;
    /** Short mark drawn as the card's watermark when the language has no logo. */
    mark: string;
    /** Background color that stands for the language (CSS hex). */
    color: string;
    /** True when text and logo drawn on `color` must be dark to stay readable. */
    darkText: boolean;
    /** Official logo from Simple Icons (CC0), or null to draw `mark` instead. */
    icon: SimpleIcon | null;
}

type LanguageSpec = readonly [
    extensions: readonly string[],
    name: string | null,
    mark: string,
    color: string,
    icon: SimpleIcon | null,
];

// Colors are the language's brand color. Brands whose color is plain black get
// the color the community associates with them instead (Rust orange, Markdown blue…).
// Languages without a logo in Simple Icons fall back to their text mark.
const LANGUAGES: readonly LanguageSpec[] = [
    [["py", "pyw", "pyi"], "Python", "PY", "#3776AB", siPython],
    [["ipynb"], "Jupyter", "NB", "#F37626", siJupyter],
    [["js", "mjs", "cjs", "jsx"], "JavaScript", "JS", "#F7DF1E", siJavascript],
    [["ts", "tsx"], "TypeScript", "TS", "#3178C6", siTypescript],
    [["java"], "Java", "JAVA", "#E76F00", siOpenjdk],
    [["kt", "kts"], "Kotlin", "KT", "#7F52FF", siKotlin],
    [["scala"], "Scala", "SCALA", "#DC322F", siScala],
    [["groovy"], "Groovy", "GVY", "#4298B8", siApachegroovy],
    [["gradle"], "Gradle", "GRADLE", "#02303A", siGradle],
    [["c", "h"], "C", "C", "#A8B9CC", siC],
    [["cpp", "cxx", "cc", "hpp", "hxx"], "C++", "C++", "#00599C", siCplusplus],
    [["cs"], "C#", "C#", "#512BD4", null],
    [["rs"], "Rust", "RS", "#B7410E", siRust],
    [["go"], "Go", "GO", "#00ADD8", siGo],
    [["zig"], "Zig", "ZIG", "#F7A41D", siZig],
    [["rb"], "Ruby", "RB", "#CC342D", siRuby],
    [["php"], "PHP", "PHP", "#777BB4", siPhp],
    [["pl", "pm"], "Perl", "PL", "#0073A1", siPerl],
    [["sh", "bash", "zsh", "fish"], "Shell", "SH", "#4EAA25", siGnubash],
    [["ps1", "psm1", "psd1"], "PowerShell", "PS", "#012456", null],
    [["lua"], "Lua", "LUA", "#000080", siLua],
    [["swift"], "Swift", "SWIFT", "#F05138", siSwift],
    [["dart"], "Dart", "DART", "#0175C2", siDart],
    [["r", "rmd"], "R", "R", "#276DC3", siR],
    [["jl"], "Julia", "JL", "#9558B2", siJulia],
    [["m"], "MATLAB", "M", "#E16737", null],
    [["ml", "mli"], "OCaml", "ML", "#EC6813", siOcaml],
    [["hs", "lhs"], "Haskell", "HS", "#5D4F85", siHaskell],
    [["ex", "exs"], "Elixir", "EX", "#4B275F", siElixir],
    [["erl", "hrl"], "Erlang", "ERL", "#A90533", siErlang],
    [["clj", "cljs", "cljc", "edn"], "Clojure", "CLJ", "#5881D8", siClojure],
    [["elm"], "Elm", "ELM", "#1293D8", siElm],
    [["fs", "fsx"], "F#", "F#", "#378BBA", siFsharp],
    [["vb"], "Visual Basic", "VB", "#945DB7", null],
    [["nim"], "Nim", "NIM", "#FFE953", siNim],
    [["d"], "D", "D", "#B03931", siD],
    [["odin"], "Odin", "ODIN", "#3882D2", siOdin],
    // `.v` is both V and Verilog: no name, no logo.
    [["v"], null, "V", "#475569", null],
    [["html", "htm"], "HTML", "HTML", "#E34F26", siHtml5],
    [["css"], "CSS", "CSS", "#663399", siCss],
    [["scss", "sass"], "Sass", "SASS", "#CC6699", siSass],
    [["less"], "Less", "LESS", "#1D365D", siLess],
    [["vue"], "Vue", "VUE", "#4FC08D", siVuedotjs],
    [["svelte"], "Svelte", "SV", "#FF3E00", siSvelte],
    [["sql"], "SQL", "SQL", "#C9702A", null],
    [["graphql", "gql"], "GraphQL", "GQL", "#E10098", siGraphql],
    [["proto", "thrift", "capnp"], null, "IDL", "#4A5A75", null],
    [["tex", "latex", "sty", "cls", "dtx", "ins"], "LaTeX", "TEX", "#008080", siLatex],
    [["bib"], "BibTeX", "BIB", "#00616A", siLatex],
    [["md", "markdown"], "Markdown", "MD", "#083FA1", siMarkdown],
    [["rst"], "reStructuredText", "RST", "#3B4A5C", null],
    [["adoc"], "AsciiDoc", "ADOC", "#E40046", siAsciidoctor],
    [["json", "json5", "jsonc"], "JSON", "{ }", "#292929", siJson],
    [["yaml", "yml"], "YAML", "YAML", "#CB171E", siYaml],
    [["toml"], "TOML", "TOML", "#9C4121", siToml],
    [["xml"], "XML", "XML", "#005FAD", siXml],
    [["csv"], "CSV", "CSV", "#217346", null],
    [["dockerfile"], "Dockerfile", "DOCKER", "#2496ED", siDocker],
    [["tf", "hcl"], "Terraform", "TF", "#844FBA", siTerraform],
    [["nix"], "Nix", "NIX", "#5277C3", siNixos],
    [["cmake"], "CMake", "CMAKE", "#064F8C", siCmake],
    [["bazel", "bzl"], "Bazel", "BAZEL", "#43A047", siBazel],
    [["makefile"], "Makefile", "MAKE", "#57534E", null],
    [["diff", "patch"], "Diff", "DIFF", "#F05032", siGit],
];

/**
 * Whether dark text reads better than white on `hex`, by YIQ brightness. The
 * card adds a darkening overlay, so mid-tone brand colors keep white text.
 */
function needsDarkText(hex: string): boolean {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return (r * 299 + g * 587 + b * 114) / 1000 >= 150;
}

const BY_EXTENSION = new Map<string, CodeLanguage>(
    LANGUAGES.flatMap(([extensions, name, mark, color, icon]) => {
        const language: CodeLanguage = { name, mark, color, darkText: needsDarkText(color), icon };
        return extensions.map((ext) => [ext, language] as const);
    }),
);

const PLAIN_TEXT: Omit<CodeLanguage, "mark"> = {
    name: null,
    color: "#334155",
    darkText: false,
    icon: null,
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
