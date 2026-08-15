"use client";

import { useState, useEffect, type ReactNode } from "react";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { ConfigProvider } from "@/components/config-provider";
import { LocaleProvider } from "@/components/locale-provider";
import { AuthBootstrap } from "@/components/auth-bootstrap";
import { RuntimeRouter } from "@/components/runtime-router";
import { CookieBanner } from "@/components/cookie-banner";
import type { AbstractIntlMessages } from "next-intl";
import {
  DEFAULT_LOCALE,
  DEFAULT_MESSAGES,
  isSupportedLocale,
  loadLocaleMessages,
  SUPPORTED_LOCALES,
  type SupportedLocale,
} from "@/lib/locale-messages";

function getCookieLocale(): SupportedLocale {
  const match = document.cookie.match(/NEXT_LOCALE=([^;]+)/);
  const val = match?.[1];
  return val && isSupportedLocale(val) ? val : DEFAULT_LOCALE;
}

export function ClientProviders({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<SupportedLocale>(DEFAULT_LOCALE);
  const [messages, setMessages] = useState<AbstractIntlMessages>(DEFAULT_MESSAGES);

  useEffect(() => {
    const cookieLocale = getCookieLocale();
    let active = true;
    void loadLocaleMessages(cookieLocale).then((catalog) => {
      if (!active) return;
      setLocale(cookieLocale);
      setMessages(catalog);
    });
    return () => { active = false; };
  }, []);

  return (
    <LocaleProvider
      initialLocale={locale}
      initialMessages={messages}
      supportedLocales={SUPPORTED_LOCALES}
      loadMessages={loadLocaleMessages}
    >
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        <ConfigProvider>
          <AuthBootstrap />
          <RuntimeRouter>{children}</RuntimeRouter>
        </ConfigProvider>
        <CookieBanner />
        <Toaster position="bottom-left" expand richColors />
      </ThemeProvider>
    </LocaleProvider>
  );
}
