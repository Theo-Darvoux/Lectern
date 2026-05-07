/**
 * Utilities for handling drag and drop of files and folders using the FileSystem API.
 */

export interface ScannedFile {
    file: File;
    /** Relative path from the drop root including filename, e.g. "FolderA/sub/file.pdf" */
    relativePath: string;
}

export interface DroppedItems {
    /** Flat files dropped directly (not inside a folder at the top level). */
    files: ScannedFile[];
    /** Top-level folder entries — each will be zipped and uploaded via batch-zip. */
    folders: Array<{ entry: FileSystemDirectoryEntry; name: string }>;
}

async function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
    const all: FileSystemEntry[] = [];
    while (true) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) =>
            reader.readEntries(res, rej),
        );
        if (batch.length === 0) break;
        all.push(...batch);
    }
    return all;
}

const MAX_TRAVERSE_DEPTH = 20;

async function traverseEntry(
    entry: FileSystemEntry,
    pathPrefix: string,
    out: ScannedFile[],
    visited: Set<string>,
    depth: number,
): Promise<void> {
    if (depth > MAX_TRAVERSE_DEPTH) return; // guard against very deep trees

    // Skip hidden files and folders (starting with .)
    if (entry.name.startsWith(".")) return;

    if (entry.isFile) {
        const file = await new Promise<File>((res, rej) =>
            (entry as FileSystemFileEntry).file(res, rej),
        );
        out.push({ file, relativePath: pathPrefix + file.name });
    } else if (entry.isDirectory) {
        const dirEntry = entry as FileSystemDirectoryEntry;
        // Use the full path to detect symlink cycles
        const fullPath = dirEntry.fullPath;
        if (visited.has(fullPath)) return; // cycle detected — skip
        visited.add(fullPath);
        const children = await readAllEntries(dirEntry.createReader());
        for (const child of children) {
            await traverseEntry(child, pathPrefix + dirEntry.name + "/", out, visited, depth + 1);
        }
    }
}

/**
 * Traverse a folder entry recursively and return all contained ScannedFiles.
 * The relative path of each file starts with the folder's name.
 */
export async function traverseFolder(entry: FileSystemDirectoryEntry): Promise<ScannedFile[]> {
    const out: ScannedFile[] = [];
    const visited = new Set<string>();
    await traverseEntry(entry, "", out, visited, 0);
    return out;
}

/**
 * Collect dropped items, separating top-level flat files from top-level folders.
 * Folders are returned as FileSystemDirectoryEntry objects for deferred zip handling.
 */
export async function collectDroppedItems(items: DataTransferItemList): Promise<DroppedItems> {
    const files: ScannedFile[] = [];
    const folders: Array<{ entry: FileSystemDirectoryEntry; name: string }> = [];

    // Capture items/entries SYNCHRONOUSLY before any await
    const entries: Array<{ entry: FileSystemEntry | null; file: File | null }> = [];
    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind !== "file") continue;

        const entry = (item as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }).webkitGetAsEntry?.();
        if (!entry) {
            entries.push({ entry: null, file: item.getAsFile() });
        } else {
            entries.push({ entry, file: null });
        }
    }

    // Now we can await safely
    for (const { entry, file } of entries) {
        if (entry) {
            if (entry.name.startsWith(".")) continue;
            if (entry.isFile) {
                const f = await new Promise<File>((res, rej) =>
                    (entry as FileSystemFileEntry).file(res, rej),
                );
                files.push({ file: f, relativePath: f.name });
            } else if (entry.isDirectory) {
                folders.push({ entry: entry as FileSystemDirectoryEntry, name: entry.name });
            }
        } else if (file) {
            if (file.name.startsWith(".")) continue;
            files.push({ file, relativePath: file.name });
        }
    }

    return { files, folders };
}


/**
 * Derive all unique directory paths from a list of file paths (excluding root "").
 */
export function extractDirPaths(scanned: ScannedFile[]): string[] {
    const dirs = new Set<string>();
    for (const { relativePath } of scanned) {
        const parts = relativePath.split("/");
        // Parts: ["FolderA", "sub", "file.pdf"] → dirs: ["FolderA", "FolderA/sub"]
        for (let i = 1; i < parts.length; i++) {
            dirs.add(parts.slice(0, i).join("/"));
        }
    }
    // Sort by depth so parents come before children
    return [...dirs].sort((a, b) => {
        const da = a.split("/").length;
        const db = b.split("/").length;
        return da !== db ? da - db : a.localeCompare(b);
    });
}

/**
 * Zip a list of scanned files into a Blob using fflate (store-only, level 0).
 *
 * Level 0 (no compression) is intentional:
 *   - Avoids triggering server-side zip-bomb ratio checks
 *   - Faster to create on the client (no CPU-intensive deflation)
 *   - The individual files are already compressed at the content level (PDF, etc.)
 *
 * The onProgress callback receives 0.0–1.0 as files are processed.
 */
export async function zipScannedFiles(
    files: ScannedFile[],
    onProgress?: (ratio: number) => void,
): Promise<Blob> {
    const fflate = await import("fflate");
    const chunks: Uint8Array[] = [];
    
    // Create a Zip stream that collects chunks into an array
    const zip = new fflate.Zip((err, chunk, final) => {
        if (err) {
            console.error("Zip error:", err);
            return;
        }
        chunks.push(chunk);
    });

    for (let i = 0; i < files.length; i++) {
        const { file, relativePath } = files[i];
        const buffer = await file.arrayBuffer();
        
        // Add file to zip (level 0 = store)
        const zipFile = new fflate.ZipPassThrough(relativePath);
        zip.add(zipFile);
        zipFile.push(new Uint8Array(buffer), true);
        
        onProgress?.((i + 1) / files.length);
    }

    zip.end();

    return new Blob(chunks as any[], { type: "application/zip" });
}
