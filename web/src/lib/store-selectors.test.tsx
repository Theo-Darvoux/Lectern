import React, { Profiler, act } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import {
    selectDirectoryColorOverride,
    selectDirectoryIconOverride,
    useDirectoryColorOverrides,
    useDirectoryIconOverrides,
} from "./stores";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("directory override selectors", () => {
    let container: HTMLDivElement;
    let root: Root;
    let renders = 0;

    beforeEach(() => {
        useDirectoryIconOverrides.setState({ overrides: new Map() });
        useDirectoryColorOverrides.setState({ overrides: new Map() });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        renders = 0;
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("does not rerender a card when another directory override changes", () => {
        function Probe() {
            const icon = useDirectoryIconOverrides(selectDirectoryIconOverride("directory-a"));
            const color = useDirectoryColorOverrides(selectDirectoryColorOverride("directory-a"));
            return <span>{icon ?? color ?? "default"}</span>;
        }

        const countRender = () => { renders += 1; };
        act(() => root.render(
            <Profiler id="directory-card" onRender={countRender}>
                <Probe />
            </Profiler>,
        ));
        expect(renders).toBe(1);

        act(() => {
            useDirectoryIconOverrides.getState().setIconOverride("directory-b", "book");
            useDirectoryColorOverrides.getState().setColorOverride("directory-b", "blue");
        });
        expect(renders).toBe(1);

        act(() => {
            useDirectoryIconOverrides.getState().setIconOverride("directory-a", "folder");
        });
        expect(renders).toBe(2);
        expect(container.textContent).toBe("folder");
    });
});
