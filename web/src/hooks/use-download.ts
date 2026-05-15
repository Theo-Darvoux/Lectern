"use client";

import { useState } from "react";
import { apiFetch, apiFetchBlob } from "@/lib/api-client";
import { toast } from "sonner";

function triggerBlobDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

export function useDownload() {
    const [isDownloading, setIsDownloading] = useState(false);

    const downloadMaterial = async (materialId: string, versionNumber?: number) => {
        setIsDownloading(true);
        try {
            const endpoint = versionNumber
                ? `/materials/${materialId}/versions/${versionNumber}/download-url`
                : `/materials/${materialId}/download-url`;

            const { url, filename } = await apiFetch<{ url: string; filename?: string }>(endpoint);

            const link = document.createElement("a");
            link.href = url;
            link.setAttribute("download", filename || "");
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        } catch (error) {
            console.error("Download failed:", error);
            toast.error("Failed to start download. Please try again.");
        } finally {
            setIsDownloading(false);
        }
    };

    const downloadQcmAsXml = async (materialId: string) => {
        setIsDownloading(true);
        try {
            const blob = await apiFetchBlob(`/qcm/export-moodle/${materialId}`);
            triggerBlobDownload(blob, `qcm-${materialId}.xml`);
        } catch (error) {
            console.error("QCM export failed:", error);
            toast.error("Failed to export QCM. Please try again.");
        } finally {
            setIsDownloading(false);
        }
    };

    return { downloadMaterial, downloadQcmAsXml, isDownloading };
}
