import type { NextConfig } from "next";
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  // Production runs the Next server (`node server.js` in web/Dockerfile): the
  // root layout renders the runtime branding into every page, so the build
  // carries no instance-specific settings.
  output: "standalone",
  trailingSlash: true,
  transpilePackages: ["papaparse", "@react-pdf/renderer"],
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default withNextIntl(nextConfig);
