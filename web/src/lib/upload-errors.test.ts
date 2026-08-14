import { describe, expect, it } from "vitest";

import {
    isMalwareUploadErrorMessage,
    malwareDetectionReason,
    withMalwareErrorPrefix,
} from "./upload-errors";

describe("upload malware errors", () => {
    it("preserves an explicit scanner reason while hiding the internal marker", () => {
        const message =
            'ERR_MALWARE_DETECTED: File hash matched known malware signature "Win32.Test.Malware".';

        expect(isMalwareUploadErrorMessage(message)).toBe(true);
        expect(malwareDetectionReason(message)).toBe(
            'File hash matched known malware signature "Win32.Test.Malware".',
        );
    });

    it("normalizes legacy malicious status details for upload-state classification", () => {
        expect(withMalwareErrorPrefix("PDF contains embedded executable content")).toBe(
            "ERR_MALWARE_DETECTED: PDF contains embedded executable content",
        );
    });
});
