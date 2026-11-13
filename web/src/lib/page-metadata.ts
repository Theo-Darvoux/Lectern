import type { Metadata } from "next";
import { getPageMeta, type PageMeta } from "@/lib/server-config";

/** Open Graph and Twitter card tags from the API's page metadata. */
export function socialMetadata(meta: PageMeta): Pick<Metadata, "openGraph" | "twitter"> {
    const image = {
        url: meta.image,
        width: meta.image_width ?? undefined,
        height: meta.image_height ?? undefined,
        alt: meta.og_title,
    };
    return {
        openGraph: {
            title: meta.og_title,
            description: meta.description,
            siteName: meta.site_name,
            type: meta.type,
            locale: meta.locale,
            url: meta.url,
            images: [image],
        },
        twitter: {
            card: "summary_large_image",
            title: meta.og_title,
            description: meta.description,
            images: [image],
            site: meta.twitter_site ?? undefined,
            creator: meta.twitter_creator ?? undefined,
        },
    };
}

function decodeSegment(segment: string): string {
    try {
        return decodeURIComponent(segment);
    } catch {
        return segment;
    }
}

/** Site path ("/browse/a/b/") of a route's static prefix plus its params. */
export function sitePath(prefix: string, segments: string[] = []): string {
    const parts = [prefix, ...segments.map(decodeSegment)].filter(Boolean);
    return `/${parts.join("/")}/`;
}

/**
 * Metadata of a resource page (folder, document, profile, QCM): its own title,
 * description and preview banner. Falls back to the root layout's site-level
 * metadata when the API is unreachable.
 */
export async function resourceMetadata(path: string): Promise<Metadata> {
    const meta = await getPageMeta(path);
    if (!meta) return {};
    return {
        title: { absolute: meta.title },
        description: meta.description,
        ...socialMetadata(meta),
    };
}
