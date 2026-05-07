import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import "./print.css";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { LayoutShell } from "@/components/layout-shell";
import { ConfigProvider } from "@/components/config-provider";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
};

async function fetchSiteConfig() {
  try {
    const apiBase = process.env.API_INTERNAL_URL ?? "http://api:8000";
    const res = await fetch(`${apiBase}/api/auth/methods`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata(): Promise<Metadata> {
  const config = await fetchSiteConfig();
  const siteName = config?.site_name ?? "WikINT";
  const description = config?.site_description ?? "Collaborative course materials platform";
  const ogImage = config?.og_image_url ?? undefined;

  return {
    title: {
      default: siteName,
      template: `%s • ${siteName}`,
    },
    description,
    openGraph: {
      siteName,
      description,
      ...(ogImage ? { images: [{ url: ogImage }] } : {}),
    },
  };
}


import { getMessages } from 'next-intl/server';
import { LocaleProvider } from '@/components/locale-provider';
import { cookies } from 'next/headers';

export const dynamic = "force-dynamic";

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const messages = await getMessages();
  const locale = (await cookies()).get('NEXT_LOCALE')?.value || 'en';

  return (
    <html lang={locale} suppressHydrationWarning>
      <body className={`${inter.variable} font-sans`}>
        <LocaleProvider initialLocale={locale} initialMessages={messages}>
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <ConfigProvider>
              <LayoutShell>{children}</LayoutShell>
            </ConfigProvider>
            <Toaster position="bottom-left" expand richColors />
          </ThemeProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
