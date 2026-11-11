import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { subscribeToSSE, type SSEChannel } from "@/lib/sse-client";
import { invalidateMaterialFileUrl } from "@/lib/api-client";
import { invalidateBrowseEntity } from "@/lib/browse-prefetch";
import { resolvePendingContributionEvent } from "@/lib/pending-contributions";

interface BrowseData {
    type: "directory_listing" | "material";
    directory?: Record<string, unknown> | null;
    material?: Record<string, unknown> | null;
    breadcrumbs?: { id: string; name: string; slug: string }[];
}

// The logical topic changes only with the entity.
function browseChannel(data: BrowseData | null): SSEChannel | null {
    if (data?.type === "directory_listing") {
        return `directory:${data.directory ? String(data.directory.id) : "root"}`;
    }
    if (data?.type === "material" && data.material) {
        return `material:${String(data.material.id)}`;
    }
    return null;
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
 * Mutable values (path, router, breadcrumbs) are held in refs so listeners
 * always see the latest version without triggering reconnects. Invalidating a
 * path on screen refetches it.
 */
export function useBrowseSSE(data: BrowseData | null, path: string): void {
    const router = useRouter();

    const channel = browseChannel(data);

    // Mutable refs — updated after each render so listeners always see fresh values.
    const pathRef = useRef(path);
    const routerRef = useRef(router);
    const breadcrumbSlugsRef = useRef<string[]>([]);

    useEffect(() => {
        pathRef.current = path;
        routerRef.current = router;
        breadcrumbSlugsRef.current = data?.breadcrumbs?.map((b) => b.slug) ?? [];
    });

    useEffect(() => {
        if (!channel) return;
        const [kind, id] = channel.split(":");
        const refresh = () => invalidateBrowseEntity(channel, pathRef.current);
        const redirectTo = (parentPath: string) => {
            refresh();
            routerRef.current.replace(parentPath);
        };
        const browsePath = (slugs: string[]) =>
            slugs.length > 0 ? `/browse/${slugs.join("/")}` : "/browse";
        const listeners: Record<string, (event: MessageEvent) => void> = {};
        let onResync = refresh;

        if (kind === "directory") {
            if (id !== "root") {
                listeners["directory_deleted"] = () => {
                    redirectTo(browsePath(breadcrumbSlugsRef.current.slice(0, -1)));
                };
                listeners["directory_icon_updated"] = refresh;
                listeners["directory_color_updated"] = refresh;
            }
            listeners["child_added"] = refresh;
            listeners["child_updated"] = refresh;
            listeners["child_removed"] = refresh;
            listeners["pr_closed"] = (event) => {
                resolvePendingContributionEvent(event);
                refresh();
            };
        } else {
            // A new version may have been applied (e.g. a reviewed/merged edit).
            // Drop the cached file URL so the viewer re-fetches the updated content.
            const refreshMaterial = () => {
                invalidateMaterialFileUrl(id);
                refresh();
            };
            listeners["material_deleted"] = () => {
                redirectTo(browsePath(breadcrumbSlugsRef.current));
            };
            listeners["material_updated"] = refreshMaterial;
            onResync = refreshMaterial;
        }

        const connection = subscribeToSSE({ channel, listeners, onResync });
        return () => connection.close();
    }, [channel]); // update the master topic when the entity changes
}
