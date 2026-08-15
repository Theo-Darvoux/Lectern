"use client";

import { useState } from "react";
import { apiFetch, apiFetchBlob, fetchMaterialFile } from "@/lib/api-client";
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

            const isPdf = (filename ?? "").toLowerCase().endsWith(".pdf");
            if (isPdf) {
                // Open PDFs in the native browser PDF viewer instead of downloading
                window.open(url, "_blank", "noopener,noreferrer");
            } else {
                const link = document.createElement("a");
                link.href = url;
                link.setAttribute("download", filename || "");
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            }
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
            console.error("QCM XML export failed:", error);
            toast.error("Failed to export QCM. Please try again.");
        } finally {
            setIsDownloading(false);
        }
    };

    const downloadQcmAsPdf = async (materialId: string, title?: string) => {
        setIsDownloading(true);
        try {
            const response = await fetchMaterialFile(materialId);
            const qcm = await response.json();
            // @react-pdf/renderer + KaTeX are large and QCM-specific. Loading
            // them statically makes every material viewer (including images)
            // compile this stack in development. Keep it off the common path.
            const { generateQcmPdfBlob } = await import("@/lib/qcm-pdf-renderer");
            const blob = await generateQcmPdfBlob(qcm, title ?? "QCM");
            const blobUrl = URL.createObjectURL(blob);
            window.open(blobUrl, "_blank", "noopener,noreferrer");
            // Revoke after a delay so the new tab has time to load the blob
            setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
        } catch (error) {
            console.error("QCM PDF export failed:", error);
            toast.error("Failed to export QCM as PDF. Please try again.");
        } finally {
            setIsDownloading(false);
        }
    };

    return { downloadMaterial, downloadQcmAsXml, downloadQcmAsPdf, isDownloading };
}
