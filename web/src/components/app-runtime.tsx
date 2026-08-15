"use client";

import type { ReactNode } from "react";
import { LayoutShell } from "@/components/layout-shell";
import { TutorialProvider } from "@/components/tutorials/tutorial-provider";

export function AppRuntime({ children }: { children: ReactNode }) {
    return (
        <>
            <LayoutShell>{children}</LayoutShell>
            <TutorialProvider />
        </>
    );
}
