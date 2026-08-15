"use client";

import { useState, useEffect, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { useConfigStore } from "@/lib/stores";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function SetupPage() {
    const t = useTranslations("Setup");
    const router = useRouter();
    const { setup } = useAuth();
    const config = useConfigStore((state) => state.config);
    const updateConfig = useConfigStore((state) => state.updateConfig);

    const [email, setEmail] = useState("");
    const [confirmEmail, setConfirmEmail] = useState("");
    const [displayName, setDisplayName] = useState("");
    const [bootstrapToken, setBootstrapToken] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [loading, setLoading] = useState(false);

    // Setup is only reachable on a fresh instance. Once an admin exists, bounce to login.
    useEffect(() => {
        if (config && !config.needs_setup) {
            router.replace("/login");
        }
    }, [config, router]);

    const handleSubmit = async (e: FormEvent) => {
        e.preventDefault();
        if (email.trim().toLowerCase() !== confirmEmail.trim().toLowerCase()) {
            toast.error(t("emailMismatch"));
            return;
        }
        if (password.length < 8) {
            toast.error(t("passwordTooShort"));
            return;
        }
        if (password !== confirm) {
            toast.error(t("passwordMismatch"));
            return;
        }
        setLoading(true);
        try {
            await setup(email, password, displayName, bootstrapToken);
            // Mark setup complete in the local config so ConfigProvider doesn't bounce us back.
            updateConfig({ needs_setup: false });
            toast.success(t("success"));
            router.replace("/");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failed"));
        } finally {
            setLoading(false);
        }
    };

    const siteName = config?.site_name ?? "Lectern";

    return (
        <div className="flex min-h-svh items-center justify-center p-4">
            <Card className="w-full max-w-md">
                <CardHeader>
                    <CardTitle>{t("title", { siteName })}</CardTitle>
                    <CardDescription>{t("description")}</CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        {config?.bootstrap_token_required && (
                            <div className="space-y-2">
                                <Label htmlFor="bootstrapToken">{t("bootstrapTokenLabel")}</Label>
                                <Input
                                    id="bootstrapToken"
                                    type="password"
                                    required
                                    autoComplete="off"
                                    value={bootstrapToken}
                                    onChange={(e) => setBootstrapToken(e.target.value)}
                                    placeholder={t("bootstrapTokenPlaceholder")}
                                />
                                <p className="text-xs text-muted-foreground">
                                    {t("bootstrapTokenHint")}
                                </p>
                            </div>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="email">{t("emailLabel")}</Label>
                            <Input
                                id="email"
                                type="email"
                                required
                                autoComplete="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                placeholder={t("emailPlaceholder")}
                            />
                            <p className="text-xs text-muted-foreground">{t("emailHint")}</p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="confirmEmail">{t("confirmEmailLabel")}</Label>
                            <Input
                                id="confirmEmail"
                                type="email"
                                required
                                autoComplete="email"
                                value={confirmEmail}
                                onChange={(e) => setConfirmEmail(e.target.value)}
                                placeholder={t("confirmEmailPlaceholder")}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="displayName">{t("displayNameLabel")}</Label>
                            <Input
                                id="displayName"
                                type="text"
                                autoComplete="name"
                                value={displayName}
                                onChange={(e) => setDisplayName(e.target.value)}
                                placeholder={t("displayNamePlaceholder")}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password">{t("passwordLabel")}</Label>
                            <Input
                                id="password"
                                type="password"
                                required
                                autoComplete="new-password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                placeholder={t("passwordPlaceholder")}
                            />
                            <p className="text-xs text-muted-foreground">{t("passwordHint")}</p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="confirm">{t("confirmLabel")}</Label>
                            <Input
                                id="confirm"
                                type="password"
                                required
                                autoComplete="new-password"
                                value={confirm}
                                onChange={(e) => setConfirm(e.target.value)}
                                placeholder={t("confirmPlaceholder")}
                            />
                        </div>
                        <Button type="submit" className="w-full" disabled={loading}>
                            {loading ? t("creating") : t("submit")}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
