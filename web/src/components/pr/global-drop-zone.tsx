"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { UploadCloud } from "lucide-react";
import { UploadDrawer } from "@/components/pr/upload-drawer";
import { usePathname } from "next/navigation";
import { apiFetch } from "@/lib/api-client";
import { collectDroppedItems, type DroppedItems } from "@/lib/drop-utils";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useDropZoneStore, type UploadTarget } from "@/lib/drop-zone-store";
export { useDropZoneStore } from "@/lib/drop-zone-store";
export type { UploadTarget } from "@/lib/drop-zone-store";

// ---------------------------------------------------------------------------
// Resolve the current browse context from the URL path
// ---------------------------------------------------------------------------

interface BrowseContext {
    directoryId: string;
    directoryName: string;
}

async function resolveBrowseContext(pathname: string, t: (key: string) => string): Promise<BrowseContext | null> {
    // Only works on /browse pages
    if (!pathname.startsWith("/browse")) return null;

    const slug = pathname.replace(/^\/browse\/?/, "") || "";
    try {
        const endpoint = slug ? `/browse/${slug}` : "/browse";
        const data = await apiFetch<{
            type: string;
            directory?: { id: string; name: string } | null;
            material?: { id: string; title: string; directory_id?: string } | null;
        }>(endpoint);

        if (data.type === "directory_listing" && data.directory) {
            return {
                directoryId: data.directory.id,
                directoryName: data.directory.name,
            };
        }
        // Root directory
        if (data.type === "directory_listing" && !data.directory) {
            return { directoryId: "", directoryName: t("root") };
        }
    } catch {
        // Can't resolve — fall back to root
    }
    return null;
}

// ---------------------------------------------------------------------------
// Global Drop Zone component — renders full-screen overlay on drag
// ---------------------------------------------------------------------------

export function GlobalDropZone() {
    const t = useTranslations("GlobalDropZone");
    const pathname = usePathname();
    const [isDragOver, setIsDragOver] = useState(false);
    const [localDrawerOpen, setLocalDrawerOpen] = useState(false);
    const [target, setTarget] = useState<UploadTarget | null>(null);
    const dragCounterRef = useRef(0);
    const [pendingItems, setPendingItems] = useState<DroppedItems>({ files: [], folders: [], inaccessible: [] });


    const { uploadTarget, browseContext, setDismissOverlay, clear } = useDropZoneStore();

    // Register overlay dismiss so UploadDrawer can clear it on drop
    const dismiss = useCallback(() => {
        dragCounterRef.current = 0;
        setIsDragOver(false);
    }, []);
    useEffect(() => {
        setDismissOverlay(dismiss);
        return () => setDismissOverlay(null);
    }, [dismiss, setDismissOverlay]);

    // Handle explicit upload requests from other components
    useEffect(() => {
        if (uploadTarget) {
            queueMicrotask(() => {
                setTarget(uploadTarget);
                setLocalDrawerOpen(true);
            });
        }
    }, [uploadTarget]);

    const handleDrawerClose = useCallback(
        (open: boolean) => {
            setLocalDrawerOpen(open);
            if (!open) {
                clear();
                setPendingItems({ files: [], folders: [], inaccessible: [] });
                // Reset drag state — prevents the overlay from re-appearing
                // when the drawer handled the drop internally (stopPropagation).
                dragCounterRef.current = 0;
                setIsDragOver(false);
            }
        },
        [clear],
    );

    // Resolve target and open drawer with files + folders
    const handleItemsDrop = useCallback(
        async (items: DroppedItems) => {
            setPendingItems(items);

            // Prefer the live browse context (tracks ghost dirs) over an API fetch
            if (browseContext) {
                setTarget(browseContext);
            } else {
                const ctx = await resolveBrowseContext(pathname, t);
                if (ctx) {
                    setTarget({
                        directoryId: ctx.directoryId,
                        directoryName: ctx.directoryName,
                    });
                } else {
                    setTarget({
                        directoryId: "",
                        directoryName: t("root"),
                    });
                }
            }
            setLocalDrawerOpen(true);
        },
        [browseContext, pathname, t],
    );

    // Listen for drag events on the document (show/hide overlay)
    useEffect(() => {
        const onDragEnter = (e: DragEvent) => {
            e.preventDefault();
            dragCounterRef.current++;

            // Only show overlay for file drags
            if (e.dataTransfer?.types.includes("Files")) {
                setIsDragOver(true);
            }
        };

        const onDragOver = (e: DragEvent) => {
            if (!e.dataTransfer?.types.includes("Files")) return;
            e.preventDefault();
            if (e.dataTransfer) {
                e.dataTransfer.dropEffect = "copy";
            }
        };

        const onDragLeave = (e: DragEvent) => {
            e.preventDefault();
            dragCounterRef.current--;

            // Reliable way to check if drag left the browser window entirely
            const leftWindow =
                !e.relatedTarget ||
                e.clientX <= 0 ||
                e.clientY <= 0 ||
                e.clientX >= window.innerWidth ||
                e.clientY >= window.innerHeight;

            if (dragCounterRef.current <= 0 || leftWindow) {
                dragCounterRef.current = 0;
                setIsDragOver(false);
            }
        };

        // Fallback: catch drops that escape the overlay (e.g. dropped outside it)
        const onDrop = (e: DragEvent) => {
            dragCounterRef.current = 0;
            setIsDragOver(false);
            e.preventDefault();
        };

        document.addEventListener("dragenter", onDragEnter);
        document.addEventListener("dragover", onDragOver);
        document.addEventListener("dragleave", onDragLeave);
        document.addEventListener("drop", onDrop);

        return () => {
            document.removeEventListener("dragenter", onDragEnter);
            document.removeEventListener("dragover", onDragOver);
            document.removeEventListener("dragleave", onDragLeave);
            document.removeEventListener("drop", onDrop);
        };
    }, []);

    // Drop handler for the overlay element itself
    const handleOverlayDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    }, []);

    const handleOverlayDrop = useCallback(
        (e: React.DragEvent) => {
            e.preventDefault();
            e.stopPropagation();
            dragCounterRef.current = 0;
            setIsDragOver(false);

            if (!e.dataTransfer?.items.length) return;
            collectDroppedItems(e.dataTransfer.items).then((dropped) => {
                if (dropped.inaccessible.length > 0) {
                    toast.warning(
                        t("foldersNotAccessible", { count: dropped.inaccessible.length }),
                    );
                }
                handleItemsDrop(dropped);
            });
        },
        [handleItemsDrop, t],
    );


    return (
        <>
            {/* Full-screen overlay when dragging files */}
            {isDragOver && (
                <div
                    className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm"
                    onDragOver={handleOverlayDragOver}
                    onDrop={handleOverlayDrop}
                >
                    <div className="pointer-events-none flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed border-primary bg-primary/5 px-16 py-12">
                        <UploadCloud className="h-16 w-16 text-primary animate-bounce" />
                        <div className="text-center">
                            <p className="text-lg font-semibold text-primary">
                                {t("dropToUpload")}
                            </p>
                            <p className="text-sm text-muted-foreground mt-1">
                                {t("stagedChangesHint")}
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Upload drawer rendered here with the resolved target */}
            {target && (
                <UploadDrawer
                    open={localDrawerOpen}
                    onOpenChange={handleDrawerClose}
                    directoryId={target.directoryId}
                    directoryName={target.directoryName}
                    parentMaterialId={target.parentMaterialId}
                    initialFiles={pendingItems.files}
                    initialFolderEntries={pendingItems.folders}
                />
            )}
        </>
    );
}
