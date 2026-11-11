"use client";

import type { Locale } from "date-fns";
import { enUS, fr } from "date-fns/locale";
import { useLocale } from "next-intl";

/** The date-fns locale matching the interface language, for relative dates and formats. */
export function useDateLocale(): Locale {
    return useLocale() === "fr" ? fr : enUS;
}
