/**
 * Next.js instrumentation hook — runs once when the server starts.
 *
 * We register a global `unhandledRejection` listener here to suppress the
 * spurious "controller[kState].transformAlgorithm is not a function" error
 * that surfaces when a client disconnects mid-stream (e.g. navigation, browser
 * close, or a Docker healthcheck using `wget --spider`).
 *
 * The error originates from Node.js's internal web-streams implementation:
 * when a TransformStream controller is garbage-collected while a write is still
 * in flight, `kState.transformAlgorithm` is already undefined. This is a
 * race condition inside Node.js/Next.js that cannot be fixed from user-land —
 * it is non-fatal and does not affect the user experience.
 *
 * Reference: https://github.com/vercel/next.js/issues/[stream-race-condition]
 */
export async function register() {
    if (process.env.NEXT_RUNTIME === "nodejs") {
        process.on("unhandledRejection", (reason) => {
            if (
                reason instanceof TypeError &&
                typeof reason.message === "string" &&
                reason.message.includes("transformAlgorithm is not a function")
            ) {
                // Silently swallow — this is a known Node.js/Next.js streaming
                // race condition triggered by premature client disconnects.
                return;
            }
            // Re-throw all other unhandled rejections so they surface normally.
            throw reason;
        });
    }
}
