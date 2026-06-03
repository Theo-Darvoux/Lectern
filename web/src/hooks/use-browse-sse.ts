import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { createSSEConnection } from "@/lib/sse-client";

interface BrowseData {
    type: "directory_listing" | "material";
    directory?: Record<string, unknown> | null;
    material?: Record<string, unknown> | null;
    breadcrumbs?: { id: string; name: string; slug: string }[];
}

/**
 * Subscribes to the appropriate SSE topic for the current browse view.
 *
 * - material view      → /materials/{id}/sse  (material_deleted)
 * - attachment listing → /materials/{id}/sse  (material_deleted)
 * - directory listing  → /directories/{id}/sse
 *                        (directory_deleted, child_added, pr_opened, pr_closed)
 *
 * The SSE connection is keyed on the entity ID, NOT on the `data` object
 * reference. This means background revalidations that return new data objects
 * for the same directory/material do NOT disconnect and reconnect the SSE.
 * Mutable values (path, browseCache, fetchData, etc.) are held in refs so
 * listeners always call the latest version without triggering reconnects.
 */
export function useBrowseSSE(
    data: BrowseData | null,
    path: string,
    browseCache: { delete: (key: string) => void },
    fetchData: (background: boolean) => void,
    triggerBrowseRefresh: () => void,
): void {
    const router = useRouter();

    // Derive a stable entity key.  The SSE only reconnects when this changes.
    let sseEntityKey: string | null = null;
    let sseUrl: string | null = null;
    if (data?.type === "directory_listing") {
        const dirId = data.directory ? String(data.directory.id) : "root";
        sseEntityKey = `dir:${dirId}`;
        sseUrl = `/directories/${dirId}/sse`;
    } else if (data?.type === "material" && data.material) {
        const matId = String(data.material.id);
        sseEntityKey = `mat:${matId}`;
        sseUrl = `/materials/${matId}/sse`;
    }

    // Mutable refs — updated after each render so listeners always see fresh values.
    const pathRef = useRef(path);
    const browseCacheRef = useRef(browseCache);
    const fetchDataRef = useRef(fetchData);
    const triggerRefreshRef = useRef(triggerBrowseRefresh);
    const routerRef = useRef(router);
    const breadcrumbSlugsRef = useRef<string[]>([]);

    useEffect(() => {
        pathRef.current = path;
        browseCacheRef.current = browseCache;
        fetchDataRef.current = fetchData;
        triggerRefreshRef.current = triggerBrowseRefresh;
        routerRef.current = router;
        breadcrumbSlugsRef.current = data?.breadcrumbs?.map((b) => b.slug) ?? [];
    });

    useEffect(() => {
        if (!sseEntityKey || !sseUrl) return;

        const capturedUrl = sseUrl;
        const listeners: Record<string, () => void> = {};

        if (sseEntityKey.startsWith("dir:")) {
            const dirId = sseEntityKey.slice(4);

            if (dirId !== "root") {
                listeners["directory_deleted"] = () => {
                    const slugs = breadcrumbSlugsRef.current;
                    const parentSlugs = slugs.slice(0, -1);
                    const parentPath =
                        parentSlugs.length > 0
                            ? `/browse/${parentSlugs.join("/")}`
                            : "/browse";
                    browseCacheRef.current.delete(pathRef.current);
                    triggerRefreshRef.current();
                    routerRef.current.replace(parentPath);
                };
            }

            const refreshDir = () => {
                browseCacheRef.current.delete(pathRef.current);
                fetchDataRef.current(true);
                triggerRefreshRef.current();
            };

            listeners["child_added"] = refreshDir;
            listeners["child_removed"] = refreshDir;
            listeners["pr_closed"] = refreshDir;
        } else {
            // mat: key — material view
            listeners["material_deleted"] = () => {
                const slugs = breadcrumbSlugsRef.current;
                const parentPath =
                    slugs.length > 0
                        ? `/browse/${slugs.join("/")}`
                        : "/browse";
                browseCacheRef.current.delete(pathRef.current);
                triggerRefreshRef.current();
                routerRef.current.replace(parentPath);
            };
        }

        if (Object.keys(listeners).length === 0) return;

        const connection = createSSEConnection({
            url: capturedUrl,
            listeners,
            startupDelay: 50,
        });

        return () => connection.close();
    }, [sseEntityKey, sseUrl]); // reconnect when entity changes
}
