import type { NextConfig } from "next";
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  // Static HTML export targets the production build that Nginx serves. In
  // `next dev` we keep the normal server so deep dynamic routes (e.g.
  // /browse/<path>) render without enumerating every possible param.
  ...(process.env.NODE_ENV === "production" ? { output: "export" as const } : {}),
  trailingSlash: true,
  transpilePackages: ["papaparse", "@react-pdf/renderer"],
  typescript: {
    ignoreBuildErrors: true,
  },
  // NOTE: deep dynamic routes (e.g. /browse/<id>) 404 in `next dev` because the
  // pages keep `dynamicParams = false` (required by `output: export`). Next
  // rewrites do NOT fix this — `beforeFiles` rewrites don't pre-empt a matching
  // App Router dynamic route, so the request still hits the page and 404s. The
  // dev SPA fallback is therefore handled in the dev Nginx
  // (infra/nginx/nginx.dev.conf.template); production uses the static export +
  // web/nginx.conf try_files fallback.
};

export default withNextIntl(nextConfig);
