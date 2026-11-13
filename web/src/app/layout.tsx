import type { Metadata, Viewport } from "next";
import type { CSSProperties } from "react";
import { Inter, Geist_Mono } from "next/font/google";
import "./globals.css";
import "./print.css";
import { ClientProviders } from "@/components/client-providers";
import { brandColorVariables, wordmarkFontsHref } from "@/lib/brand";
import { socialMetadata } from "@/lib/page-metadata";
import { getPageMeta, getPublicConfig } from "@/lib/server-config";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

// Used only when the API cannot be reached while rendering.
const FALLBACK_SITE_NAME = "Lectern";

// The manifest and home-screen icons are served by the API, next to the rest
// of the branding.
const apiBase = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/$/, "");

// Branding comes from the API's runtime settings on every request (see
// docs/adr/009-server-rendered-runtime-branding.md), so a rebrand needs no
// rebuild and the first paint already shows it.
export async function generateMetadata(): Promise<Metadata> {
  const [config, meta] = await Promise.all([getPublicConfig(), getPageMeta("/")]);
  const siteName = config?.site_name || FALLBACK_SITE_NAME;
  const themeColor = meta?.theme_color ?? config?.primary_color;
  return {
    metadataBase: meta ? new URL(meta.url) : undefined,
    title: { default: meta?.title ?? siteName, template: `%s • ${siteName}` },
    description: meta?.description ?? config?.site_description,
    icons: {
      icon: config?.site_favicon_url || "/favicon.ico",
      apple: `${apiBase}/app/icon-180.png`,
    },
    manifest: `${apiBase}/app/manifest.webmanifest`,
    // No `title`: iOS then uses the manifest `name`.
    appleWebApp: { capable: true, statusBarStyle: "default" },
    ...(meta ? socialMetadata(meta) : {}),
    other: themeColor ? { "msapplication-TileColor": themeColor } : {},
  };
}

export async function generateViewport(): Promise<Viewport> {
  const [config, meta] = await Promise.all([getPublicConfig(), getPageMeta("/")]);
  return {
    width: "device-width",
    initialScale: 1,
    viewportFit: "cover",
    interactiveWidget: "resizes-content",
    themeColor: meta?.theme_color ?? config?.primary_color,
  };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const config = await getPublicConfig();
  const fontsHref = wordmarkFontsHref(config?.site_name_style);

  return (
    <html
      lang="fr"
      suppressHydrationWarning
      style={brandColorVariables(config?.primary_color) as CSSProperties}
    >
      <head>
        {fontsHref && (
          <>
            <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
            <link rel="stylesheet" href={fontsHref} data-lectern-fonts="1" />
          </>
        )}
      </head>
      <body className={`${inter.variable} ${geistMono.variable} font-sans`}>
        <ClientProviders initialConfig={config}>{children}</ClientProviders>
      </body>
    </html>
  );
}
