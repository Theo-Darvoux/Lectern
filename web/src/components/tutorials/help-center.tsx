"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronDown, CircleHelp, Play, RotateCcw, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTutorial } from "@/lib/tutorials/use-tutorial";
import { tutorialIcon } from "./tutorial-icons";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { helpCategoryForTutorial, matchesHelpQuery, type HelpCategory } from "@/lib/help-center";

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
    const [query, setQuery] = useState("");
    const completedCount = available.filter((tutorial) => isCompleted(tutorial.id)).length;
    const completionPercent = available.length > 0
        ? Math.round((completedCount / available.length) * 100)
        : 0;
    const categories: HelpCategory[] = ["gettingStarted", "create", "collaborate"];
    const filteredTutorials = available.filter((tutorial) =>
        matchesHelpQuery(
            [t(`${tutorial.id}.title`), t(`${tutorial.id}.description`), th(`categories.${helpCategoryForTutorial(tutorial.id)}`)],
            query,
        ),
    );
    const faqIds = ["find", "upload", "viewer"] as const;
    const filteredFaqs = faqIds.filter((id) =>
        matchesHelpQuery([th(`faq.${id}.title`), th(`faq.${id}.body`)], query),
    );
    const hasResults = filteredTutorials.length > 0 || filteredFaqs.length > 0;

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
        <div className="mx-auto w-full max-w-3xl space-y-6 p-4 pb-24 sm:p-6 sm:pb-8">
            <div className="rounded-2xl border border-primary/15 bg-card p-5 sm:p-6">
                <h1 className="text-2xl font-bold tracking-tight">{th("title")}</h1>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{th("description")}</p>
                {available.length > 0 && (
                    <div className="mt-5">
                        <div className="mb-2 flex items-center justify-between text-xs">
                            <span className="font-medium">{th("progress", { completed: completedCount, total: available.length })}</span>
                            <span className="tabular-nums text-muted-foreground">{completionPercent}%</span>
                        </div>
                        <Progress value={completionPercent} className="h-2" />
                    </div>
                )}
                <div className="relative mt-5">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={th("searchPlaceholder")}
                        aria-label={th("searchLabel")}
                        className="h-10 bg-background pl-9"
                    />
                </div>
            </div>

            {!hasResults ? (
                <div className="rounded-xl border border-dashed p-8 text-center">
                    <CircleHelp className="mx-auto h-8 w-8 text-muted-foreground/40" />
                    <p className="mt-3 font-medium">{th("noResultsTitle")}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{th("noResultsDescription")}</p>
                </div>
            ) : (
                <div className="space-y-8">
                {categories.map((category) => {
                    const tutorials = filteredTutorials.filter((tutorial) => helpCategoryForTutorial(tutorial.id) === category);
                    if (tutorials.length === 0) return null;
                    return (
                        <section key={category} aria-labelledby={`help-${category}`}>
                            <div className="mb-3">
                                <h2 id={`help-${category}`} className="text-lg font-semibold">
                                    {th(`categories.${category}`)}
                                </h2>
                                <p className="text-sm text-muted-foreground">
                                    {th(`categoryDescriptions.${category}`)}
                                </p>
                            </div>
                            <ul className="grid gap-3 md:grid-cols-2">
                                {tutorials.map((tut) => {
                                    const Icon = tutorialIcon(tut.icon);
                                    const done = isCompleted(tut.id);
                                    return (
                                        <li
                                            key={tut.id}
                                            className="flex h-full flex-col gap-4 rounded-xl border bg-card p-4 transition-colors hover:border-primary/25 hover:bg-accent/20"
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
                                                    <h3 className="truncate font-semibold">{t(`${tut.id}.title`)}</h3>
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
                                                className="mt-auto w-full shrink-0"
                                                onClick={() => launch(tut.id)}
                                            >
                                                <Play className="size-4" />
                                                {done ? th("replay") : th("start")}
                                            </Button>
                                        </li>
                                    );
                                })}
                            </ul>
                        </section>
                    );
                })}

                {filteredFaqs.length > 0 && (
                    <section aria-labelledby="help-quick-answers">
                        <div className="mb-3">
                            <h2 id="help-quick-answers" className="text-lg font-semibold">{th("quickAnswers")}</h2>
                            <p className="text-sm text-muted-foreground">{th("quickAnswersDescription")}</p>
                        </div>
                        <div className="space-y-2">
                            {filteredFaqs.map((id) => (
                                <details key={id} className="group rounded-xl border bg-card">
                                    <summary className="flex cursor-pointer list-none items-center gap-3 p-4 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                                        <CircleHelp className="h-4 w-4 shrink-0 text-primary" />
                                        <span className="flex-1">{th(`faq.${id}.title`)}</span>
                                        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                                    </summary>
                                    <p className="border-t px-4 py-3 text-sm leading-relaxed text-muted-foreground">
                                        {th(`faq.${id}.body`)}
                                    </p>
                                </details>
                            ))}
                        </div>
                    </section>
                )}
                </div>
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
