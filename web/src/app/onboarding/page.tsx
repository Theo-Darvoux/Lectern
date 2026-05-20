"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { useAuthStore, type UserBrief } from "@/lib/stores";
import { apiFetch } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { Sparkles, GraduationCap, CheckCircle2, ArrowRight } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useConfigStore } from "@/lib/stores";

export default function OnboardingPage() {
    const t = useTranslations("Onboarding");
    const { config } = useConfigStore();
    const siteName = config?.site_name || "";
    const { user, isLoading } = useAuth();
    const { setUser } = useAuthStore();
    const [displayName, setDisplayName] = useState("");
    const [academicYear, setAcademicYear] = useState<string>("");
    const [gdprConsent, setGdprConsent] = useState(false);
    const [loading, setLoading] = useState(false);
    const router = useRouter();

    useEffect(() => {
        if (!isLoading && user?.onboarded) {
            router.replace("/");
        }
    }, [isLoading, user, router]);

    const ACADEMIC_YEARS = [
        { value: "1A", label: t("years.1A.label"), description: t("years.1A.description") },
        { value: "2A", label: t("years.2A.label"), description: t("years.2A.description") },
        { value: "3A+", label: t("years.3A+.label"), description: t("years.3A+.description") },
    ] as const;

    // Autocomplete with SSO name if available
    useEffect(() => {
        if (user?.display_name && !displayName) {
            setDisplayName(user.display_name);
        }
    }, [user, displayName]);

    const handleSubmit = async (e: FormEvent) => {
        e.preventDefault();
        if (!gdprConsent) {
            toast.error(t("termsRequired"));
            return;
        }
        setLoading(true);
        try {
            const updated = await apiFetch<UserBrief>("/users/me/onboard", {
                method: "POST",
                body: JSON.stringify({
                    display_name: displayName,
                    academic_year: academicYear,
                    gdpr_consent: gdprConsent,
                }),
            });
            setUser(updated);
            router.push("/");
            toast.success(t("success"));
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failed"));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-background to-background px-4">
            <div className="absolute inset-0 bg-[url('/grid.svg')] bg-center [mask-image:linear-gradient(180deg,white,rgba(255,255,255,0))] pointer-events-none" />
            
            <Card className="relative w-full max-w-md border-primary/10 bg-background/60 backdrop-blur-xl shadow-2xl overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-primary/50 via-primary to-primary/50" />
                
                <CardHeader className="text-center pt-8">
                    <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                        <Sparkles className="h-6 w-6 text-primary animate-pulse" />
                    </div>
                    <CardTitle className="text-3xl font-extrabold tracking-tight">{t("welcome", { siteName })}</CardTitle>
                    <CardDescription className="text-base mt-2">
                        {t("completeProfile")}
                    </CardDescription>
                </CardHeader>

                <CardContent className="pb-8">
                    <form onSubmit={handleSubmit} className="space-y-8">
                        {/* Display Name Section */}
                        <div className="space-y-3">
                            <div className="flex items-center gap-2">
                                <Label htmlFor="displayName" className="text-sm font-semibold flex items-center gap-2">
                                    <CheckCircle2 className="h-4 w-4 text-primary" />
                                    {t("displayName")}
                                </Label>
                                {user?.display_name && (
                                    <span className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">
                                        {t("importedFromGoogle")}
                                    </span>
                                )}
                            </div>
                            <Input
                                id="displayName"
                                type="text"
                                placeholder={t("displayNamePlaceholder")}
                                value={displayName}
                                onChange={(e) => setDisplayName(e.target.value)}
                                className="h-12 bg-background/50 border-primary/20 focus:border-primary transition-all text-lg"
                                required
                                autoFocus
                            />
                        </div>

                        {/* Academic Year Section */}
                        <div className="space-y-3">
                            <Label className="text-sm font-semibold flex items-center gap-2">
                                <GraduationCap className="h-4 w-4 text-primary" />
                                {t("academicYear")}
                            </Label>
                            <div className="grid grid-cols-3 gap-3">
                                {ACADEMIC_YEARS.map((year) => (
                                    <button
                                        key={year.value}
                                        type="button"
                                        onClick={() => setAcademicYear(year.value)}
                                        className={`group relative flex flex-col items-center justify-center rounded-xl border-2 p-3 transition-all duration-200 ${
                                            academicYear === year.value
                                                ? "border-primary bg-primary/5 shadow-inner"
                                                : "border-primary/10 bg-background/40 hover:border-primary/30 hover:bg-primary/5"
                                        }`}
                                    >
                                        <span className={`text-lg font-bold ${academicYear === year.value ? "text-primary" : "text-muted-foreground"}`}>
                                            {year.label}
                                        </span>
                                        <span className="text-[9px] uppercase tracking-wider text-muted-foreground/60 font-medium">
                                            {year.description}
                                        </span>
                                        {academicYear === year.value && (
                                            <div className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-primary flex items-center justify-center">
                                                <CheckCircle2 className="h-3 w-3 text-white" />
                                            </div>
                                        )}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Terms Section */}
                        <div className="relative rounded-2xl border border-primary/10 bg-primary/5 p-4 flex items-start gap-4 group transition-colors hover:bg-primary/10">
                            <div className="flex h-6 items-center">
                                <Checkbox
                                    id="gdpr"
                                    checked={gdprConsent}
                                    onCheckedChange={(checked) => setGdprConsent(!!checked)}
                                    className="h-5 w-5 border-2 border-primary data-[state=checked]:bg-primary"
                                />
                            </div>
                            <div className="grid gap-1.5 leading-none">
                                <Label
                                    htmlFor="gdpr"
                                    className="text-sm font-medium leading-normal cursor-pointer text-muted-foreground group-hover:text-foreground transition-colors"
                                >
                                    {t("gdprAgree", { siteName })}
                                    <span className="block mt-1 text-xs opacity-70 italic">
                                        {t.rich("gdprNote", {
                                            privacy: (chunks) => (
                                                <Link href="/privacy" className="text-primary underline underline-offset-4 hover:opacity-100 font-bold">
                                                    {chunks}
                                                </Link>
                                            )
                                        })}
                                    </span>
                                </Label>
                            </div>
                        </div>

                        {/* Submit Section */}
                        <Button
                            type="submit"
                            size="lg"
                            className="w-full h-14 text-lg font-bold group shadow-lg shadow-primary/20 relative overflow-hidden"
                            disabled={loading || !academicYear || !displayName || !gdprConsent}
                        >
                            <span className="relative z-10 flex items-center justify-center gap-2">
                                {loading ? t("finalizing") : t("getStarted")}
                                {!loading && <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />}
                            </span>
                            <div className="absolute inset-0 bg-gradient-to-r from-primary via-primary/80 to-primary opacity-0 group-hover:opacity-100 transition-opacity" />
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
