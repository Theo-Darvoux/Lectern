import { apiFetch, apiRequest } from "@/lib/api-client";
import { waitForUploadCompletion } from "@/lib/upload-client";

interface PendingAvatarUpload {
    upload_id: string;
    file_key: string;
}

interface AvatarUploadOptions {
    onProcessing?: () => void;
}

/**
 * Upload an avatar through the asynchronous security pipeline and adopt it only
 * after the server reports the upload CLEAN. The backend independently repeats
 * all ownership/status checks when avatar_upload_id is adopted.
 */
export async function uploadAvatarAndAdopt<T>(
    file: File,
    options: AvatarUploadOptions = {},
): Promise<T> {
    const formData = new FormData();
    formData.append("file", file);

    const response = await apiRequest("/upload", {
        method: "POST",
        body: formData,
    });
    const upload = await response.json() as PendingAvatarUpload;

    if (!upload.upload_id || !upload.file_key) {
        throw new Error("Upload response is missing its upload identifier");
    }

    options.onProcessing?.();

    // POST /upload is asynchronous (202). Do not attempt adoption until the
    // existing SSE/poll pipeline has observed an authoritative CLEAN terminal
    // result. Malicious/failed/timeout states reject here and never PATCH.
    await waitForUploadCompletion(upload.file_key);

    return apiFetch<T>("/users/me", {
        method: "PATCH",
        body: JSON.stringify({ avatar_upload_id: upload.upload_id }),
    });
}
