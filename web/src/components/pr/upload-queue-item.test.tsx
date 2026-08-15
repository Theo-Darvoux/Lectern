import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { QueueItem } from "@/lib/upload-queue";
import { UploadQueueItem } from "./upload-queue-item";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next-intl", () => ({
    useTranslations: () => (key: string, values?: Record<string, string>) =>
        values?.reason ? `${key}: ${values.reason}` : key,
}));

vi.mock("next/image", () => ({ default: () => <div /> }));
vi.mock("@/components/ui/input", () => ({ Input: () => <input /> }));
vi.mock("@/components/ui/progress", () => ({ Progress: () => <div /> }));
vi.mock("@/components/ui/button", () => ({
    Button: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
}));

describe("UploadQueueItem", () => {
    let host: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        host = document.createElement("div");
        document.body.appendChild(host);
        root = createRoot(host);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        host.remove();
    });

    it("shows the scanner's explicit reason when malware rejects an upload", async () => {
        const item: QueueItem = {
            clientId: "client-1",
            uploadId: "upload-1",
            fileName: "project.pdf",
            fileSize: 100,
            fileMimeType: "application/pdf",
            title: "Project",
            status: "virus",
            progress: 80,
            processingStatus: "",
            error:
                'ERR_MALWARE_DETECTED: File hash matched known malware signature "Win32.Test.Malware".',
            targetDirPath: "",
        };

        await act(async () => {
            root.render(
                <UploadQueueItem
                    f={item}
                    hasFileObject={true}
                    fileSizeStr="100 B"
                    hasTusHandle={false}
                    onUpdateTitle={() => undefined}
                    onReAttach={() => undefined}
                    onPause={() => undefined}
                    onResume={() => undefined}
                    onRetry={() => undefined}
                    onRemove={() => undefined}
                />,
            );
        });

        expect(host.textContent).toContain(
            'threatReason: File hash matched known malware signature "Win32.Test.Malware".',
        );
        expect(host.textContent).not.toContain("ERR_MALWARE_DETECTED");
    });
});
