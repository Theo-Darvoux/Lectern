"use client";

import { useState } from "react";
import { Mail, Loader2, Send } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { TabsContent } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

interface AuthConfig {
    smtp_host: string | null;
    smtp_ip: string | null;
    smtp_port: number | null;
    smtp_user: string | null;
    smtp_password: string | null;
    smtp_from: string | null;
    smtp_sender_name: string | null;
    smtp_avatar_url: string | null;
    smtp_use_tls: boolean;
}

interface EmailConfigTabProps {
    config: AuthConfig;
}

export function EmailConfigTab({ config }: EmailConfigTabProps) {
    const t = useTranslations("Admin.Config.Email");
    const [testEmail, setTestEmail] = useState("");
    const [testingEmail, setTestingEmail] = useState(false);

    const handleTestEmail = async () => {
        if (!testEmail.trim()) return;
        setTestingEmail(true);
        try {
            await apiFetch("/admin/auth-config/test-email", {
                method: "POST",
                body: JSON.stringify({ email: testEmail }),
            });
            toast.success(t("test.success", { email: testEmail }));
        } catch {
            toast.error(t("test.error"));
        } finally {
            setTestingEmail(false);
        }
    };

    return (
        <TabsContent value="email" className="mt-6 space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Mail className="h-5 w-5 text-primary" />
                        {t("title")}
                    </CardTitle>
                    <CardDescription>
                        {t("descriptionCard")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="smtp_host">{t("host")}</Label>
                            <Input
                                id="smtp_host"
                                readOnly
                                value={config.smtp_host ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_ip">{t("ip")}</Label>
                            <Input
                                id="smtp_ip"
                                readOnly
                                value={config.smtp_ip ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_port">{t("port")}</Label>
                            <Input
                                id="smtp_port"
                                type="number"
                                readOnly
                                value={config.smtp_port ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_user">{t("user")}</Label>
                            <Input
                                id="smtp_user"
                                readOnly
                                value={config.smtp_user ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_password">{t("password")}</Label>
                            <Input
                                id="smtp_password"
                                type="password"
                                readOnly
                                value={config.smtp_password ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_from">{t("from")}</Label>
                            <Input
                                id="smtp_from"
                                readOnly
                                value={config.smtp_from ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label htmlFor="smtp_sender_name">{t("senderName")}</Label>
                            <Input
                                id="smtp_sender_name"
                                readOnly
                                value={config.smtp_sender_name ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                            <p className="text-xs text-muted-foreground">{t("senderNameDescription")}</p>
                        </div>
                    </div>

                    <div className="flex items-start justify-between gap-4 py-4">
                        <div className="flex gap-3">
                            <Mail className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                            <div>
                                <p className="font-medium text-sm leading-none">{t("tls")}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{t("description")}</p>
                            </div>
                        </div>
                        <Switch checked={config.smtp_use_tls} disabled />
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                        <Send className="h-4 w-4 text-muted-foreground" />
                        {t("test.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("test.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex gap-2 max-w-md">
                        <Input
                            placeholder={t("test.placeholder")}
                            value={testEmail}
                            onChange={(e) => setTestEmail(e.target.value)}
                        />
                        <Button
                            variant="outline"
                            onClick={handleTestEmail}
                            disabled={!testEmail || testingEmail}
                        >
                            {testingEmail ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                t("test.button")
                            )}
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </TabsContent>
    );
}
