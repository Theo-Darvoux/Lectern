"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { onStorageFull } from "@/lib/safe-storage";

/** Tells the user when the browser's local storage refused a write. */
export function StorageFullNotice() {
    const t = useTranslations("Feedback");
    useEffect(() => onStorageFull(() => toast.error(t("storageFull"))), [t]);
    return null;
}
