"use client";

import { AlertCircle, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Inline replacement for content that failed to load, with a retry action. */
export function QueryError({
    onRetry,
    message,
    className,
}: {
    onRetry: () => void;
    message?: string;
    className?: string;
}) {
    const t = useTranslations("Common");
    return (
        <div
            role="alert"
            className={cn(
                "flex flex-col items-center gap-2 py-4 text-center text-sm text-muted-foreground",
                className,
            )}
        >
            <span className="flex items-center gap-1.5">
                <AlertCircle className="size-4 shrink-0 text-destructive" aria-hidden />
                {message ?? t("loadFailed")}
            </span>
            <Button variant="outline" size="sm" onClick={onRetry}>
                <RotateCcw className="size-3.5" aria-hidden />
                {t("tryAgain")}
            </Button>
        </div>
    );
}
