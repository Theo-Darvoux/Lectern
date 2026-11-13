// Server-side reads of the runtime settings the API owns (branding, auth
// methods, page metadata). Only server components and generateMetadata may
// import this module: it calls the API over the internal network.
import { cache } from "react";
import { connection } from "next/server";
import type { components } from "@/lib/api-types";
import type { PublicConfig } from "@/lib/stores";

export type PageMeta = components["schemas"]["OgMetaResponse"];

const API_ORIGIN = (process.env.API_INTERNAL_URL || "http://api:8000").replace(/\/$/, "");
// A slow API must not hold the page: the client fetches the config again when
// the server could not.
const TIMEOUT_MS = 3_000;

async function getJson<T>(path: string): Promise<T | null> {
    // These settings change without a rebuild, so every page renders per
    // request instead of at build time.
    await connection();
    try {
        const res = await fetch(`${API_ORIGIN}/api${path}`, {
            cache: "no-store",
            signal: AbortSignal.timeout(TIMEOUT_MS),
        });
        if (!res.ok) {
            console.error(`GET /api${path} returned ${res.status}`);
            return null;
        }
        return (await res.json()) as T;
    } catch (error) {
        console.error(`GET /api${path} failed`, error);
        return null;
    }
}

/** The public config (`GET /api/auth/methods`), once per request. */
export const getPublicConfig = cache(() => getJson<PublicConfig>("/auth/methods"));

/** Head metadata of one site path (`GET /api/og/meta`), once per request and path. */
export const getPageMeta = cache((path: string) =>
    getJson<PageMeta>(`/og/meta?path=${encodeURIComponent(path)}`),
);
