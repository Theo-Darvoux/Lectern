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
