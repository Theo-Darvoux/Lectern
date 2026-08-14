"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { requiresAppRuntime } from "@/lib/runtime-policy";

const AppRuntime = dynamic(
    () => import("@/components/app-runtime").then((module) => module.AppRuntime),
    {
        loading: () => (
            <main className="flex min-h-svh items-center justify-center" aria-busy="true">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            </main>
        ),
    },
);

export function RuntimeRouter({ children }: { children: ReactNode }) {
    const pathname = usePathname();
    return requiresAppRuntime(pathname) ? <AppRuntime>{children}</AppRuntime> : children;
}
