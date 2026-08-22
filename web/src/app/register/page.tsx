"use client";

import { useState, useEffect, type FormEvent, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { useConfigStore, useUIStore } from "@/lib/stores";
import { cn, sanitizeNext } from "@/lib/utils";
import { Eye, EyeOff } from "lucide-react";
import { ShaderText } from "@/components/shader-text";

type Step = "email" | "code_and_password";

function getNext(): string | null {
    if (typeof window === "undefined") return null;
    return sanitizeNext(new URLSearchParams(window.location.search).get("next"));
}

export default function RegisterPage() {
    const [step, setStep] = useState<Step>("email");
    const [displayName, setDisplayName] = useState("");
    const [email, setEmail] = useState("");
    const [code, setCode] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [resending, setResending] = useState(false);
    
    const { registerWithPassword, requestCode, isAuthenticated, user } = useAuth();
    const config = useConfigStore((state) => state.config);
    const setHideFooter = useUIStore((state) => state.setHideFooter);
    const setNavbarVisible = useUIStore((state) => state.setNavbarVisible);
    const router = useRouter();
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        setHideFooter(true);
        setNavbarVisible(false);
        return () => {
            setHideFooter(false);
            setNavbarVisible(true);
        };
    }, [setHideFooter, setNavbarVisible]);

    useEffect(() => {
        if (isAuthenticated && user?.onboarded) {
            router.replace(getNext() ?? "/browse");
        } else if (isAuthenticated && !user?.onboarded) {
            router.replace("/onboarding");
        }
    }, [isAuthenticated, user, router]);

    const siteName = config?.site_name || process.env.NEXT_PUBLIC_SITE_NAME || "Lectern";

    useEffect(() => {
        document.title = `Sign Up • ${siteName}`;
    }, [siteName]);

    const handleRequestCode = async (e?: FormEvent) => {
        if (e) e.preventDefault();
        if (!email.trim()) return;
        setLoading(true);
        try {
            await requestCode(email.trim());
            setStep("code_and_password");
            setCode("");
            toast.success("Verification code sent");
            setTimeout(() => inputRef.current?.focus(), 150);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to send code");
        } finally {
            setLoading(false);
        }
    };

    const handleResendCode = async () => {
        if (!email.trim() || resending) return;
        setResending(true);
        try {
            await requestCode(email.trim());
            toast.success("Verification code sent");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to resend code");
        } finally {
            setResending(false);
        }
    };

    const handleCodeChange = (val: string) => {
        const cleaned = val.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
        setCode(cleaned);
    };

    const handleCodePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
        e.preventDefault();
        const pasted = e.clipboardData.getData("text");
        const cleaned = pasted.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
        if (cleaned) {
            setCode(cleaned);
        }
    };

    const handleRegister = async (e: FormEvent) => {
        e.preventDefault();
        if (!email.trim() || !password || code.length < 8) return;
        setLoading(true);
        try {
            const data = await registerWithPassword(email.trim(), code, password, displayName.trim());
            if (data.is_new_user || !data.user.onboarded) {
                router.push("/onboarding");
            } else {
                router.push(getNext() ?? "/");
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Registration failed");
        } finally {
            setLoading(false);
        }
    };

    if (!config?.classic_enabled) {
        return (
            <div className="flex min-h-screen items-center justify-center p-4">
                <p className="text-[#828096]">Classic registration is disabled.</p>
            </div>
        );
    }

    return (
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
                        Create your account
                    </p>
                </div>

                <div className="login-sep" aria-hidden="true" />

                {step === "email" ? (
                    <form onSubmit={handleRequestCode} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="email" className="login-overline">
                                Email
                            </Label>
                            <Input
                                id="email"
                                type="email"
                                placeholder={config?.email_placeholder || "name@example.com"}
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                                autoFocus
                                autoComplete="username"
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
                                    Sending...
                                </>
                            ) : (
                                "Send Verification Code"
                            )}
                        </Button>
                    </form>
                ) : (
                    <form onSubmit={handleRegister} className="space-y-4">
                        <div className="flex items-center justify-between p-3 rounded-md bg-[#12131d] border border-[#1e2030] mb-2">
                            <div className="min-w-0">
                                <p className="text-[11px] text-[#6a667d] tracking-wider uppercase font-mono font-semibold">
                                    Registering as
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
                                Change
                            </button>
                        </div>
                        <div className="space-y-3">
                            <div className="flex justify-between items-center px-0.5">
                                <Label htmlFor="code" className="login-overline">
                                    Verification Code
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
                                    Resend Code
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
                                    aria-label="Verification Code"
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
                        </div>

                        <div className="space-y-2 pt-2">
                            <Label htmlFor="displayName" className="login-overline">
                                Display Name (Optional)
                            </Label>
                            <Input
                                id="displayName"
                                type="text"
                                placeholder="How should we call you?"
                                value={displayName}
                                onChange={(e) => setDisplayName(e.target.value)}
                                disabled={loading}
                                className="h-11 text-sm"
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="password" className="login-overline">
                                Password
                            </Label>
                            <div className="relative">
                                <Input
                                    id="password"
                                    type={showPassword ? "text" : "password"}
                                    placeholder="Create a password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    minLength={8}
                                    autoComplete="new-password"
                                    disabled={loading}
                                    className="pr-10 h-11 text-sm font-mono"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowPassword(!showPassword)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[#524f64] hover:text-[#f0eef5] transition-colors p-0.5"
                                    aria-label={showPassword ? "Hide password" : "Show password"}
                                >
                                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                        </div>
                        <Button
                            type="submit"
                            className="w-full h-11 text-sm font-medium tracking-wide bg-[#f0eff5] text-[#08090f] hover:bg-[#dedbe8] transition-colors"
                            disabled={loading || !password || code.length < 8}
                        >
                            {loading ? (
                                <>
                                    <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin mr-2" />
                                    Creating account...
                                </>
                            ) : (
                                "Sign Up"
                            )}
                        </Button>
                    </form>
                )}

                <div className="mt-4 text-center text-sm">
                    <span className="text-[#828096]">Already have an account? </span>
                    <Link href="/login?tab=password" className="text-[#f0eef5] hover:underline transition-colors">
                        Sign In
                    </Link>
                </div>
            </div>
        </div>
    );
}
