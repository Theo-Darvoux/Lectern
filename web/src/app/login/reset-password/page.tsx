"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Eye, EyeOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ShaderText } from "@/components/shader-text";
import { apiFetch } from "@/lib/api-client";
import { useConfigStore } from "@/lib/stores";

export default function ResetPasswordPage() {
    const t = useTranslations("Login");
    const config = useConfigStore((state) => state.config);
    const [token, setToken] = useState<string | null>(null);
    const [tokenLoaded, setTokenLoaded] = useState(false);
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [requestSent, setRequestSent] = useState(false);
    const [resetComplete, setResetComplete] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const siteName = config?.site_name || process.env.NEXT_PUBLIC_SITE_NAME || "Lectern";

    useEffect(() => {
        document.title = `${t("resetPasswordTitle")} • ${siteName}`;
    }, [siteName, t]);

    useEffect(() => {
        const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
        const capturedToken = fragment.get("token");
        if (capturedToken) setToken(capturedToken);

        const cleanUrl = new URL(window.location.href);
        const hadLegacyQueryToken = cleanUrl.searchParams.has("token");
        cleanUrl.searchParams.delete("token");
        if (window.location.hash || hadLegacyQueryToken) {
            window.history.replaceState(null, "", `${cleanUrl.pathname}${cleanUrl.search}`);
        }
        setTokenLoaded(true);
    }, []);

    const handleRequest = async (event: FormEvent) => {
        event.preventDefault();
        if (!email.trim()) return;
        setLoading(true);
        setError(null);
        try {
            await apiFetch("/auth/password-reset/request", {
                method: "POST",
                body: JSON.stringify({ email: email.trim() }),
                skipAuth: true,
            });
            setRequestSent(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : t("passwordResetRequestFailed"));
        } finally {
            setLoading(false);
        }
    };

    const handleReset = async (event: FormEvent) => {
        event.preventDefault();
        setError(null);
        if (!token) {
            setError(t("invalidPasswordResetLink"));
            return;
        }
        if (password.length < 8) {
            setError(t("passwordTooShort"));
            return;
        }
        if (password !== confirmPassword) {
            setError(t("passwordMismatch"));
            return;
        }

        setLoading(true);
        try {
            await apiFetch("/auth/password-reset/confirm", {
                method: "POST",
                body: JSON.stringify({ token, password }),
                skipAuth: true,
            });
            setResetComplete(true);
            setToken(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : t("invalidPasswordResetLink"));
        } finally {
            setLoading(false);
        }
    };

    const unavailable = config?.classic_enabled === false;
    const resetting = tokenLoaded && token !== null;

    return (
        <div className="login-page relative flex min-h-screen w-full flex-col items-center justify-center overflow-hidden p-4 sm:p-6">
            <div className="login-bg-base" aria-hidden="true" />
            <div className="login-grain" aria-hidden="true" />
            <div className="login-dots" aria-hidden="true" />

            <div className="login-card-wrapper relative z-10 w-full max-w-[420px] space-y-5 p-6 sm:p-8">
                <div className="text-center -mb-1 -mt-2">
                    <ShaderText
                        text={siteName}
                        style={config?.site_name_style}
                        className="text-3xl font-extrabold tracking-tight text-[#f8f7fc]"
                    />
                    <p className="mx-auto mt-1 max-w-[310px] text-xs font-medium leading-relaxed text-[#918da6] sm:text-[0.8125rem]">
                        {resetting ? t("chooseNewPassword") : t("forgotPasswordDesc")}
                    </p>
                </div>

                <div className="login-sep" aria-hidden="true" />

                {unavailable ? (
                    <div className="space-y-4 text-center">
                        <p className="rounded-md border border-[#1e2030] bg-[#12131d] p-3.5 text-sm text-[#918da6]">
                            {t("passwordResetUnavailable")}
                        </p>
                        <Button asChild className="w-full">
                            <Link href="/login">{t("backToLogin")}</Link>
                        </Button>
                    </div>
                ) : resetComplete ? (
                    <div className="space-y-4 text-center">
                        <p className="rounded-md border border-emerald-900/60 bg-emerald-950/30 p-3.5 text-sm text-emerald-300">
                            {t("passwordResetSuccess")}
                        </p>
                        <Button asChild className="w-full">
                            <Link href="/login">{t("signIn")}</Link>
                        </Button>
                    </div>
                ) : resetting ? (
                    <form onSubmit={handleReset} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="new-password" className="login-overline">
                                {t("newPasswordLabel")}
                            </Label>
                            <div className="relative">
                                <Input
                                    id="new-password"
                                    type={showPassword ? "text" : "password"}
                                    value={password}
                                    onChange={(event) => setPassword(event.target.value)}
                                    minLength={8}
                                    maxLength={128}
                                    required
                                    autoComplete="new-password"
                                    autoFocus
                                    disabled={loading}
                                    className="h-11 pr-10 font-mono text-sm"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowPassword((shown) => !shown)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 p-0.5 text-[#524f64] transition-colors hover:text-[#f0eef5]"
                                    aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                                >
                                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="confirm-password" className="login-overline">
                                {t("confirmPasswordLabel")}
                            </Label>
                            <Input
                                id="confirm-password"
                                type={showPassword ? "text" : "password"}
                                value={confirmPassword}
                                onChange={(event) => setConfirmPassword(event.target.value)}
                                minLength={8}
                                maxLength={128}
                                required
                                autoComplete="new-password"
                                disabled={loading}
                                className="h-11 font-mono text-sm"
                            />
                        </div>
                        {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
                        <Button type="submit" className="h-11 w-full" disabled={loading}>
                            {loading ? t("resettingPassword") : t("resetPassword")}
                        </Button>
                    </form>
                ) : requestSent ? (
                    <div className="space-y-4 text-center">
                        <p className="rounded-md border border-[#1e2030] bg-[#12131d] p-3.5 text-sm leading-6 text-[#918da6]">
                            {t("passwordResetEmailSent")}
                        </p>
                        <Button variant="outline" asChild className="w-full">
                            <Link href="/login">{t("backToLogin")}</Link>
                        </Button>
                    </div>
                ) : (
                    <form onSubmit={handleRequest} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="reset-email" className="login-overline">
                                {t("emailLabel")}
                            </Label>
                            <Input
                                id="reset-email"
                                type="email"
                                value={email}
                                onChange={(event) => setEmail(event.target.value)}
                                placeholder={config?.email_placeholder || t("emailPlaceholder")}
                                required
                                autoFocus
                                autoComplete="email"
                                disabled={loading}
                                className="h-11 text-sm"
                            />
                        </div>
                        {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
                        <Button type="submit" className="h-11 w-full" disabled={loading || !email.trim()}>
                            {loading ? t("sending") : t("sendPasswordResetLink")}
                        </Button>
                        <Button variant="ghost" asChild className="h-9 w-full text-xs text-[#6a667d]">
                            <Link href="/login">{t("backToLogin")}</Link>
                        </Button>
                    </form>
                )}
            </div>
        </div>
    );
}
