import { ApiError } from "@/lib/api-client";

const MALWARE_ERROR_PREFIX = "ERR_MALWARE_DETECTED:";

export function isMalwareUploadErrorMessage(message: string): boolean {
    return message.trimStart().startsWith(MALWARE_ERROR_PREFIX);
}

export function malwareDetectionReason(message: string | undefined): string | null {
    if (!message) return null;
    const reason = message.trim().replace(/^ERR_MALWARE_DETECTED:\s*/i, "").trim();
    return reason || null;
}

export function withMalwareErrorPrefix(message: string): string {
    return isMalwareUploadErrorMessage(message)
        ? message
        : `${MALWARE_ERROR_PREFIX} ${message}`;
}

const TRANSLATED_ERROR_CODES = new Set([
    "ERR_FILE_TOO_LARGE",
    "ERR_FILE_UNREADABLE",
    "ERR_FILENAME_TOO_LONG",
    "ERR_TYPE_NOT_ALLOWED",
    "ERR_MIME_MISMATCH",
    "ERR_SVG_UNSAFE",
    "ERR_SCAN_UNAVAILABLE",
    "ERR_QUOTA_EXCEEDED",
    "ERR_STORAGE_FULL",
    "ERR_PROCESSING_FAILED",
]);

/** Turn any upload failure into a message for the user's language. */
export function localizeUploadError(err: unknown, t: (key: string) => string): string {
    if (err instanceof ApiError) {
        if (isMalwareUploadErrorMessage(err.message)) return err.message;
        if (err.error_code && TRANSLATED_ERROR_CODES.has(err.error_code)) {
            return t(`errors.${err.error_code}`);
        }
        if (err.status === 429) return t("errors.rateLimited");
        if (err.status === 401 || err.status === 403) return t("errors.notAllowed");
        if (err.status >= 500) return t("errors.server");
        return err.message;
    }
    const isConnectionFailure =
        err instanceof TypeError ||
        err instanceof DOMException ||
        (err instanceof Error && err.name === "TransferInterruptedError");
    return t(isConnectionFailure ? "errors.network" : "errorProcessing");
}
