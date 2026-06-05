import { defineConfig } from "vitest/config";

// Plain Node test runner for the self-hosted (server.ts) path. The Cloudflare
// path uses vitest.config.ts (workerd pool); this one runs the shared handler
// on Node to prove it works off-Cloudflare with a fake S3 object source.
export default defineConfig({
  test: {
    include: ["src/node/**/*.test.ts"],
    environment: "node",
  },
});
