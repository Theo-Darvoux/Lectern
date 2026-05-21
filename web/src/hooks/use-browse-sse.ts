import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { createSSEConnection } from "@/lib/sse-client";

interface BrowseData {
    type: "directory_listing" | "material" | "attachment_listing";
    directory?: Record<string, unknown> | null;
    material?: Record<string, unknown> | null;
    parent_material?: Record<string, unknown> | null;
    breadcrumbs?: { id: string; name: string; slug: string }[];
}

/**
 * Subscribes to the appropriate SSE topic for the current browse view.
 *
 * - material view      → /materials/{id}/sse  (material_deleted)
 * - attachment listing → /materials/{id}/sse  (material_deleted)
 * - directory listing  → /directories/{id}/sse
 *                        (directory_deleted, child_added, pr_opened, pr_closed)
 */
export function useBrowseSSE(
    data: BrowseData | null,
    path: string,
    browseCache: { delete: (key: string) => void },
    fetchData: (background: boolean) => void,
): void {
    const router = useRouter();

    useEffect(() => {
        if (!data) return;

        let sseUrl: string | null = null;
        const listeners: Record<string, () => void> = {};
        const breadcrumbSlugs = data.breadcrumbs?.map((b) => b.slug) ?? [];

        if (data.type === "material" && data.material) {
            sseUrl = `/materials/${data.material.id as string}/sse`;
            const parentPath =
                breadcrumbSlugs.length > 0
                    ? `/browse/${breadcrumbSlugs.join("/")}`
                    : "/browse";
            listeners["material_deleted"] = () => {
                browseCache.delete(path);
                router.replace(parentPath);
            };
        } else if (data.type === "directory_listing") {
            const dirId = data.directory ? String(data.directory.id) : "root";
            sseUrl = `/directories/${dirId}/sse`;

            if (data.directory) {
                const parentSlugs = breadcrumbSlugs.slice(0, -1);
                const parentPath =
                    parentSlugs.length > 0
                        ? `/browse/${parentSlugs.join("/")}`
                        : "/browse";
                listeners["directory_deleted"] = () => {
                    browseCache.delete(path);
                    router.replace(parentPath);
                };
            }

            listeners["child_added"] = () => {
                browseCache.delete(path);
                fetchData(true);
            };

            listeners["pr_closed"] = () => {
                browseCache.delete(path);
                fetchData(true);
            };
        } else if (data.type === "attachment_listing" && data.parent_material) {
            sseUrl = `/materials/${(data.parent_material as Record<string, unknown>).id as string}/sse`;
            const parentPath =
                breadcrumbSlugs.length > 0
                    ? `/browse/${breadcrumbSlugs.join("/")}`
                    : "/browse";
            listeners["material_deleted"] = () => {
                browseCache.delete(path);
                router.replace(parentPath);
            };
        }

        if (!sseUrl || Object.keys(listeners).length === 0) return;

        const connection = createSSEConnection({
            url: sseUrl,
            listeners,
            startupDelay: 50,
        });

        return () => connection.close();
    }, [data, path, browseCache, fetchData, router]);
}
