"use client";

import { Database, Cloud } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { TabsContent } from "@/components/ui/tabs";
import { useTranslations } from "next-intl";

interface AuthConfig {
    s3_endpoint: string | null;
    s3_access_key: string | null;
    s3_secret_key: string | null;
    s3_bucket: string | null;
    s3_public_endpoint: string | null;
    s3_region: string | null;
    s3_use_ssl: boolean;
    max_storage_gb: number | null;
}

interface StorageConfigTabProps {
    config: AuthConfig;
}

export function StorageConfigTab({ config }: StorageConfigTabProps) {
    const t = useTranslations("Admin.Config.Storage");

    return (
        <TabsContent value="storage" className="mt-6 space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Database className="h-5 w-5 text-primary" />
                        {t("title")}
                    </CardTitle>
                    <CardDescription>
                        {t("description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="s3_endpoint">{t("endpoint")}</Label>
                            <Input
                                id="s3_endpoint"
                                readOnly
                                value={config.s3_endpoint ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                            <p className="text-[10px] text-muted-foreground">
                                {t("endpointHelp")}
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="s3_region">{t("region")}</Label>
                            <Input
                                id="s3_region"
                                readOnly
                                value={config.s3_region ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="s3_bucket">{t("bucket")}</Label>
                            <Input
                                id="s3_bucket"
                                readOnly
                                value={config.s3_bucket ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="s3_public_endpoint">{t("publicEndpoint")}</Label>
                            <Input
                                id="s3_public_endpoint"
                                readOnly
                                value={config.s3_public_endpoint ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="s3_access_key">{t("accessKey")}</Label>
                            <Input
                                id="s3_access_key"
                                type="password"
                                readOnly
                                value={config.s3_access_key ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="s3_secret_key">{t("secretKey")}</Label>
                            <Input
                                id="s3_secret_key"
                                type="password"
                                readOnly
                                value={config.s3_secret_key ?? ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                    </div>

                    <div className="pt-4 border-t space-y-4">
                        <div className="space-y-2 max-w-xs">
                            <Label htmlFor="max_storage_gb">{t("maxStorage")}</Label>
                            <div className="flex items-center gap-3">
                                <Input
                                    id="max_storage_gb"
                                    type="number"
                                    readOnly
                                    value={config.max_storage_gb ?? ""}
                                    className="bg-muted/30 cursor-default"
                                />
                                <span className="text-sm font-bold text-muted-foreground whitespace-nowrap">GB</span>
                            </div>
                            <p className="text-[10px] text-muted-foreground">
                                {t("maxStorageHelp")}
                            </p>
                        </div>

                        <div className="flex items-start justify-between gap-4 py-4">
                            <div className="flex gap-3">
                                <Cloud className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                                <div>
                                    <p className="font-medium text-sm leading-none">{t("useSsl")}</p>
                                    <p className="mt-1 text-xs text-muted-foreground">{t("useSslDescription")}</p>
                                </div>
                            </div>
                            <Switch checked={config.s3_use_ssl} disabled />
                        </div>
                    </div>
                </CardContent>
            </Card>
        </TabsContent>
    );
}
