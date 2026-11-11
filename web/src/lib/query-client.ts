import { QueryClient, isServer } from "@tanstack/react-query";
import { isRetriableError } from "@/lib/api-client";

const MAX_RETRIES = 2;

function createQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: {
                // apiFetch already refreshes expired tokens, so only transient
                // network and server failures are worth another attempt.
                retry: (failureCount, error) => failureCount < MAX_RETRIES && isRetriableError(error),
                // Data stays stale: each mount shows the cached copy at once and
                // refreshes it, as the screens did before they shared a cache.
                refetchOnWindowFocus: false,
            },
        },
    });
}

let browserQueryClient: QueryClient | undefined;

/**
 * The browser's single query cache, also reachable outside React (hover
 * prefetches, live-update invalidation). The server gets a fresh one per call
 * so cached data never crosses requests.
 */
export function getQueryClient(): QueryClient {
    if (isServer) return createQueryClient();
    browserQueryClient ??= createQueryClient();
    return browserQueryClient;
}
