import { apiFetch } from "@/lib/api-client";
import { MIME_QCM } from "@/lib/file-utils";
import type { QCMFile } from "@/lib/qcm-types";
import { validateQCMFile } from "@/lib/qcm-utils";

function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

/** Store a QCM on the server and return the file fields of its operation. */
export async function stageQcmFile(qcm: QCMFile, title: string) {
  const staged = await apiFetch<{ file_key: string; sha256: string; file_size: number }>(
    "/qcm/stage",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: qcm }),
    },
  );
  return {
    file_key: staged.file_key,
    file_name: `${slugify(title) || "qcm"}.qcm`,
    file_size: staged.file_size,
    file_mime_type: MIME_QCM,
    content_sha256: staged.sha256,
  };
}

/** Read back a QCM saved to the draft. */
export async function fetchStagedQcm(fileKey: string): Promise<QCMFile> {
  const data = await apiFetch<unknown>(`/qcm/staged?file_key=${encodeURIComponent(fileKey)}`);
  if (!validateQCMFile(data)) throw new Error("Invalid QCM file format");
  return data as QCMFile;
}
