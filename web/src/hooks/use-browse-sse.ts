import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { subscribeToSSE } from "@/lib/sse-client";
import { invalidateMaterialFileUrl } from "@/lib/api-client";
import { invalidateBrowseEntity } from "@/lib/browse-prefetch";
import { resolvePendingContributionEvent } from "@/lib/pending-contributions";

interface BrowseData {
    type: "directory_listing" | "material";
    directory?: Record<string, unknown> | null;
    material?: Record<string, unknown> | null;
    breadcrumbs?: { id: string; name: string; slug: string }[];
}

/**
 * Subscribes to the appropriate SSE topic for the current browse view.
 *
 * - material/attachment view → material:{id} logical channel
 * - directory listing        → directory:{id} logical channel
 *                              (directory_deleted, child_added, PR changes)
 *
 * The logical subscription is keyed on the entity ID, NOT on the `data` object
 * reference. This means background revalidations that return new data objects
 * for the same directory/material do not churn the master topic set.
 * Mutable values (path, fetchData, etc.) are held in refs so
 * listeners always call the latest version without triggering reconnects.
 */
export function useBrowseSSE(
    data: BrowseData | null,
    path: string,
    fetchData: (background: boolean) => void,
): void {
    const router = useRouter();

    // Derive a stable entity key. The logical topic changes only with the entity.
    let sseEntityKey: string | null = null;
    if (data?.type === "directory_listing") {
        const dirId = data.directory ? String(data.directory.id) : "root";
        sseEntityKey = `dir:${dirId}`;
    } else if (data?.type === "material" && data.material) {
        const matId = String(data.material.id);
        sseEntityKey = `mat:${matId}`;
    }

    // Mutable refs — updated after each render so listeners always see fresh values.
    const pathRef = useRef(path);
    const fetchDataRef = useRef(fetchData);
    const routerRef = useRef(router);
    const breadcrumbSlugsRef = useRef<string[]>([]);

    useEffect(() => {
        pathRef.current = path;
        fetchDataRef.current = fetchData;
        routerRef.current = router;
        breadcrumbSlugsRef.current = data?.breadcrumbs?.map((b) => b.slug) ?? [];
    });

    useEffect(() => {
        if (!sseEntityKey) return;
        const listeners: Record<string, (event: MessageEvent) => void> = {};

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
                    invalidateBrowseEntity(`directory:${dirId}`, pathRef.current);
                    routerRef.current.replace(parentPath);
                };
            }

            const refreshDir = () => {
                invalidateBrowseEntity(`directory:${dirId}`, pathRef.current);
                fetchDataRef.current(true);
            };

            listeners["child_added"] = refreshDir;
            listeners["child_updated"] = refreshDir;
            listeners["child_removed"] = refreshDir;
            listeners["pr_closed"] = (event) => {
                resolvePendingContributionEvent(event);
                refreshDir();
            };
        } else {
            // mat: key — material view
            const matId = sseEntityKey.slice(4);
            listeners["material_deleted"] = () => {
                const slugs = breadcrumbSlugsRef.current;
                const parentPath =
                    slugs.length > 0
                        ? `/browse/${slugs.join("/")}`
                        : "/browse";
                invalidateBrowseEntity(`material:${matId}`, pathRef.current);
                routerRef.current.replace(parentPath);
            };
            // A new version was applied (e.g. a reviewed/merged edit). Drop the
            // cached file URL so the viewer re-fetches the updated content.
            listeners["material_updated"] = () => {
                invalidateMaterialFileUrl(matId);
                invalidateBrowseEntity(`material:${matId}`, pathRef.current);
                fetchDataRef.current(true);
            };
        }

        if (Object.keys(listeners).length === 0) return;

        const connection = subscribeToSSE({
            channel: sseEntityKey.startsWith("dir:")
                ? `directory:${sseEntityKey.slice(4)}`
                : `material:${sseEntityKey.slice(4)}`,
            listeners,
            onResync: () => {
                if (sseEntityKey.startsWith("mat:")) {
                    invalidateMaterialFileUrl(sseEntityKey.slice(4));
                }
                const entityTag = sseEntityKey.startsWith("dir:")
                    ? `directory:${sseEntityKey.slice(4)}`
                    : `material:${sseEntityKey.slice(4)}`;
                invalidateBrowseEntity(entityTag, pathRef.current);
                fetchDataRef.current(true);
            },
            startupDelay: 50,
        });

        return () => connection.close();
    }, [sseEntityKey]); // update the master topic when the entity changes
}
