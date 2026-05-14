import { NextResponse } from "next/server";

/**
 * Lightweight health check endpoint for Docker/container orchestration.
 *
 * This route handler intercepts GET /api/health *before* the Next.js rewrite
 * proxy sends it to the backend, preventing the "controller[kState].transformAlgorithm
 * is not a function" TransformStream race condition that occurs when health-check
 * clients (e.g. `wget --spider`) close the connection immediately after receiving
 * headers, which tears down the stream controller mid-transform.
 *
 * The backend's own health endpoint (/api/health on FastAPI) is still accessible
 * directly through nginx for detailed service-level health checks.
 */
export async function GET() {
    return NextResponse.json({ status: "ok" }, { status: 200 });
}
