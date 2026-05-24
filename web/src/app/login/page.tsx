"use client";

import { useState, useEffect, type FormEvent, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { GoogleOAuthProvider, GoogleLogin, CredentialResponse } from "@react-oauth/google";
import { useConfigStore } from "@/lib/stores";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Eye, KeyRound, Mail } from "lucide-react";

type Step = "email" | "code" | "password";

import { useTranslations } from "next-intl";

export default function LoginPage() {
    const t = useTranslations("Login");
    const [step, setStep] = useState<Step>("email");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [code, setCode] = useState("");
    const [loading, setLoading] = useState(false);
    const { requestCode, verifyCode, verifyGoogleOAuth, loginWithPassword, continueAsGuest, isAuthenticated, user } = useAuth();
    const { config } = useConfigStore();
    const router = useRouter();
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isAuthenticated && user?.onboarded) {
            router.replace("/browse");
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


    const handleRequestCode = async (e?: FormEvent) => {
        if (e) e.preventDefault();
        setLoading(true);
        try {
            await requestCode(email);
            setStep("code");
            setCode("");
            toast.success(t("codeSent"));
            setTimeout(() => inputRef.current?.focus(), 100);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("sendFailed"));
        } finally {
            setLoading(false);
        }
    };

    const handleVerifyCode = async (e: FormEvent) => {
        e.preventDefault();
        setLoading(true);
        try {
            const data = await verifyCode(email, code);
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push("/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("invalidCode"));
        } finally {
            setLoading(false);
        }
    };

    const handlePasswordLogin = async (e: FormEvent) => {
        e.preventDefault();
        setLoading(true);
        try {
            const data = await loginWithPassword(email, password);
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push("/");
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
            router.push("/browse");
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
                router.push("/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("googleFailed"));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
            <Card className="w-full max-w-md">
                <CardHeader className="text-center">
                    <CardTitle className="text-2xl font-bold">{config?.site_name || t("title")}</CardTitle>
                    <CardDescription>
                        {step === "email"
                            ? (config?.site_description || t("descEmail"))
                            : step === "code" ? t("descCode") : t("descPassword")}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {step === "email" ? (
                        <div className="space-y-6">
                            {authMethods.google_enabled && (
                                <div className="flex flex-col gap-4 items-center">
                                    <div className="w-full flex justify-center">
                                        {authMethods.google_client_id ? (
                                            <GoogleOAuthProvider clientId={authMethods.google_client_id}>
                                                <GoogleLogin
                                                    onSuccess={handleGoogleSuccess}
                                                    onError={() => toast.error(t("googleFailed"))}
                                                    theme="outline"
                                                    size="large"
                                                    width="100%"
                                                    shape="rectangular"
                                                    context="signin"
                                                />
                                            </GoogleOAuthProvider>
                                        ) : (
                                            <div className="h-[44px] w-full bg-muted animate-pulse rounded-md" />
                                        )}
                                    </div>
                                    {(authMethods.totp_enabled || authMethods.classic_enabled) && (
                                        <div className="relative w-full">
                                            <div className="absolute inset-0 flex items-center">
                                                <span className="w-full border-t" />
                                            </div>
                                            <div className="relative flex justify-center text-xs uppercase">
                                                <span className="bg-card px-2 text-muted-foreground">{t("orContinue")}</span>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {(authMethods.totp_enabled || authMethods.classic_enabled) && (
                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="email">{t("emailLabel")}</Label>
                                        <Input
                                            id="email"
                                            type="email"
                                            placeholder={t("emailPlaceholder")}
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            required
                                            autoFocus
                                            disabled={loading}
                                        />
                                    </div>

                                    {authMethods.totp_enabled && authMethods.classic_enabled ? (
                                        <div className="grid grid-cols-2 gap-4">
                                            <Button variant="outline" onClick={handleRequestCode} disabled={loading || !email}>
                                                <Mail className="mr-2 h-4 w-4" />
                                                {t("sendCode")}
                                            </Button>
                                            <Button variant="outline" onClick={() => setStep("password")} disabled={loading || !email}>
                                                <KeyRound className="mr-2 h-4 w-4" />
                                                {t("password")}
                                            </Button>
                                        </div>
                                    ) : authMethods.totp_enabled ? (
                                        <Button className="w-full" onClick={handleRequestCode} disabled={loading || !email}>
                                            {loading ? t("sending") : t("sendVerificationCode")}
                                        </Button>
                                    ) : (
                                        <Button className="w-full" onClick={() => setStep("password")} disabled={loading || !email}>
                                            {t("continueWithPassword")}
                                        </Button>
                                    )}
                                </div>
                            )}

                            {!authMethods.totp_enabled && !authMethods.google_enabled && !authMethods.classic_enabled && !config?.guest_access_enabled && (
                                <p className="text-sm text-center text-muted-foreground py-4">
                                    {t("noMethods")}
                                </p>
                            )}

                            {config?.guest_access_enabled && (
                                <div className="space-y-4">
                                    {(authMethods.totp_enabled || authMethods.google_enabled || authMethods.classic_enabled) && (
                                        <div className="relative w-full">
                                            <div className="absolute inset-0 flex items-center">
                                                <span className="w-full border-t" />
                                            </div>
                                            <div className="relative flex justify-center text-xs uppercase">
                                                <span className="bg-card px-2 text-muted-foreground">{t("orContinue")}</span>
                                            </div>
                                        </div>
                                    )}
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        className="w-full"
                                        onClick={handleGuest}
                                        disabled={loading}
                                    >
                                        <Eye className="mr-2 h-4 w-4" />
                                        {t("continueAsGuest")}
                                    </Button>
                                </div>
                            )}
                        </div>
                    ) : step === "password" ? (
                        <form onSubmit={handlePasswordLogin} className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="password">{t("passwordLabel")}</Label>
                                <Input
                                    id="password"
                                    type="password"
                                    placeholder={t("passwordPlaceholder")}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    autoFocus
                                    disabled={loading}
                                />
                            </div>
                            <Button type="submit" className="w-full" disabled={loading}>
                                {loading ? t("signingIn") : t("signIn")}
                            </Button>
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setStep("email")}
                                disabled={loading}
                            >
                                {t("back")}
                            </Button>
                        </form>
                    ) : (
                        <form onSubmit={handleVerifyCode} className="space-y-6">
                            <div className="space-y-3">
                                <div className="flex justify-between items-center px-1">
                                    <Label htmlFor="code" className="block">{t("verificationCode")}</Label>
                                    <button 
                                        type="button" 
                                        className="text-xs text-primary hover:underline font-medium"
                                        onClick={() => handleRequestCode()}
                                        disabled={loading}
                                    >
                                        {t("resendCode")}
                                    </button>
                                </div>
                                <div className="relative cursor-text" onClick={() => inputRef.current?.focus()}>
                                    {/* Invisible actual input - accessible to screen readers */}
                                    <input
                                        ref={inputRef}
                                        id="code"
                                        type="text"
                                        maxLength={8}
                                        value={code}
                                        onChange={(e) => setCode(e.target.value.toUpperCase())}
                                        className="absolute inset-0 opacity-0 cursor-text"
                                        autoFocus
                                        required
                                        autoComplete="one-time-code"
                                        aria-label={t("codeAriaLabel")}
                                        disabled={loading}
                                    />
                                    {/* Visual representation - hidden from screen readers */}
                                    <div className="flex justify-between gap-2" aria-hidden="true">
                                        {[...Array(8)].map((_, i) => (
                                            <div
                                                key={i}
                                                className={`
                                                    flex h-12 w-10 items-center justify-center rounded-md border-2 text-lg font-bold transition-all
                                                    ${code.length === i && !loading ? "border-primary ring-2 ring-primary/20 scale-105" : "border-muted"}
                                                    ${code[i] ? "border-primary/50 bg-primary/5" : ""}
                                                `}
                                            >
                                                {code[i] || ""}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <p className="text-center text-xs text-muted-foreground">
                                    {t("codeHelp")}
                                    <br/>
                                    <span className="opacity-80">{t("codeHelp2")}</span>
                                </p>
                            </div>
                            <Button type="submit" className="w-full" disabled={loading}>
                                {loading ? t("verifying") : t("verify")}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                className="w-full"
                                onClick={() => window.open("https://cerbere.imt.fr/zimbra", "_blank")}
                                disabled={loading}
                            >
                                {t("openZimbra")}
                            </Button>
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => {
                                    setStep("email");
                                    setCode("");
                                }}
                                disabled={loading}
                            >
                                {t("useDifferentEmail")}
                            </Button>
                        </form>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
