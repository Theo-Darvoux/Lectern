import { create } from "zustand";

export interface UploadTarget {
    directoryId: string;
    directoryName: string;
    parentMaterialId?: string | null;
}

interface DropZoneState {
    /** Files buffered from a global drop, waiting for the drawer to consume */
    droppedFiles: DataTransferItemList | null;
    /** Explicit open request from a button (e.g. "Upload Attachment") */
    uploadTarget: UploadTarget | null;
    /** Current browse context kept in sync by DirectoryListing (includes ghost dirs) */
    browseContext: UploadTarget | null;
    /** Callback to dismiss the drag overlay (set by GlobalDropZone, called by UploadDrawer on drop) */
    dismissOverlay: (() => void) | null;
    /** Open the upload drawer for the given target */
    requestUpload: (target: UploadTarget) => void;
    /** Set dropped files (from the global drop handler) */
    setDroppedFiles: (items: DataTransferItemList | null) => void;
    /** Update the current browse context (called by DirectoryListing) */
    setBrowseContext: (ctx: UploadTarget | null) => void;
    /** Register the overlay dismiss callback */
    setDismissOverlay: (cb: (() => void) | null) => void;
    /** Clear everything */
    clear: () => void;
}

export const useDropZoneStore = create<DropZoneState>((set) => ({
    droppedFiles: null,
    uploadTarget: null,
    browseContext: null,
    dismissOverlay: null,
    requestUpload: (target) => set({ uploadTarget: target, droppedFiles: null }),
    setDroppedFiles: (items) => set({ droppedFiles: items }),
    setBrowseContext: (ctx) => set({ browseContext: ctx }),
    setDismissOverlay: (cb) => set({ dismissOverlay: cb }),
    clear: () => set({ droppedFiles: null, uploadTarget: null }),
}));
