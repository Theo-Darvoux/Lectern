import { ACCEPTED_FILE_TYPES, MIME_TO_EXT, sniffFileType } from "@/lib/file-utils";
import type { ScannedFile } from "@/lib/drop-utils";

export interface UploadPreflightResult {
    files: ScannedFile[];
    unreadable: string[];
}

/** Recover missing file types independently so one local I/O error stays local. */
export async function prepareScannedFiles(
    scanned: ScannedFile[],
    allowedExtensions?: readonly string[],
): Promise<UploadPreflightResult> {
    const accepted = allowedExtensions ?? ACCEPTED_FILE_TYPES.split(",");
    const results = await Promise.all(scanned.map(async (item): Promise<ScannedFile | null> => {
        try {
            let file = item.file;
            const ext = `.${file.name.split(".").pop()?.toLowerCase()}`;
            if (accepted.includes(ext)) return item;

            let mime = file.type;
            if (!mime || mime === "application/octet-stream") {
                const sniffed = await sniffFileType(file);
                if (sniffed) mime = sniffed.mime;
            }

            const canonicalExt = MIME_TO_EXT[mime];
            const name = canonicalExt ? `${file.name}.${canonicalExt}` : file.name;
            if (mime === file.type && name === file.name) return item;
            file = new File([file], name, { type: mime, lastModified: file.lastModified });
            return { ...item, file };
        } catch {
            return null;
        }
    }));

    return {
        files: results.filter((item): item is ScannedFile => item !== null),
        unreadable: scanned
            .filter((_item, index) => results[index] === null)
            .map((item) => item.relativePath),
    };
}
