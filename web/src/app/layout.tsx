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

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
};

export const metadata: Metadata = {
  title: process.env.SITE_NAME || "WikINT",
  description: process.env.SITE_DESCRIPTION || "Collaborative course materials platform",
  icons: { icon: process.env.SITE_FAVICON_URL || "/favicon.ico" },
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
