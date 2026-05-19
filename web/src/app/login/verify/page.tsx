"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { useTranslations } from "next-intl";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useConfigStore } from "@/lib/stores";
import { SiteName } from "@/components/site-name";

function MagicLinkVerifier() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const { verifyMagicLink, isAuthenticated, user } = useAuth();
    const t = useTranslations("Login");
    const [error, setError] = useState<string | null>(null);
    const [isVerifying, setIsVerifying] = useState(false);
    const token = searchParams.get("token");
    const attempted = useRef(false);
    const { config } = useConfigStore();

    useEffect(() => {
        if (!token && !isAuthenticated && !attempted.current) {
            attempted.current = true;

            setError(t("invalidMagicLink"));
        }
    }, [token, isAuthenticated, t]);

    useEffect(() => {
        if (isAuthenticated && user?.onboarded) {
            router.replace("/browse");
        } else if (isAuthenticated && !user?.onboarded) {
            router.replace("/onboarding");
        }
    }, [isAuthenticated, user, router]);

    const handleVerify = async () => {
        if (!token || isVerifying) return;
        setIsVerifying(true);
        try {
            const data = await verifyMagicLink(token);
            if (data.is_new_user || !data.user.onboarded) {
                router.replace("/onboarding");
            } else {
                router.replace("/browse");
            }
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : t("magicLinkExpired")
            );
            setIsVerifying(false);
        }
    };

    if (error) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background px-4">
                <Card className="w-full max-w-md border-destructive/20 shadow-lg">
                    <CardHeader className="text-center">
                        <CardTitle className="text-3xl font-bold tracking-tight"><SiteName name={config?.site_name || "WikINT"} style={config?.site_name_style} /></CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-6 text-center">
                        <div className="rounded-lg bg-destructive/10 p-4">
                            <p className="text-sm font-medium text-destructive">{error}</p>
                        </div>
                        <p className="text-sm text-muted-foreground">
                            {t("magicLinkExpiredDesc")}
                        </p>
                        <Button
                            className="w-full py-6 text-lg"
                            variant="outline"
                            onClick={() => router.push("/login")}
                        >
                            {t("backToLogin")}
                        </Button>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="flex min-h-screen items-center justify-center bg-background px-4">
            <Card className="w-full max-w-md shadow-2xl border-primary/10">
                <CardHeader className="text-center pt-8">
                    <CardTitle className="text-4xl font-black tracking-tighter"><SiteName name={config?.site_name || "WikINT"} style={config?.site_name_style} /></CardTitle>
                </CardHeader>
                <CardContent className="space-y-8 text-center pb-12 pt-4">
                    <div className="space-y-2">
                        <h2 className="text-xl font-semibold">{t("verifySignIn")}</h2>
                        <p className="text-sm text-muted-foreground px-8">
                            {t("verifySignInDesc")}
                        </p>
                    </div>

                    <Button
                        size="lg"
                        className="w-full h-16 text-xl font-bold transition-all hover:scale-[1.02] active:scale-[0.98]"
                        onClick={handleVerify}
                        disabled={isVerifying || !token}
                    >
                        {isVerifying ? (
                            <span className="flex items-center gap-2">
                                <span className="h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                {t("verifying")}
                            </span>
                        ) : (
                            t("confirmSignIn")
                        )}
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}

export default function MagicLinkPage() {
    const t = useTranslations("Login");
    const { config } = useConfigStore();

    return (
        <Suspense
            fallback={
                <div className="flex min-h-screen items-center justify-center bg-background px-4">
                    <Card className="w-full max-w-md">
                        <CardHeader className="text-center">
                            <CardTitle className="text-2xl font-bold"><SiteName name={config?.site_name || "WikINT"} style={config?.site_name_style} /></CardTitle>
                        </CardHeader>
                        <CardContent className="text-center">
                            <p className="text-sm text-muted-foreground">{t("loading")}</p>
                        </CardContent>
                    </Card>
                </div>
            }
        >
            <MagicLinkVerifier />
        </Suspense>
    );
}
