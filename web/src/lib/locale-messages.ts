import type { AbstractIntlMessages } from "next-intl";
import frMessages from "../../messages/fr.json";

export const DEFAULT_LOCALE = "fr";
export const DEFAULT_MESSAGES: AbstractIntlMessages = frMessages;
export const SUPPORTED_LOCALES = ["fr", "en"] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

const loaders: Record<SupportedLocale, () => Promise<AbstractIntlMessages>> = {
    fr: () => Promise.resolve(frMessages),
    en: () => import("../../messages/en.json").then((module) => module.default),
};

const messageCache = new Map<SupportedLocale, Promise<AbstractIntlMessages>>([
    ["fr", Promise.resolve(frMessages)],
]);

export function isSupportedLocale(locale: string): locale is SupportedLocale {
    return SUPPORTED_LOCALES.includes(locale as SupportedLocale);
}

/** Loads each locale catalog once; non-default catalogs stay in separate chunks. */
export function loadLocaleMessages(locale: string): Promise<AbstractIntlMessages> {
    if (!isSupportedLocale(locale)) {
        return Promise.reject(new Error(`Unsupported locale: ${locale}`));
    }
    const cached = messageCache.get(locale);
    if (cached) return cached;

    const request = loaders[locale]().catch((error) => {
        messageCache.delete(locale);
        throw error;
    });
    messageCache.set(locale, request);
    return request;
}
