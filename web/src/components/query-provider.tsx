"use client";

import { useEffect, useState, type ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";
import { useAuthStore } from "@/lib/stores";

export function QueryProvider({ children }: { children: ReactNode }) {
    const [queryClient] = useState(getQueryClient);

    // Cached responses belong to the account that fetched them.
    useEffect(
        () =>
            useAuthStore.subscribe((state, previous) => {
                if (state.user?.id !== previous.user?.id) queryClient.clear();
            }),
        [queryClient],
    );

    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
