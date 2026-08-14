import { describe, expect, it } from "vitest";
import {
    directoryGridLayout,
    shouldVirtualizeDirectoryItems,
} from "./virtualized-directory-items";

describe("directory item virtualization", () => {
    it("keeps small listings in normal document flow", () => {
        expect(shouldVirtualizeDirectoryItems(60)).toBe(false);
        expect(shouldVirtualizeDirectoryItems(61)).toBe(true);
    });

    it.each([
        [400, 2],
        [700, 3],
        [900, 4],
        [1400, 5],
    ])("uses %i responsive columns at %ipx", (width, columns) => {
        expect(directoryGridLayout(width).columnCount).toBe(columns);
    });

    it("sizes a bounded grid viewport without clipping a row", () => {
        const layout = directoryGridLayout(1_400, 100);

        expect(layout.rowCount).toBe(20);
        expect(layout.rowHeight).toBeGreaterThan(250);
        expect(layout.viewportHeight).toBeLessThanOrEqual(720);
        expect(layout.viewportHeight % layout.rowHeight).toBe(0);
    });
});
