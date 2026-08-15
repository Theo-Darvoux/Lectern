"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { initAuthSync } from "@/lib/auth-sync";
import { normalizePathname } from "@/lib/utils";

/** Owns app-lifetime auth restoration independently of the route's visual shell. */
export function AuthBootstrap() {
    const pathname = normalizePathname(usePathname());
    const { isAuthenticated, isLoading, bootstrapAuth } = useAuth();
    const started = useRef(false);

    useEffect(() => initAuthSync(), []);

    useEffect(() => {
        if (pathname === "/setup" || started.current || isAuthenticated || !isLoading) {
            return;
        }
        started.current = true;
        void bootstrapAuth();
    }, [pathname, isAuthenticated, isLoading, bootstrapAuth]);

    return null;
}
