import { create } from "zustand";

export interface SelectedItem {
    id: string;
    type: "directory" | "material";
    name: string;
    /** Current parent directory ID (used to detect no-op moves) */
    parentId?: string | null;
    /** For materials, the specific type (polycopie, annal, etc.) */
    material_type?: string;
}

interface SelectionState {
    /** Whether multi-select mode is active */
    selectMode: boolean;
    /** Currently selected items keyed by id */
    selected: Map<string, SelectedItem>;
    /** Items that have been "cut" for pasting elsewhere */
    clipboard: SelectedItem[];

    setSelectMode: (on: boolean) => void;
    toggle: (item: SelectedItem) => void;
    selectAll: (items: SelectedItem[]) => void;
    /** Pass IDs to deselect only those; omit to clear all */
    deselectAll: (ids?: string[]) => void;
    cut: () => void;
    /** Replace the clipboard with the given items (e.g. "Move" from an item's menu) */
    cutItems: (items: SelectedItem[]) => void;
    clearClipboard: () => void;
    /** Exit select mode and clear everything */
    reset: () => void;
}

export const useSelectionStore = create<SelectionState>()((set, get) => ({
    selectMode: false,
    selected: new Map(),
    clipboard: [],

    setSelectMode: (on) => {
        if (!on) {
            set({ selectMode: false, selected: new Map() });
        } else {
            set({ selectMode: true });
        }
    },

    toggle: (item) =>
        set((s) => {
            const next = new Map(s.selected);
            if (next.has(item.id)) {
                next.delete(item.id);
            } else {
                next.set(item.id, item);
            }
            return { selected: next };
        }),

    selectAll: (items) =>
        set((s) => {
            const next = new Map(s.selected);
            for (const item of items) next.set(item.id, item);
            return { selected: next };
        }),

    deselectAll: (ids?: string[]) =>
        set((s) => {
            if (!ids) return { selected: new Map() };
            const next = new Map(s.selected);
            for (const id of ids) next.delete(id);
            return { selected: next };
        }),

    cut: () => {
        const items = Array.from(get().selected.values());
        set({ clipboard: items, selected: new Map(), selectMode: false });
    },

    cutItems: (items) => set({ clipboard: items, selected: new Map(), selectMode: false }),

    clearClipboard: () => set({ clipboard: [] }),

    reset: () => set({ selectMode: false, selected: new Map(), clipboard: [] }),
}));
