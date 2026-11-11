import { queryOptions } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";

/** Presigned preview URL of an uploaded file that is not published yet. */
export function uploadPreviewQuery(fileKey: string) {
    return queryOptions({
        queryKey: ["upload-preview", fileKey],
        queryFn: async ({ signal }) =>
            (
                await apiFetch<{ url: string }>(
                    `/upload/preview?file_key=${encodeURIComponent(fileKey)}`,
                    { signal },
                )
            ).url || null,
        // Presigned URLs expire, so do not reuse one for long.
        staleTime: 5 * 60_000,
    });
}
