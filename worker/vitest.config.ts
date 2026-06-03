import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      main: "./src/index.ts",
      wrangler: { configPath: "./wrangler.toml" },
      miniflare: {
        // Must match TEST_SECRET in src/index.test.ts.
        bindings: { HMAC_SECRET: "test-hmac-secret" },
      },
    }),
  ],
});
