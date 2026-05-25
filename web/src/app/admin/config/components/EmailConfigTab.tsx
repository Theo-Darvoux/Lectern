"use client";

import { useState, useEffect, useRef } from "react";
import { Mail, Loader2, Save, Send, Upload, Download, X } from "lucide-react";
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
    saving: boolean;
    patchConfig: (patch: Partial<AuthConfig>) => Promise<void>;
}

function ToggleRow({
    label,
    description,
    checked,
    disabled,
    onToggle,
    icon: Icon,
}: {
    label: string;
    description: string;
    checked: boolean;
    disabled?: boolean;
    onToggle: () => void;
    icon: React.ElementType;
}) {
    return (
        <div className="flex items-start justify-between gap-4 py-4">
            <div className="flex gap-3">
                <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div>
                    <p className="font-medium text-sm leading-none">{label}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{description}</p>
                </div>
            </div>
            <Switch
                checked={checked}
                disabled={disabled}
                onCheckedChange={onToggle}
            />
        </div>
    );
}

interface AvatarUploadFieldProps {
    label: string;
    description: string;
    currentUrl: string | null;
    onUploaded: (url: string) => void;
    onClear: () => void;
}

function AvatarUploadField({ label, description, currentUrl, onUploaded, onClear }: AvatarUploadFieldProps) {
    const [uploading, setUploading] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const handleFile = async (file: File) => {
        setUploading(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { url } = await apiFetch<{ url: string }>("/admin/auth-config/upload-email-avatar", {
                method: "POST",
                body: fd,
            });
            onUploaded(url);
            toast.success(`${label} uploaded`);
        } catch (e: unknown) {
            toast.error(e instanceof Error ? e.message : "Upload failed");
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="space-y-2">
            <Label>{label}</Label>
            <p className="text-xs text-muted-foreground">{description}</p>
            <div className="flex items-center gap-4">
                {currentUrl ? (
                    <div className="relative group flex-shrink-0">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={currentUrl}
                            alt={label}
                            className="h-14 w-14 rounded-full object-cover border bg-muted/30"
                        />
                        <button
                            type="button"
                            onClick={onClear}
                            className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 transition-opacity shadow"
                        >
                            <X className="h-3 w-3" />
                        </button>
                    </div>
                ) : (
                    <div className="h-14 w-14 rounded-full border-2 border-dashed border-muted-foreground/30 bg-muted/20 flex items-center justify-center flex-shrink-0">
                        <Upload className="h-5 w-5 text-muted-foreground/50" />
                    </div>
                )}
                <div className="flex flex-col gap-1.5">
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        disabled={uploading}
                        onClick={() => inputRef.current?.click()}
                    >
                        {uploading ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Upload className="h-4 w-4" />
                        )}
                        {currentUrl ? "Replace" : "Upload"}
                    </Button>
                    {currentUrl && (
                        <a
                            href={currentUrl}
                            download
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                        >
                            <Download className="h-3 w-3" />
                            Download
                        </a>
                    )}
                </div>
            </div>
            <input
                ref={inputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                className="hidden"
                onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFile(file);
                    e.target.value = "";
                }}
            />
        </div>
    );
}

