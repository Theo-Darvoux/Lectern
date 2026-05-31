"use client";

import { useState, useEffect } from "react";
import { Shield, Mail, Globe, Lock, Globe2, Trash2, Plus, Loader2, Clock, Eye, Info } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { TabsContent } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

interface Domain {
    id: string;
    domain: string;
    auto_approve: boolean;
}

interface AuthConfig {
    totp_enabled: boolean;
    google_oauth_enabled: boolean;
    google_client_id: string | null;
    classic_auth_enabled: boolean;
    allow_all_domains: boolean;
    auto_approve_all_domains: boolean;
    guest_access_enabled: boolean;
    domains_from_env?: boolean;
    jwt_access_expire_days: number;
    jwt_refresh_expire_days: number;
    domains: Domain[];
}

interface AuthConfigTabProps {
    config: AuthConfig;
}

function ReadOnlyToggleRow({
    label,
    description,
    checked,
    icon: Icon,
}: {
    label: string;
    description: string;
    checked: boolean;
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
            <Switch checked={checked} disabled aria-readonly="true" />
        </div>
    );
}

export function AuthConfigTab({ config }: AuthConfigTabProps) {
    const t = useTranslations("Admin.Config.Authentication");

    const [newDomain, setNewDomain] = useState("");
    const [newAutoApprove, setNewAutoApprove] = useState(true);
    const [addingDomain, setAddingDomain] = useState(false);
    const [domains, setDomains] = useState<Domain[]>(config.domains);

    useEffect(() => {
        setDomains(config.domains);
    }, [config]);

    const handleAddDomain = async () => {
        const domain = newDomain.trim().replace(/^@/, "").toLowerCase();
        if (!domain) return;

        setAddingDomain(true);
        try {
            const added = await apiFetch<Domain>("/admin/auth-config/domains", {
                method: "POST",
                body: JSON.stringify({ domain, auto_approve: newAutoApprove }),
            });
            setDomains((prev) => [...prev, added]);
            setNewDomain("");
            setNewAutoApprove(true);
            toast.success(t("domains.success.added", { domain: added.domain }));
        } catch (err: any) {
            if (err?.message?.includes("already exists") || err?.message?.includes("409")) {
                toast.error(t("domains.errors.alreadyExists"));
            } else {
                toast.error(t("domains.errors.addFailed"));
            }
        } finally {
            setAddingDomain(false);
        }
    };

    const handleToggleAutoApprove = async (domainId: string, current: boolean) => {
        try {
            const updated = await apiFetch<Domain>(`/admin/auth-config/domains/${domainId}`, {
                method: "PATCH",
                body: JSON.stringify({ auto_approve: !current }),
            });
            setDomains((prev) =>
                prev.map((d) =>
                    d.id === domainId ? { ...d, auto_approve: updated.auto_approve } : d
                )
            );
        } catch {
            toast.error(t("domains.errors.updateFailed"));
        }
    };

    const handleDeleteDomain = async (domainId: string, domain: string) => {
        try {
            await apiFetch(`/admin/auth-config/domains/${domainId}`, { method: "DELETE" });
            setDomains((prev) => prev.filter((d) => d.id !== domainId));
            toast.success(t("domains.success.removed", { domain }));
        } catch {
            toast.error(t("domains.errors.removeFailed"));
        }
    };

    return (
        <TabsContent value="authentication" className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <p className="text-sm text-muted-foreground">
                {t("description")}
            </p>

            <Card>
                <CardHeader>
                    <div className="flex items-center gap-2">
                        <Shield className="h-5 w-5 text-primary" />
                        <CardTitle className="text-base">{t("methods.title")}</CardTitle>
                    </div>
                    <CardDescription>
                        {t("methods.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="divide-y px-6 pb-4">
                    <ReadOnlyToggleRow
                        icon={Mail}
                        label={t("methods.totp.label")}
                        description={t("methods.totp.description")}
                        checked={config.totp_enabled}
                    />
                    <div className="flex flex-col gap-4 py-4">
                        <ReadOnlyToggleRow
                            icon={Globe}
                            label={t("methods.google.label")}
                            description={t("methods.google.description")}
                            checked={config.google_oauth_enabled}
                        />
                        <div className="ml-8 space-y-2">
                            <Label htmlFor="google-client-id" className="text-xs font-medium text-muted-foreground">{t("methods.google.clientId")}</Label>
                            <Input
                                id="google-client-id"
                                type="text"
                                readOnly
                                value={config.google_client_id ?? ""}
                                className="h-8 max-w-[400px] bg-muted/30 cursor-default"
                            />
                        </div>
                        <ReadOnlyToggleRow
                            icon={Lock}
                            label={t("methods.classic.label")}
                            description={t("methods.classic.description")}
                            checked={config.classic_auth_enabled}
                        />
                    </div>
                    <ReadOnlyToggleRow
                        icon={Eye}
                        label={t("methods.guest.label")}
                        description={t("methods.guest.description")}
                        checked={config.guest_access_enabled}
                    />
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <div className="flex items-center gap-2">
                        <Clock className="h-5 w-5 text-primary" />
                        <CardTitle className="text-base">{t("session.title")}</CardTitle>
                    </div>
                    <CardDescription>
                        {t("session.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-6 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="jwt-access" className="text-sm font-medium">
                                {t("session.accessExpiry")}
                            </Label>
                            <div className="flex items-center gap-3">
                                <Input
                                    id="jwt-access"
                                    type="number"
                                    readOnly
                                    value={config.jwt_access_expire_days}
                                    className="h-9 w-24 bg-muted/30 cursor-default"
                                />
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                                {t("session.accessExpiryHelp")}
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="jwt-refresh" className="text-sm font-medium">
                                {t("session.refreshExpiry")}
                            </Label>
                            <div className="flex items-center gap-3">
                                <Input
                                    id="jwt-refresh"
                                    type="number"
                                    readOnly
                                    value={config.jwt_refresh_expire_days}
                                    className="h-9 w-24 bg-muted/30 cursor-default"
                                />
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                                {t("session.refreshExpiryHelp")}
                            </p>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <div className="flex items-center gap-2">
                        <Globe className="h-5 w-5 text-primary" />
                        <CardTitle className="text-base">{t("domains.title")}</CardTitle>
                    </div>
                    <CardDescription dangerouslySetInnerHTML={{ __html: t.raw("domains.description") }} />
                </CardHeader>
                <CardContent className="space-y-4">
                    {config.domains_from_env && (
                        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800/40 dark:bg-amber-900/20 dark:text-amber-300">
                            <Info className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{t("domains.envOverrideBanner")}</span>
                        </div>
                    )}

                    {domains.length === 0 ? (
                        <p className="text-sm text-muted-foreground italic">
                            {t("domains.empty")}
                        </p>
                    ) : (
                        <div className="divide-y rounded-lg border">
                            <div className="flex items-center justify-between gap-4 px-4 py-3 bg-primary/5">
                                <div className="flex items-center gap-3">
                                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10">
                                        <Globe2 className="h-4 w-4 text-primary" />
                                    </div>
                                    <div className="flex flex-col">
                                        <span className="text-sm font-bold text-primary">{t("domains.allowAll.label")}</span>
                                        <span className="text-[10px] text-muted-foreground">{t("domains.allowAll.description")}</span>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Badge variant="outline" className="text-[10px] font-mono whitespace-nowrap hidden sm:inline-flex">
                                        GLOBAL_ACCESS
                                    </Badge>
                                    <Switch checked={config.allow_all_domains} disabled />
                                </div>
                            </div>
                            {config.allow_all_domains && (
                                <div className="flex items-center justify-between gap-4 px-4 py-3 bg-amber-500/5 border-t border-amber-500/20">
                                    <div className="flex items-center gap-3">
                                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-amber-500/10">
                                            <Globe2 className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                                        </div>
                                        <div className="flex flex-col">
                                            <span className="text-sm font-bold text-amber-700 dark:text-amber-400">{t("domains.autoApproveAll.label")}</span>
                                            <span className="text-[10px] text-muted-foreground">{t("domains.autoApproveAll.description")}</span>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Badge variant="outline" className="text-[10px] font-mono whitespace-nowrap hidden sm:inline-flex">
                                            AUTO_APPROVE_ALL
                                        </Badge>
                                        <Switch checked={config.auto_approve_all_domains} disabled />
                                    </div>
                                </div>
                            )}
                            {domains.map((d) => (
                                <div
                                    key={d.id}
                                    className="flex items-center justify-between gap-4 px-4 py-3"
                                >
                                    <div className="flex items-center gap-3 min-w-0">
                                        <span className="font-mono text-sm truncate">
                                            @{d.domain}
                                        </span>
                                        <Badge
                                            className={cn(
                                                "shrink-0 text-xs border-0",
                                                d.auto_approve
                                                    ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
                                                    : "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                                            )}
                                        >
                                            {d.auto_approve ? t("domains.autoApprove") : t("domains.manualReview")}
                                        </Badge>
                                    </div>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <Switch
                                            checked={d.auto_approve}
                                            onCheckedChange={() =>
                                                handleToggleAutoApprove(d.id, d.auto_approve)
                                            }
                                        />
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => handleDeleteDomain(d.id, d.domain)}
                                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="flex flex-col gap-3 rounded-lg border border-dashed bg-muted/30 p-4">
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                            {t("domains.add.title")}
                        </p>
                        <div className="flex gap-2">
                            <div className="relative flex-1">
                                <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">
                                    @
                                </span>
                                <Input
                                    className="pl-7 font-mono text-sm"
                                    placeholder={t("domains.add.placeholder")}
                                    value={newDomain}
                                    onChange={(e) => setNewDomain(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleAddDomain()}
                                />
                            </div>
                            <Button
                                onClick={handleAddDomain}
                                disabled={!newDomain.trim() || addingDomain}
                            >
                                {addingDomain ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Plus className="h-4 w-4" />
                                )}
                                <span className="ml-1.5">{t("domains.add.button")}</span>
                            </Button>
                        </div>
                        <div className="flex items-center gap-2">
                            <Switch
                                id="new-domain-auto-approve"
                                checked={newAutoApprove}
                                onCheckedChange={setNewAutoApprove}
                            />
                            <Label htmlFor="new-domain-auto-approve" className="text-xs">
                                {t("domains.add.autoApproveLabel")}
                            </Label>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </TabsContent>
    );
}
