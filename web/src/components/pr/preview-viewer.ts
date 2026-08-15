import { getViewerType } from "@/lib/file-utils";

const DIRECT_PREVIEW_TYPES = new Set([
    "pdf", "image", "svg", "video", "audio", "markdown", "code", "csv", "notebook", "qcm",
]);

/** Resolve only viewer types mounted by the lightweight contribution previews. */
export function getContributionPreviewViewerType(mimeType: string, fileName: string): string {
    const viewerType = getViewerType(mimeType, fileName);
    return DIRECT_PREVIEW_TYPES.has(viewerType) ? viewerType : "generic";
}
