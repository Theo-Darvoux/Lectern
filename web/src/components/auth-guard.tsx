"use client";

import { useEffect, type ReactNode, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";

interface AuthGuardProps {
    children: ReactNode;
    requireOnboarded?: boolean;
}

export function AuthGuard({ children, requireOnboarded = false }: AuthGuardProps) {
    const { user, isAuthenticated, isLoading, bootstrapAuth } = useAuth();
    const router = useRouter();
    const fetchedRef = useRef(false);

    useEffect(() => {
        if (!fetchedRef.current && !isAuthenticated && isLoading) {
            fetchedRef.current = true;
            bootstrapAuth();
        }
    }, [isAuthenticated, isLoading, bootstrapAuth]);

    useEffect(() => {
        if (isLoading) return;

        if (!isAuthenticated) {
            router.replace("/login");
            return;
        }

        if (user?.role === "pending") {
            router.replace("/pending-approval");
            return;
        }

        if (requireOnboarded && user && !user.onboarded) {
            router.replace("/onboarding");
        }
    }, [isAuthenticated, isLoading, user, router, requireOnboarded]);

    if (isLoading) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            </div>
        );
    }

    if (!isAuthenticated) return null;
    if (user?.role === "pending") return null;
    if (requireOnboarded && user && !user.onboarded) return null;

    return <>{children}</>;
}
