import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Normalize App Router pathnames so route guards behave the same with
 * `trailingSlash: true` for both direct loads and client-side navigation.
 */
export function normalizePathname(pathname: string): string {
  if (pathname.length <= 1) return pathname;
  return pathname.replace(/\/+$/, "");
}

/**
 * Determines if a target (material or directory) is restricted based on its ID 
 * (starts with '$' for drafts) or if it's currently being previewed in a Pull Request.
 */
export function isRestrictedTarget(id: string | null | undefined, previewPr?: string | null): boolean {
  return (id?.startsWith("$") ?? false) || !!previewPr;
}

/**
 * Strip characters not allowed in name/title fields.
 * Keeps printable ASCII plus precomposed Latin accented characters
 * (U+00C0–U+017F) used in French and other Western European languages.
 * Blocks Zalgo text, emoji, Arabic, CJK, and other non-Latin scripts.
 */
export function sanitizeNameInput(v: string): string {
  return v.replace(/[^\x20-\x7e\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f]/g, "");
}

/**
 * Validate a post-login `next` redirect target. Returns the path only if it is a
 * safe root-relative path; rejects absolute/protocol-relative URLs (open-redirect)
 * and auth pages (redirect loop). Returns null otherwise.
 */
export function sanitizeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/")) return null;
  if (next.startsWith("//") || next.startsWith("/\\")) return null;
  if (next === "/login" || next.startsWith("/login/") || next.startsWith("/login?")) return null;
  return next;
}

/**
 * Locale-aware natural-order string comparator. Numeric runs are compared by
 * value, so "Chapitre 2" sorts before "Chapitre 10" (instead of lexicographic
 * "10" < "2"). `sensitivity: "base"` keeps accents/case from splitting groups.
 */
const naturalCollator = new Intl.Collator("fr", {
  numeric: true,
  sensitivity: "base",
});

/** Compare two strings in natural order (see {@link naturalCollator}). */
export function compareNatural(a: string, b: string): number {
  return naturalCollator.compare(a, b);
}

export function formatBytes(bytes?: number, decimals: number = 2) {
  if (!bytes) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + " " + sizes[i];
}
