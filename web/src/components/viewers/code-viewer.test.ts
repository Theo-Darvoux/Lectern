import { describe, expect, it } from "vitest";
import { splitHighlightedLines } from "./code-viewer";

describe("splitHighlightedLines", () => {
    it("closes and reopens a token that spans lines", () => {
        const html = 'a <span class="hljs-comment">/* one\ntwo */</span> b';

        expect(splitHighlightedLines(html)).toEqual([
            'a <span class="hljs-comment">/* one</span>',
            '<span class="hljs-comment">two */</span> b',
        ]);
    });

    it("keeps nested spans balanced on every line", () => {
        const html = '<span class="a"><span class="b">x\ny</span>z</span>\n';

        expect(splitHighlightedLines(html)).toEqual([
            '<span class="a"><span class="b">x</span></span>',
            '<span class="a"><span class="b">y</span>z</span>',
            "",
        ]);
    });
});
