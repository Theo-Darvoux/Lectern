import type { Metadata, Viewport } from "next";
import { Inter, Geist_Mono } from "next/font/google";
import "./globals.css";
import "./print.css";
import { ClientProviders } from "@/components/client-providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

const siteName = process.env.SITE_NAME || "Lectern";
const ogSiteName = process.env.OG_SITE_NAME || siteName;
const siteDescription =
  process.env.SITE_DESCRIPTION || "Collaborative course materials platform";
const ogTitle = process.env.OG_TITLE || siteName;
const ogDescription = process.env.OG_DESCRIPTION || siteDescription;
const ogImageUrl = process.env.OG_IMAGE_URL || "/api/og/image";
const themeColor = process.env.OG_THEME_COLOR || process.env.PRIMARY_COLOR || "#3b82f6";
const locale = process.env.OG_LOCALE || "fr_FR";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  themeColor: themeColor,
};

export const metadata: Metadata = {
  title: ogTitle,
  description: ogDescription,
  icons: { icon: process.env.SITE_FAVICON_URL || "/favicon.ico" },
  openGraph: {
    title: ogTitle,
    description: ogDescription,
    siteName: ogSiteName,
    type: "website",
    locale: locale,
    images: [
      {
        url: ogImageUrl,
        width: 1200,
        height: 630,
        alt: ogTitle,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: ogTitle,
    description: ogDescription,
    images: [ogImageUrl],
    site: process.env.OG_TWITTER_SITE || undefined,
    creator: process.env.OG_TWITTER_CREATOR || undefined,
  },
  other: {
    "msapplication-TileColor": themeColor,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" suppressHydrationWarning>
      <body className={`${inter.variable} ${geistMono.variable} font-sans`}>
        <ClientProviders>{children}</ClientProviders>
      </body>
    </html>
  );
}
