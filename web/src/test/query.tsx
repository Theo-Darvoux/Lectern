import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";

/** Wrap a component under test in a fresh, non-retrying query cache. */
export function withQueryClient(ui: ReactNode): ReactNode {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

/** Let pending queries resolve and render their results. Call after, not inside, act(). */
export async function settleQueries(): Promise<void> {
    // Query results reach React through scheduled notifications, which are only
    // flushed by an act() scope that starts after they were queued.
    for (let pass = 0; pass < 2; pass++) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 10));
        });
    }
}
