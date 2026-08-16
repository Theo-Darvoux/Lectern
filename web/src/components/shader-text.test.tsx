import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ShaderText } from "./shader-text";
import { useConfigStore } from "@/lib/stores";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("ShaderText Component", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        vi.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        useConfigStore.setState({ config: null });
    });

    afterEach(() => {
        if (root) {
            act(() => root.unmount());
        }
        container?.remove();
    });

    it("renders fallback SiteName when WebGL is unavailable and style is plain text", async () => {
        // In jsdom environment, WebGL is not available by default
        await act(async () => {
            root.render(
                <ShaderText text="Lectern" className="text-3xl font-extrabold" />
            );
        });

        expect(container.textContent).toContain("Lectern");
    });

    it("renders styled segments when style is provided via props", async () => {
        const style = JSON.stringify([
            { text: "Lect", font: "Inter", color: "#7c3aed", bold: true, italic: false },
            { text: "ern", font: "Playfair Display", color: "#06b6d4", bold: false, italic: true },
        ]);

        await act(async () => {
            root.render(
                <ShaderText text="FallbackName" style={style} />
            );
        });

        expect(container.textContent).toContain("Lect");
        expect(container.textContent).toContain("ern");

        const spans = container.querySelectorAll("span span");
        expect(spans.length).toBe(2);
        expect(spans[0].textContent).toBe("Lect");
        expect((spans[0] as HTMLElement).style.color).toBe("rgb(124, 58, 237)");
        expect((spans[0] as HTMLElement).style.fontFamily).toContain("Inter");
        expect((spans[0] as HTMLElement).style.fontWeight).toBe("700");

        expect(spans[1].textContent).toBe("ern");
        expect((spans[1] as HTMLElement).style.color).toBe("rgb(6, 182, 212)");
        expect((spans[1] as HTMLElement).style.fontFamily).toContain("Playfair Display");
        expect((spans[1] as HTMLElement).style.fontStyle).toBe("italic");
    });

    it("automatically picks up site_name_style from config store if style prop is not passed", async () => {
        const style = JSON.stringify([
            { text: "INT", font: "JetBrains Mono", color: "#3b82f6", bold: true, italic: false },
            { text: "ellect", font: "Caveat", color: null, bold: false, italic: true },
        ]);

        useConfigStore.setState({
            config: {
                site_name: "INTellect",
                site_name_style: style,
            } as any,
        });

        await act(async () => {
            root.render(
                <ShaderText text="INTellect" />
            );
        });

        expect(container.textContent).toContain("INT");
        expect(container.textContent).toContain("ellect");

        const spans = container.querySelectorAll("span span");
        expect(spans.length).toBe(2);
        expect(spans[0].textContent).toBe("INT");
        expect((spans[0] as HTMLElement).style.color).toBe("rgb(59, 130, 246)");
        expect((spans[0] as HTMLElement).style.fontFamily).toContain("JetBrains Mono");
        expect((spans[0] as HTMLElement).style.fontWeight).toBe("700");

        expect(spans[1].textContent).toBe("ellect");
        expect((spans[1] as HTMLElement).style.fontFamily).toContain("Caveat");
        expect((spans[1] as HTMLElement).style.fontStyle).toBe("italic");
    });
});
