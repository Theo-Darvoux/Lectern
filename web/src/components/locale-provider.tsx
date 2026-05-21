"use client";

import {
  createContext,
  useCallback,
  useContext,
  useState,
  useTransition,
  type ReactNode,
} from "react";
import { NextIntlClientProvider } from "next-intl";
import type { AbstractIntlMessages } from "next-intl";

interface LocaleContextValue {
  locale: string;
  changeLocale: (newLocale: string) => Promise<void>;
  isPending: boolean;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function useLocaleContext(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    throw new Error("useLocaleContext must be used inside <LocaleProvider>");
  }
  return ctx;
}

interface LocaleProviderProps {
  initialLocale: string;
  initialMessages: AbstractIntlMessages;
  children: ReactNode;
}

export function LocaleProvider({
  initialLocale,
  initialMessages,
  children,
}: LocaleProviderProps) {
  const [locale, setLocale] = useState(initialLocale);
  const [messages, setMessages] = useState<AbstractIntlMessages>(initialMessages);
  const [isPending, startTransition] = useTransition();

  const changeLocale = useCallback(async (newLocale: string) => {
    // Set the cookie so server-side renders also pick up the new locale.
    document.cookie = `NEXT_LOCALE=${newLocale}; path=/; max-age=31536000; SameSite=Lax`;

    // Fetch the new message bundle from public static assets.
    const res = await fetch(`/messages/${newLocale}.json`);
    if (!res.ok) {
      console.error(`Failed to load messages for locale: ${newLocale}`);
      return;
    }
    const newMessages: AbstractIntlMessages = await res.json();

    startTransition(() => {
      setLocale(newLocale);
      setMessages(newMessages);

      // Keep the <html lang="…"> attribute in sync.
      document.documentElement.lang = newLocale;
    });
  }, []);

  return (
    <LocaleContext.Provider value={{ locale, changeLocale, isPending }}>
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}
