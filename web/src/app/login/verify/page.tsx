"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { useConfigStore } from "@/lib/stores";
import { ShaderText } from "@/components/shader-text";

function MagicLinkVerifier() {
    const router = useRouter();
    const { verifyMagicLink, isAuthenticated, user } = useAuth();
    const t = useTranslations("Login");
    const [error, setError] = useState<string | null>(null);
    const [isVerifying, setIsVerifying] = useState(false);
    const [token, setToken] = useState<string | null>(null);
    const [linkLoaded, setLinkLoaded] = useState(false);
    const attempted = useRef(false);
    const config = useConfigStore((state) => state.config);

    const siteName = config?.site_name || process.env.NEXT_PUBLIC_SITE_NAME || t("title") || "Lectern";

    useEffect(() => {
        document.title = `${t("verifySignIn")} • ${siteName}`;
    }, [siteName, t]);

    // URL fragments are never sent in the HTTP request target. Read the
    // capability once and immediately remove it from browser history before
    // the user has to click the confirmation button.
    useEffect(() => {
        const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
        const capturedToken = params.get("token");
        if (capturedToken) {
            setToken(capturedToken);
        }

        // Never accept a legacy query token, but scrub one if an old emailed
        // link is opened after deployment so the secret does not remain in
        // browser history.
        const cleanUrl = new URL(window.location.href);
        const hadLegacyQueryToken = cleanUrl.searchParams.has("token");
        cleanUrl.searchParams.delete("token");
        if (window.location.hash || hadLegacyQueryToken) {
            window.history.replaceState(
                null,
                "",
                `${cleanUrl.pathname}${cleanUrl.search}`,
            );
        }
        setLinkLoaded(true);
    }, []);

    useEffect(() => {
        if (linkLoaded && !token && !isAuthenticated && !attempted.current) {
            attempted.current = true;
            setError(t("invalidMagicLink"));
        }
    }, [linkLoaded, token, isAuthenticated, t]);

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

    return (
        <div className="login-page relative flex min-h-screen w-full flex-col items-center justify-center p-4 sm:p-6 overflow-hidden">
            {/* Background layers: solid matte base + fine grain texture + dot matrix */}
            <div className="login-bg-base" aria-hidden="true" />
            <div className="login-grain" aria-hidden="true" />
            <div className="login-dots" aria-hidden="true" />

            {/* Auth Card - Matching /login */}
            <div className="login-card-wrapper relative z-10 w-full max-w-[420px] p-6 sm:p-8 space-y-5">
                {/* Header: 90s 3D Chrome Shader Title */}
                <div className="text-center -mt-2">
                    <ShaderText
                        text={siteName}
                        style={config?.site_name_style}
                        className="text-3xl font-extrabold tracking-tight text-[#f8f7fc]"
                    />
                    <p className="-mt-4 sm:-mt-5 text-xs sm:text-[0.8125rem] font-medium tracking-[0.015em] text-[#918da6] leading-relaxed max-w-[310px] mx-auto mb-1">
                        {t("verifySignIn")}
                    </p>
                </div>

                <div className="login-sep" aria-hidden="true" />

                {error ? (
                    <div className="space-y-4">
                        <div className="rounded-md bg-[#1e1318] p-3.5 border border-[#4a1c24] text-center text-xs text-[#f87171] leading-relaxed">
                            <p className="font-semibold text-sm mb-1">{error}</p>
                            <p className="text-[#a87a82] text-xs">{t("magicLinkExpiredDesc")}</p>
                        </div>

                        <Button
                            type="button"
                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                            onClick={() => router.push("/login")}
                        >
                            {t("backToLogin")}
                        </Button>
                    </div>
                ) : (
                    <div className="space-y-4">
                        <div className="rounded-md bg-[#12131d] p-3.5 border border-[#1a1c29] text-center text-xs text-[#828096] leading-relaxed">
                            <p>{t("verifySignInDesc")}</p>
                        </div>

                        <Button
                            type="button"
                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                            onClick={handleVerify}
                            disabled={isVerifying || !token || !linkLoaded}
                        >
                            {isVerifying ? (
                                <>
                                    <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                    {t("verifying")}
                                </>
                            ) : (
                                t("confirmSignIn")
                            )}
                        </Button>

                        <Button
                            type="button"
                            variant="ghost"
                            className="w-full h-9 text-xs text-[#6a667d] hover:text-[#f0eef5]"
                            onClick={() => router.push("/login")}
                            disabled={isVerifying}
                        >
                            {t("backToLogin")}
                        </Button>
                    </div>
                )}
            </div>

            {/* Footer Links */}
            <footer className="relative z-10 mt-6 flex items-center justify-center gap-4 text-center text-xs text-[#524f64]">
                <Link href="/privacy" className="hover:text-[#828096] transition-colors">
                    Privacy
                </Link>
                <span>·</span>
                <Link href="/terms" className="hover:text-[#828096] transition-colors">
                    Terms
                </Link>
            </footer>
        </div>
    );
}

export default function MagicLinkPage() {
    const t = useTranslations("Login");
    const config = useConfigStore((state) => state.config);
    const siteName = config?.site_name || process.env.NEXT_PUBLIC_SITE_NAME || t("title") || "Lectern";

    return (
        <Suspense
            fallback={
                <div className="login-page relative flex min-h-screen w-full flex-col items-center justify-center p-4 sm:p-6 overflow-hidden">
                    <div className="login-bg-base" aria-hidden="true" />
                    <div className="login-grain" aria-hidden="true" />
                    <div className="login-dots" aria-hidden="true" />
                    <div className="login-card-wrapper relative z-10 w-full max-w-[420px] p-6 sm:p-8 space-y-5">
                        <div className="text-center -mt-2">
                            <ShaderText
                                text={siteName}
                                style={config?.site_name_style}
                                className="text-3xl font-extrabold tracking-tight text-[#f8f7fc]"
                            />
                            <p className="-mt-4 sm:-mt-5 text-xs sm:text-[0.8125rem] font-medium tracking-[0.015em] text-[#918da6] leading-relaxed max-w-[310px] mx-auto mb-1">
                                {t("loading")}
                            </p>
                        </div>
                        <div className="login-sep" aria-hidden="true" />
                        <div className="flex justify-center py-6">
                            <span className="inline-block h-6 w-6 rounded-full border-2 border-current border-r-transparent animate-spin text-[#dedbe8]" />
                        </div>
                    </div>
                </div>
            }
        >
            <MagicLinkVerifier />
        </Suspense>
    );
}
