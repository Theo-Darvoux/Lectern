import { createSHA256 } from "hash-wasm";

/**
 * Compute SHA-256 of a File using WASM for maximum performance and memory efficiency.
 *
 * Processes the file in chunks to avoid materialising the entire file
 * in an ArrayBuffer, preventing OOM crashes on large files.
 *
 * @param file The file to hash.
 * @param onProgress Optional callback for progress updates (0-100).
 * @returns Hex-encoded lowercase SHA-256 digest.
 */
export async function sha256File(
    file: File,
    onProgress?: (pct: number) => void,
    signal?: AbortSignal
): Promise<string> {
    const hasher = await createSHA256();
    hasher.init();

    const CHUNK_SIZE = 4 * 1024 * 1024; // 4 MiB
    let offset = 0;

    while (offset < file.size) {
        if (signal?.aborted) {
            throw new Error("Hashing cancelled");
        }

        const slice = file.slice(offset, offset + CHUNK_SIZE);
        const buffer = await slice.arrayBuffer();
        hasher.update(new Uint8Array(buffer));
        
        offset += CHUNK_SIZE;
        if (onProgress) {
            const pct = Math.min(100, Math.round((offset / file.size) * 100));
            onProgress(pct);
        }
    }

    return hasher.digest();
}

