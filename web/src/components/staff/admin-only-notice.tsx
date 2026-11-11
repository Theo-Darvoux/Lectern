"use client";

import { useTranslations } from "next-intl";

/** Shown in place of an administrator-only staff page. */
export function AdminOnlyNotice() {
    const t = useTranslations("Staff");
    return (
        <div className="flex items-center justify-center p-12 text-muted-foreground">
            {t("adminOnly")}
        </div>
    );
}
