import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { API_BASE, ApiError, apiFetch } from "@/lib/api-client";

/** Download a directory as a ZIP, reporting refusals as toasts. */
export function useDirectoryZipDownload() {
    const t = useTranslations("Browse");
    const [isDownloading, setIsDownloading] = useState(false);

    const download = async (directoryId: string) => {
        if (isDownloading) return;
        setIsDownloading(true);
        try {
            // The ZIP streams through a plain link, so a refusal would replace the
            // page with a JSON error. Check first to report it here instead.
            await apiFetch<void>(`/directories/${directoryId}/download/check`);
            const link = document.createElement("a");
            link.href = `${API_BASE}/directories/${directoryId}/download`;
            document.body.appendChild(link);
            link.click();
            link.remove();
        } catch (err) {
            const empty = err instanceof ApiError && err.status === 400;
            toast.error(t(empty ? "downloadZipEmpty" : "downloadZipFailed"));
        } finally {
            setIsDownloading(false);
        }
    };

    return { download, isDownloading };
}
