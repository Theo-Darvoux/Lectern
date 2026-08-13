"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
  messagesByLocale: Record<string, AbstractIntlMessages>;
  children: ReactNode;
}

export function LocaleProvider({
  initialLocale,
  initialMessages,
  messagesByLocale,
  children,
}: LocaleProviderProps) {
  const [locale, setLocale] = useState(initialLocale);
  const [messages, setMessages] = useState<AbstractIntlMessages>(initialMessages);
  const [isPending, startTransition] = useTransition();

  // ClientProviders resolves the persisted locale after hydration so the server
  // and first client render stay deterministic. Apply that resolved value when
  // the incoming initial props change; useState initializers alone would ignore it.
  useEffect(() => {
    setLocale(initialLocale);
    setMessages(initialMessages);
    document.documentElement.lang = initialLocale;
  }, [initialLocale, initialMessages]);

  const changeLocale = useCallback(async (newLocale: string) => {
    const newMessages = messagesByLocale[newLocale];
    if (!newMessages) {
      console.error(`Unsupported locale: ${newLocale}`);
      return;
    }

    // The canonical bundles are already imported by ClientProviders, so avoid
    // a second network request to stale duplicated files under public/messages.
    document.cookie = `NEXT_LOCALE=${newLocale}; path=/; max-age=31536000; SameSite=Lax`;

    startTransition(() => {
      setLocale(newLocale);
      setMessages(newMessages);

      // Keep the <html lang="…"> attribute in sync.
      document.documentElement.lang = newLocale;
    });
  }, [messagesByLocale]);

  return (
    <LocaleContext.Provider value={{ locale, changeLocale, isPending }}>
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}
