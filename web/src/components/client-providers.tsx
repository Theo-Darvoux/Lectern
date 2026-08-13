"use client";

import { useState, useEffect, type ReactNode } from "react";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { LayoutShell } from "@/components/layout-shell";
import { ConfigProvider } from "@/components/config-provider";
import { LocaleProvider } from "@/components/locale-provider";
import { TutorialProvider } from "@/components/tutorials/tutorial-provider";
import type { AbstractIntlMessages } from "next-intl";

import enMessages from "../../messages/en.json";
import frMessages from "../../messages/fr.json";

const MESSAGES: Record<string, AbstractIntlMessages> = { en: enMessages, fr: frMessages };
const DEFAULT_LOCALE = "fr";

function getCookieLocale(): string {
  const match = document.cookie.match(/NEXT_LOCALE=([^;]+)/);
  const val = match?.[1];
  return val && val in MESSAGES ? val : DEFAULT_LOCALE;
}

export function ClientProviders({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState(DEFAULT_LOCALE);
  const [messages, setMessages] = useState<AbstractIntlMessages>(MESSAGES[DEFAULT_LOCALE]);

  useEffect(() => {
    const cookieLocale = getCookieLocale();
    if (cookieLocale !== DEFAULT_LOCALE) {
      setLocale(cookieLocale);
      setMessages(MESSAGES[cookieLocale]);
    }
  }, []);

  return (
    <LocaleProvider
      initialLocale={locale}
      initialMessages={messages}
      messagesByLocale={MESSAGES}
    >
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        <ConfigProvider>
          <LayoutShell>{children}</LayoutShell>
          <TutorialProvider />
        </ConfigProvider>
        <Toaster position="bottom-left" expand richColors />
      </ThemeProvider>
    </LocaleProvider>
  );
}
