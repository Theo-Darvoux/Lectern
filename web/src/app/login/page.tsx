"use client";

import { useState, useEffect, type FormEvent, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { GoogleOAuthProvider, GoogleLogin, CredentialResponse } from "@react-oauth/google";
import { useConfigStore } from "@/lib/stores";
import { cn, sanitizeNext } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SiteName } from "@/components/site-name";
import { Eye, EyeOff, ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import { ShaderText } from "@/components/shader-text";

type Step = "email" | "code" | "password";
type AuthTab = "code" | "password";

/** Read and validate the post-login redirect target from the current URL. */
function getNext(): string | null {
    if (typeof window === "undefined") return null;
    return sanitizeNext(new URLSearchParams(window.location.search).get("next"));
}

export default function LoginPage() {
    const t = useTranslations("Login");
    const [step, setStep] = useState<Step>("email");
    const [authTab, setAuthTab] = useState<AuthTab>("code");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [code, setCode] = useState("");
    const [loading, setLoading] = useState(false);
    const [resending, setResending] = useState(false);
    const { requestCode, verifyCode, verifyGoogleOAuth, loginWithPassword, continueAsGuest, isAuthenticated, user } = useAuth();
    const config = useConfigStore((state) => state.config);
    const router = useRouter();
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isAuthenticated && user?.onboarded) {
            router.replace(getNext() ?? "/browse");
        } else if (isAuthenticated && !user?.onboarded) {
            router.replace("/onboarding");
        }
    }, [isAuthenticated, user, router]);

    // authMethods derived from config
    const authMethods = {
        totp_enabled: config?.totp_enabled ?? true,
        google_enabled: config?.google_enabled ?? false,
        google_client_id: config?.google_client_id ?? null,
        classic_enabled: config?.classic_enabled ?? false,
    };

    // Auto-select active tab based on available methods
    useEffect(() => {
        if (!authMethods.totp_enabled && authMethods.classic_enabled) {
            setAuthTab("password");
        } else if (authMethods.totp_enabled) {
            setAuthTab("code");
        }
    }, [authMethods.totp_enabled, authMethods.classic_enabled]);

    const handleRequestCode = async (e?: FormEvent) => {
        if (e) e.preventDefault();
        if (!email.trim()) return;
        setLoading(true);
        try {
            await requestCode(email.trim());
            setStep("code");
            setCode("");
            toast.success(t("codeSent"));
            setTimeout(() => inputRef.current?.focus(), 150);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("sendFailed"));
        } finally {
            setLoading(false);
        }
    };

    const handleResendCode = async () => {
        if (!email.trim() || resending) return;
        setResending(true);
        try {
            await requestCode(email.trim());
            toast.success(t("codeSent"));
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("sendFailed"));
        } finally {
            setResending(false);
        }
    };

    const executeVerifyCode = useCallback(async (codeToVerify: string) => {
        if (!codeToVerify.trim() || codeToVerify.length < 8) return;
        setLoading(true);
        try {
            const data = await verifyCode(email.trim(), codeToVerify.trim());
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push(getNext() ?? "/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("invalidCode"));
        } finally {
            setLoading(false);
        }
    }, [email, router, t, verifyCode]);

    const handleVerifyCodeSubmit = async (e: FormEvent) => {
        e.preventDefault();
        await executeVerifyCode(code);
    };

    const handleCodeChange = (val: string) => {
        const cleaned = val.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
        setCode(cleaned);
        if (cleaned.length === 8) {
            void executeVerifyCode(cleaned);
        }
    };

    const handleCodePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
        e.preventDefault();
        const pasted = e.clipboardData.getData("text");
        const cleaned = pasted.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
        if (cleaned) {
            setCode(cleaned);
            if (cleaned.length === 8) {
                void executeVerifyCode(cleaned);
            }
        }
    };

    const handlePasswordLogin = async (e: FormEvent) => {
        e.preventDefault();
        if (!email.trim() || !password) return;
        setLoading(true);
        try {
            const data = await loginWithPassword(email.trim(), password);
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push(getNext() ?? "/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("invalidCredentials"));
        } finally {
            setLoading(false);
        }
    };

    const handleGuest = async () => {
        setLoading(true);
        try {
            await continueAsGuest();
            router.push(getNext() ?? "/browse");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("guestFailed"));
        } finally {
            setLoading(false);
        }
    };

    const handleGoogleSuccess = async (credentialResponse: CredentialResponse) => {
        if (!credentialResponse.credential) return;
        setLoading(true);
        try {
            const data = await verifyGoogleOAuth(credentialResponse.credential);
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push(getNext() ?? "/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("googleFailed"));
        } finally {
            setLoading(false);
        }
    };

    const hasAnyAuthMethod =
        authMethods.totp_enabled ||
        authMethods.google_enabled ||
        authMethods.classic_enabled ||
        config?.guest_access_enabled;

    const emailPlaceholder = config?.email_placeholder || t("emailPlaceholder");

    return (
        <div className="login-page relative flex min-h-screen w-full flex-col items-center justify-center p-4 sm:p-6 overflow-hidden">
            {/* Background layers: solid matte base + fine grain texture + dot matrix */}
            <div className="login-bg-base" aria-hidden="true" />
            <div className="login-grain" aria-hidden="true" />
            <div className="login-dots" aria-hidden="true" />

            {/* Auth Card - Simple, elegant, responsive */}
            <div className="login-card-wrapper relative z-10 w-full max-w-[420px] p-6 sm:p-8 space-y-5">
                
                {/* Header: 90s 3D Chrome Shader Title (Fills top space inside card) */}
                <div className="text-center -mt-2 -mb-1">
                    {config?.site_logo_url && (
                        <div className="flex justify-center mb-1">
                            <Image
                                src={config.site_logo_url}
                                alt={config?.site_name || "Logo"}
                                width={40}
                                height={40}
                                className="h-10 w-auto object-contain"
                                unoptimized
                            />
                        </div>
                    )}

                    <ShaderText
                        text={config?.site_name || t("title") || "Lectern"}
                        className="text-3xl font-extrabold tracking-tight text-[#f8f7fc]"
                    />

                    {step !== "email" && (
                        <p className="text-xs sm:text-[0.8125rem] font-medium tracking-[0.015em] text-[#918da6] leading-relaxed max-w-[310px] mx-auto mt-1">
                            {step === "code" ? t("descCode") : t("descPassword")}
                        </p>
                    )}
                </div>

                <div className="login-sep" aria-hidden="true" />

                {/* STEP 1: EMAIL & AUTH METHOD SELECTION */}
                {step === "email" ? (
                    <div className="space-y-4">
                        {/* Google OAuth Provider */}
                        {authMethods.google_enabled && (
                            <div className="space-y-4">
                                <div className="flex justify-center w-full min-h-[44px]">
                                    {authMethods.google_client_id ? (
                                        <GoogleOAuthProvider clientId={authMethods.google_client_id}>
                                            <div className="w-full flex justify-center [&>div]:w-full">
                                                <GoogleLogin
                                                    onSuccess={handleGoogleSuccess}
                                                    onError={() => toast.error(t("googleFailed"))}
                                                    theme="filled_black"
                                                    size="large"
                                                    width="100%"
                                                    shape="rectangular"
                                                    context="signin"
                                                />
                                            </div>
                                        </GoogleOAuthProvider>
                                    ) : (
                                        <div className="h-11 w-full bg-[#12131d] animate-pulse rounded-md" />
                                    )}
                                </div>

                                {(authMethods.totp_enabled || authMethods.classic_enabled) && (
                                    <div className="relative w-full py-1">
                                        <div className="absolute inset-0 flex items-center">
                                            <span className="w-full border-t border-[#1a1c29]" />
                                        </div>
                                        <div className="relative flex justify-center">
                                            <span className="login-overline bg-[#0c0d14] px-2.5">
                                                {t("orContinue")}
                                            </span>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Dual Methods: Email Code vs Password */}
                        {authMethods.totp_enabled && authMethods.classic_enabled ? (
                            <Tabs
                                value={authTab}
                                onValueChange={(val) => setAuthTab(val as AuthTab)}
                                className="w-full space-y-4"
                            >
                                <TabsList className="grid w-full grid-cols-2 p-1 bg-[#12131d] rounded-md border border-[#1e2030]">
                                    <TabsTrigger
                                        value="code"
                                        className="rounded text-xs font-medium py-1.5 transition-colors data-[state=active]:bg-[#1d1f30] data-[state=active]:text-[#f0eef5]"
                                    >
                                        {t("emailCodeTab")}
                                    </TabsTrigger>
                                    <TabsTrigger
                                        value="password"
                                        className="rounded text-xs font-medium py-1.5 transition-colors data-[state=active]:bg-[#1d1f30] data-[state=active]:text-[#f0eef5]"
                                    >
                                        {t("passwordTab")}
                                    </TabsTrigger>
                                </TabsList>

                                {/* Verification Code Tab */}
                                <TabsContent value="code" className="space-y-4">
                                    <form onSubmit={handleRequestCode} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="email-code" className="login-overline">
                                                {t("emailLabel")}
                                            </Label>
                                            <Input
                                                id="email-code"
                                                type="email"
                                                placeholder={emailPlaceholder}
                                                value={email}
                                                onChange={(e) => setEmail(e.target.value)}
                                                required
                                                autoFocus
                                                autoComplete="email"
                                                disabled={loading}
                                                className="h-11 text-sm"
                                            />
                                        </div>
                                        <Button
                                            type="submit"
                                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                                            disabled={loading || !email.trim()}
                                        >
                                            {loading ? (
                                                <>
                                                    <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                                    {t("sending")}
                                                </>
                                            ) : (
                                                t("sendVerificationCode")
                                            )}
                                        </Button>
                                    </form>
                                </TabsContent>

                                {/* Password Tab */}
                                <TabsContent value="password" className="space-y-4">
                                    <form onSubmit={handlePasswordLogin} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="email-pass" className="login-overline">
                                                {t("emailLabel")}
                                            </Label>
                                            <Input
                                                id="email-pass"
                                                type="email"
                                                placeholder={emailPlaceholder}
                                                value={email}
                                                onChange={(e) => setEmail(e.target.value)}
                                                required
                                                autoComplete="username"
                                                disabled={loading}
                                                className="h-11 text-sm"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="password-tab-input" className="login-overline">
                                                {t("passwordLabel")}
                                            </Label>
                                            <div className="relative">
                                                <Input
                                                    id="password-tab-input"
                                                    type={showPassword ? "text" : "password"}
                                                    placeholder={t("passwordPlaceholder")}
                                                    value={password}
                                                    onChange={(e) => setPassword(e.target.value)}
                                                    required
                                                    autoComplete="current-password"
                                                    disabled={loading}
                                                    className="pr-10 h-11 text-sm font-mono"
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => setShowPassword(!showPassword)}
                                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[#524f64] hover:text-[#f0eef5] transition-colors p-0.5"
                                                    aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                                                >
                                                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                                </button>
                                            </div>
                                        </div>
                                        <Button
                                            type="submit"
                                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                                            disabled={loading || !email.trim() || !password}
                                        >
                                            {loading ? (
                                                <>
                                                    <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                                    {t("signingIn")}
                                                </>
                                            ) : (
                                                t("signIn")
                                            )}
                                        </Button>
                                    </form>
                                </TabsContent>
                            </Tabs>
                        ) : authMethods.totp_enabled ? (
                            /* TOTP Only */
                            <form onSubmit={handleRequestCode} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="email-single" className="login-overline">
                                        {t("emailLabel")}
                                    </Label>
                                    <Input
                                        id="email-single"
                                        type="email"
                                        placeholder={emailPlaceholder}
                                        value={email}
                                        onChange={(e) => setEmail(e.target.value)}
                                        required
                                        autoFocus
                                        autoComplete="email"
                                        disabled={loading}
                                        className="h-11 text-sm"
                                    />
                                </div>
                                <Button
                                    type="submit"
                                    className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                                    disabled={loading || !email.trim()}
                                >
                                    {loading ? (
                                        <>
                                            <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                            {t("sending")}
                                        </>
                                    ) : (
                                        t("sendVerificationCode")
                                    )}
                                </Button>
                            </form>
                        ) : authMethods.classic_enabled ? (
                            /* Classic Password Only */
                            <form onSubmit={handlePasswordLogin} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="email-classic" className="login-overline">
                                        {t("emailLabel")}
                                    </Label>
                                    <Input
                                        id="email-classic"
                                        type="email"
                                        placeholder={emailPlaceholder}
                                        value={email}
                                        onChange={(e) => setEmail(e.target.value)}
                                        required
                                        autoFocus
                                        autoComplete="username"
                                        disabled={loading}
                                        className="h-11 text-sm"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="password-classic" className="login-overline">
                                        {t("passwordLabel")}
                                    </Label>
                                    <div className="relative">
                                        <Input
                                            id="password-classic"
                                            type={showPassword ? "text" : "password"}
                                            placeholder={t("passwordPlaceholder")}
                                            value={password}
                                            onChange={(e) => setPassword(e.target.value)}
                                            required
                                            autoComplete="current-password"
                                            disabled={loading}
                                            className="pr-10 h-11 text-sm font-mono"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowPassword(!showPassword)}
                                            className="absolute right-3 top-1/2 -translate-y-1/2 text-[#524f64] hover:text-[#f0eef5] transition-colors p-0.5"
                                            aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                                        >
                                            {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                        </button>
                                    </div>
                                </div>
                                <Button
                                    type="submit"
                                    className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                                    disabled={loading || !email.trim() || !password}
                                >
                                    {loading ? (
                                        <>
                                            <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                            {t("signingIn")}
                                        </>
                                    ) : (
                                        t("signIn")
                                    )}
                                </Button>
                            </form>
                        ) : null}

                        {!hasAnyAuthMethod && (
                            <p className="text-sm text-center text-[#828096] py-4">
                                {t("noMethods")}
                            </p>
                        )}

                        {/* Guest Access Section */}
                        {config?.guest_access_enabled && (
                            <div className="pt-2">
                                {(authMethods.totp_enabled || authMethods.google_enabled || authMethods.classic_enabled) && (
                                    <div className="relative w-full py-2">
                                        <div className="absolute inset-0 flex items-center">
                                            <span className="w-full border-t border-[#1a1c29]" />
                                        </div>
                                        <div className="relative flex justify-center">
                                            <span className="login-overline bg-[#0c0d14] px-2.5">
                                                {t("orContinue")}
                                            </span>
                                        </div>
                                    </div>
                                )}
                                <Button
                                    type="button"
                                    variant="outline"
                                    className="w-full h-11 text-sm font-medium border-[#1c1e2b] bg-[#12131d] hover:bg-[#1a1c29] text-[#dedbe8] transition-colors"
                                    onClick={handleGuest}
                                    disabled={loading}
                                >
                                    {t("continueAsGuest")}
                                </Button>
                            </div>
                        )}
                    </div>
                ) : step === "password" ? (
                    /* STEP 2: PASSWORD FALLBACK */
                    <form onSubmit={handlePasswordLogin} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="password" className="login-overline">
                                {t("passwordLabel")}
                            </Label>
                            <div className="relative">
                                <Input
                                    id="password"
                                    type={showPassword ? "text" : "password"}
                                    placeholder={t("passwordPlaceholder")}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    autoFocus
                                    autoComplete="current-password"
                                    disabled={loading}
                                    className="pr-10 h-11 text-sm font-mono"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowPassword(!showPassword)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[#524f64] hover:text-[#f0eef5] transition-colors p-0.5"
                                    aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                                >
                                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                        </div>
                        <Button
                            type="submit"
                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                            disabled={loading || !password}
                        >
                            {loading ? (
                                <>
                                    <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                    {t("signingIn")}
                                </>
                            ) : (
                                t("signIn")
                            )}
                        </Button>
                        <Button
                            type="button"
                            variant="ghost"
                            className="w-full h-9 text-xs text-[#6a667d] hover:text-[#f0eef5]"
                            onClick={() => setStep("email")}
                            disabled={loading}
                        >
                            {t("back")}
                        </Button>
                    </form>
                ) : (
                    /* STEP 3: OTP VERIFICATION CODE */
                    <form onSubmit={handleVerifyCodeSubmit} className="space-y-5">
                        <div className="flex items-center justify-between p-3 rounded-md bg-[#12131d] border border-[#1e2030]">
                            <div className="min-w-0">
                                <p className="text-[11px] text-[#6a667d] tracking-wider uppercase font-mono font-semibold">
                                    {t("enterCodeSentTo")}
                                </p>
                                <p className="text-sm font-medium truncate text-[#f0eef5] mt-0.5">
                                    {email}
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={() => {
                                    setStep("email");
                                    setCode("");
                                }}
                                className="text-xs text-[#828096] hover:text-[#f0eef5] font-medium px-2 py-1 shrink-0 transition-colors"
                            >
                                {t("changeEmail")}
                            </button>
                        </div>

                        <div className="space-y-3">
                            <div className="flex justify-between items-center px-0.5">
                                <Label htmlFor="code" className="login-overline">
                                    {t("verificationCode")}
                                </Label>
                                <button
                                    type="button"
                                    className="text-xs text-[#828096] hover:text-[#f0eef5] font-medium flex items-center gap-1.5 transition-colors"
                                    onClick={handleResendCode}
                                    disabled={loading || resending}
                                >
                                    {resending && (
                                        <span className="inline-block h-3 w-3 rounded-full border-[1.5px] border-current border-r-transparent animate-spin" />
                                    )}
                                    {t("resendCode")}
                                </button>
                            </div>

                            <div
                                className="relative cursor-text select-none"
                                onClick={() => inputRef.current?.focus()}
                            >
                                <input
                                    ref={inputRef}
                                    id="code"
                                    type="text"
                                    maxLength={8}
                                    value={code}
                                    onChange={(e) => handleCodeChange(e.target.value)}
                                    onPaste={handleCodePaste}
                                    className="absolute inset-0 opacity-0 cursor-text w-full h-full"
                                    autoFocus
                                    required
                                    autoComplete="one-time-code"
                                    aria-label={t("codeAriaLabel")}
                                    disabled={loading}
                                />

                                <div className="grid grid-cols-8 gap-1" aria-hidden="true">
                                    {[...Array(8)].map((_, i) => (
                                        <div
                                            key={i}
                                            className={cn(
                                                "flex h-11 items-center justify-center rounded-md border text-base font-mono font-bold transition-colors",
                                                code.length === i && !loading
                                                    ? "border-[#4a4e70] bg-[#1a1c2b] text-[#f0eef5]"
                                                    : "border-[#1e2030] bg-[#12131d] text-[#828096]",
                                                code[i] && "border-[#2d3047] bg-[#161826] text-[#f0eef5]"
                                            )}
                                        >
                                            {code[i] || ""}
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="rounded-md bg-[#12131d] p-2.5 border border-[#1a1c29] text-center text-xs text-[#6a667d] leading-relaxed">
                                <p>{t("codeHelp")}</p>
                            </div>
                        </div>

                        <div className="space-y-2.5">
                            <Button
                                type="submit"
                                className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                                disabled={loading || code.length < 8}
                            >
                                {loading ? (
                                    <>
                                        <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                        {t("verifying")}
                                    </>
                                ) : (
                                    t("verify")
                                )}
                            </Button>

                            <Button
                                type="button"
                                variant="outline"
                                className="w-full h-11 text-sm font-medium border-[#1c1e2b] bg-[#12131d] hover:bg-[#1a1c29] text-[#dedbe8] transition-colors flex items-center justify-center gap-2"
                                onClick={() => window.open("https://cerbere.imt.fr/zimbra", "_blank")}
                                disabled={loading}
                            >
                                <span>{t("openZimbra")}</span>
                                <ExternalLink className="h-3.5 w-3.5 text-[#828096]" />
                            </Button>

                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full h-9 text-xs text-[#6a667d] hover:text-[#f0eef5]"
                                onClick={() => {
                                    setStep("email");
                                    setCode("");
                                }}
                                disabled={loading}
                            >
                                {t("useDifferentEmail")}
                            </Button>
                        </div>
                    </form>
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
