import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Node-runtime tests for the self-hosted path run via vitest.node.config.ts,
    // not the workerd pool.
    exclude: [...configDefaults.exclude, "src/node/**"],
  },
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
