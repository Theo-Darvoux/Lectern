"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Check, Play, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTutorial } from "@/lib/tutorials/use-tutorial";
import { tutorialIcon } from "./tutorial-icons";

/**
 * Lists every tutorial the current user qualifies for, with completion state
 * and a start/replay action. Rendered by the `/help` page; also reusable in a
 * dialog. Launching a tutorial drives the global overlay (which navigates to
 * the relevant route itself).
 */
export function HelpCenter() {
    const t = useTranslations("Tutorials");
    const th = useTranslations("Tutorials.helpCenter");
    const { available, isCompleted, launch, resetAll, completed } = useTutorial();
    const [resetting, setResetting] = useState(false);

    const handleReset = async () => {
        setResetting(true);
        try {
            await resetAll();
            toast.success(th("resetDone"));
        } finally {
            setResetting(false);
        }
    };

    return (
        <div className="mx-auto w-full max-w-2xl space-y-6 p-4 sm:p-6">
            <div className="space-y-1">
                <h1 className="text-2xl font-bold tracking-tight">{th("title")}</h1>
                <p className="text-sm text-muted-foreground">{th("description")}</p>
            </div>

            {available.length === 0 ? (
                <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                    {th("empty")}
                </p>
            ) : (
                <ul className="space-y-3">
                    {available.map((tut) => {
                        const Icon = tutorialIcon(tut.icon);
                        const done = isCompleted(tut.id);
                        return (
                            <li
                                key={tut.id}
                                className="flex items-center gap-4 rounded-xl border bg-card p-4 transition-colors hover:bg-accent/30"
                            >
                                <span
                                    className={cn(
                                        "flex size-10 shrink-0 items-center justify-center rounded-full",
                                        done ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground",
                                    )}
                                >
                                    <Icon className="size-5" />
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2">
                                        <h2 className="truncate font-semibold">{t(`${tut.id}.title`)}</h2>
                                        {done && (
                                            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                                                <Check className="size-3" />
                                                {th("completed")}
                                            </span>
                                        )}
                                    </div>
                                    <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">
                                        {t(`${tut.id}.description`)}
                                    </p>
                                </div>
                                <Button
                                    variant={done ? "outline" : "default"}
                                    size="sm"
                                    className="shrink-0"
                                    onClick={() => launch(tut.id)}
                                >
                                    <Play className="size-4" />
                                    {done ? th("replay") : th("start")}
                                </Button>
                            </li>
                        );
                    })}
                </ul>
            )}

            {completed.length > 0 && (
                <div className="flex justify-end">
                    <Button variant="ghost" size="sm" onClick={handleReset} disabled={resetting}>
                        <RotateCcw className="size-4" />
                        {th("resetAll")}
                    </Button>
                </div>
            )}
        </div>
    );
}
