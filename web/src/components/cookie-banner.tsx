"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";

export function CookieBanner() {
    const t = useTranslations("Layout");
    const [show, setShow] = useState(false);

    useEffect(() => {
        queueMicrotask(() => {
            const consent = localStorage.getItem("lectern_cookie_consent");
            if (!consent) {
                setShow(true);
            }
        });
    }, []);

    const accept = () => {
        localStorage.setItem("lectern_cookie_consent", "true");
        setShow(false);
    };

    if (!show) return null;

    return (
        <div className="fixed bottom-20 sm:bottom-4 right-4 z-[100] max-w-sm rounded-lg border bg-background p-4 shadow-lg animate-in slide-in-from-bottom-2">
            <h3 className="text-sm font-semibold mb-2">{t("cookieConsentTitle")}</h3>
            <p className="text-xs text-muted-foreground mb-4">
                {t("cookieConsentMessage")}
            </p>
            <div className="flex justify-end gap-2">
                <Button variant="default" size="sm" onClick={accept}>
                    {t("gotIt")}
                </Button>
            </div>
        </div>
    );
}
