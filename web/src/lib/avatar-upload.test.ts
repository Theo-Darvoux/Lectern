import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiRequest } from "./api-client";
import { waitForUploadCompletion } from "./upload-client";
import { uploadAvatarAndAdopt } from "./avatar-upload";

vi.mock("./api-client", () => ({
    apiFetch: vi.fn(),
    apiRequest: vi.fn(),
}));

vi.mock("./upload-client", () => ({
    waitForUploadCompletion: vi.fn(),
}));

describe("uploadAvatarAndAdopt", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("waits for CLEAN processing before adopting the upload id", async () => {
        const upload = {
            upload_id: "11111111-1111-4111-8111-111111111111",
            file_key: "quarantine/user/avatar.png",
        };
        vi.mocked(apiRequest).mockResolvedValue({
            json: async () => upload,
        } as any);

        let resolveClean!: (value: {
            file_key: string;
            size: number;
            original_size: number;
            mime_type: string;
        }) => void;
        const clean = new Promise<{
            file_key: string;
            size: number;
            original_size: number;
            mime_type: string;
        }>((resolve) => {
            resolveClean = resolve;
        });
        vi.mocked(waitForUploadCompletion).mockReturnValue(clean);
        vi.mocked(apiFetch).mockResolvedValue({ id: "user-id" });

        const onProcessing = vi.fn();
        const resultPromise = uploadAvatarAndAdopt<{ id: string }>(
            new File(["avatar"], "avatar.png", { type: "image/png" }),
            { onProcessing },
        );

        await vi.waitFor(() => {
            expect(waitForUploadCompletion).toHaveBeenCalledWith(upload.file_key);
        });
        expect(onProcessing).toHaveBeenCalledOnce();
        expect(apiFetch).not.toHaveBeenCalled();

        resolveClean({
            file_key: "cas/clean",
            size: 6,
            original_size: 6,
            mime_type: "image/png",
        });
        await resultPromise;

        expect(apiFetch).toHaveBeenCalledOnce();
        expect(apiFetch).toHaveBeenCalledWith("/users/me", {
            method: "PATCH",
            body: JSON.stringify({ avatar_upload_id: upload.upload_id }),
        });
    });

    it("does not adopt when background processing fails", async () => {
        vi.mocked(apiRequest).mockResolvedValue({
            json: async () => ({
                upload_id: "22222222-2222-4222-8222-222222222222",
                file_key: "quarantine/user/bad.png",
            }),
        } as any);
        vi.mocked(waitForUploadCompletion).mockRejectedValue(
            new Error("Malware detected"),
        );

        await expect(
            uploadAvatarAndAdopt(
                new File(["bad"], "bad.png", { type: "image/png" }),
            ),
        ).rejects.toThrow("Malware detected");

        expect(apiFetch).not.toHaveBeenCalled();
    });
});