export function EmailConfigTab({ config, saving, patchConfig }: EmailConfigTabProps) {
    const t = useTranslations("Admin.Config.Email");
    const [emailForm, setEmailForm] = useState<Partial<AuthConfig>>({});
    const [isEmailModified, setIsEmailModified] = useState(false);
    const [testEmail, setTestEmail] = useState("");
    const [testingEmail, setTestingEmail] = useState(false);

    useEffect(() => {
        setEmailForm({
            smtp_host: config.smtp_host,
            smtp_ip: config.smtp_ip,
            smtp_port: config.smtp_port,
            smtp_user: config.smtp_user,
            smtp_password: config.smtp_password,
            smtp_from: config.smtp_from,
            smtp_sender_name: config.smtp_sender_name,
            smtp_avatar_url: config.smtp_avatar_url,
            smtp_use_tls: config.smtp_use_tls,
        });
        setIsEmailModified(false);
    }, [config]);

    const handleSave = async () => {
        await patchConfig(emailForm);
        toast.success(t("success"));
        setIsEmailModified(false);
    };

    const handleDiscard = () => {
        setEmailForm({
            smtp_host: config.smtp_host,
            smtp_ip: config.smtp_ip,
            smtp_port: config.smtp_port,
            smtp_user: config.smtp_user,
            smtp_password: config.smtp_password,
            smtp_from: config.smtp_from,
            smtp_sender_name: config.smtp_sender_name,
            smtp_avatar_url: config.smtp_avatar_url,
            smtp_use_tls: config.smtp_use_tls,
        });
        setIsEmailModified(false);
    };

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
                                placeholder={t("placeholders.host")}
                                value={emailForm.smtp_host || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_host: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_ip">{t("ip")}</Label>
                            <Input
                                id="smtp_ip"
                                placeholder={t("placeholders.ip", { defaultValue: "1.2.3.4" })}
                                value={emailForm.smtp_ip || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_ip: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_port">{t("port")}</Label>
                            <Input
                                id="smtp_port"
                                type="number"
                                placeholder={t("placeholders.port")}
                                value={emailForm.smtp_port ?? ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_port: parseInt(e.target.value) || 0 }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_user">{t("user")}</Label>
                            <Input
                                id="smtp_user"
                                placeholder={t("placeholders.user")}
                                value={emailForm.smtp_user || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_user: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_password">{t("password")}</Label>
                            <Input
                                id="smtp_password"
                                type="password"
                                placeholder={t("placeholders.password")}
                                autoComplete="off"
                                value={emailForm.smtp_password || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_password: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="smtp_from">{t("from")}</Label>
                            <Input
                                id="smtp_from"
                                placeholder={t("placeholders.from")}
                                value={emailForm.smtp_from || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_from: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label htmlFor="smtp_sender_name">{t("senderName")}</Label>
                            <Input
                                id="smtp_sender_name"
                                placeholder={t("placeholders.senderName")}
                                value={emailForm.smtp_sender_name || ""}
                                onChange={(e) => {
                                    setEmailForm((prev) => ({ ...prev, smtp_sender_name: e.target.value }));
                                    setIsEmailModified(true);
                                }}
                            />
                            <p className="text-xs text-muted-foreground">{t("senderNameDescription")}</p>
                        </div>
                    </div>

                    <ToggleRow
                        icon={Mail}
                        label={t("tls")}
                        description={t("description")}
                        checked={emailForm.smtp_use_tls ?? config.smtp_use_tls}
                        onToggle={() => {
                            setEmailForm((prev) => ({
                                ...prev,
                                smtp_use_tls: !prev.smtp_use_tls,
                            }));
                            setIsEmailModified(true);
                        }}
                    />

                    <div className="border-t pt-6">
                        <AvatarUploadField
                            label={t("avatar")}
                            description={t("avatarDescription")}
                            currentUrl={emailForm.smtp_avatar_url ?? null}
                            onUploaded={(url) => {
                                setEmailForm((prev) => ({ ...prev, smtp_avatar_url: url }));
                                setIsEmailModified(true);
                            }}
                            onClear={() => {
                                setEmailForm((prev) => ({ ...prev, smtp_avatar_url: null }));
                                setIsEmailModified(true);
                            }}
                        />
                    </div>

                    <div className="flex justify-end gap-3 pt-4 border-t">
                        {isEmailModified && (
                            <Button variant="outline" onClick={handleDiscard}>
                                {t("discard")}
                            </Button>
                        )}
                        <Button
                            onClick={handleSave}
                            disabled={saving || (!isEmailModified && !!config)}
                            className="gap-2"
                        >
                            {saving ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Save className="h-4 w-4" />
                            )}
                            {t("save")}
                        </Button>
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
