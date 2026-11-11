type RuntimeTranslator = ((key: string) => string) & { has: (key: string) => boolean };

/**
 * Translate a key known only at runtime, such as an enum value sent by the API,
 * falling back when the catalog has no entry. next-intl types keys statically,
 * so this is the one place that widens a translator to plain strings.
 */
export function translateDynamic(t: unknown, key: string, fallback: string = key): string {
    const translate = t as RuntimeTranslator;
    return translate.has(key) ? translate(key) : fallback;
}
