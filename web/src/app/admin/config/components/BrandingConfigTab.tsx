"use client";

import { useState, useEffect } from "react";
import { Palette, Layout, Loader2, Save, Search } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { useConfigStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

interface AuthConfig {
    site_name: string;
    site_description: string;
    site_logo_url: string | null;
    site_favicon_url: string | null;
    primary_color: string;
    footer_text: string;
    organization_url: string | null;
    og_image_url: string | null;
    legal_name: string | null;
    legal_address: string | null;
    legal_siret: string | null;
    contact_email: string | null;
    dpo_email: string | null;
    dpo_address: string | null;
    data_transfers: string | null;
    legal_version: string | null;
}

interface BrandingConfigTabProps {
    config: AuthConfig;
    saving: boolean;
    patchConfig: (patch: Partial<AuthConfig>) => Promise<void>;
}

export function BrandingConfigTab({ config, saving, patchConfig }: BrandingConfigTabProps) {
    const t = useTranslations("Admin.Config.Branding");
    const [brandingForm, setBrandingForm] = useState<Partial<AuthConfig>>({});
    const [isBrandingModified, setIsBrandingModified] = useState(false);
    const { updateConfig: updateGlobalConfig } = useConfigStore();

    useEffect(() => {
        setBrandingForm({
            site_name: config.site_name,
            site_description: config.site_description,
            site_logo_url: config.site_logo_url,
            site_favicon_url: config.site_favicon_url,
            primary_color: config.primary_color,
            footer_text: config.footer_text,
            organization_url: config.organization_url,
            og_image_url: config.og_image_url,
            legal_name: config.legal_name,
            legal_address: config.legal_address,
            legal_siret: config.legal_siret,
            contact_email: config.contact_email,
            dpo_email: config.dpo_email,
            dpo_address: config.dpo_address,
            data_transfers: config.data_transfers,
            legal_version: config.legal_version,
        });
        setIsBrandingModified(false);
    }, [config]);

    const handleSave = async () => {
        await patchConfig(brandingForm);
        toast.success(t("success"));
        setIsBrandingModified(false);
    };

    const handleDiscard = () => {
        setBrandingForm({
            site_name: config.site_name,
            site_description: config.site_description,
            site_logo_url: config.site_logo_url,
            site_favicon_url: config.site_favicon_url,
            primary_color: config.primary_color,
            footer_text: config.footer_text,
            organization_url: config.organization_url,
            og_image_url: config.og_image_url,
            legal_name: config.legal_name,
            legal_address: config.legal_address,
            legal_siret: config.legal_siret,
            contact_email: config.contact_email,
            dpo_email: config.dpo_email,
            dpo_address: config.dpo_address,
            data_transfers: config.data_transfers,
            legal_version: config.legal_version,
        });
        setIsBrandingModified(false);
        // Revert global config preview
        updateGlobalConfig({
            site_name: config.site_name,
            primary_color: config.primary_color,
        });
    };

    return (
        <TabsContent value="branding" className="mt-6 space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Palette className="h-5 w-5 text-primary" />
                        {t("visualIdentity.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("visualIdentity.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="site_name">{t("visualIdentity.siteName")}</Label>
                            <Input
                                id="site_name"
                                placeholder={t("visualIdentity.placeholders.siteName")}
                                value={brandingForm.site_name || ""}
                                onChange={(e) => {
                                    const val = e.target.value;
                                    setBrandingForm(prev => ({ ...prev, site_name: val }));
                                    setIsBrandingModified(true);
                                    updateGlobalConfig({ site_name: val });
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="primary_color">{t("visualIdentity.primaryColor")}</Label>
                            <div className="flex gap-2">
                                <Input
                                    id="primary_color"
                                    type="color"
                                    value={brandingForm.primary_color || "#3b82f6"}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        setBrandingForm(prev => ({ ...prev, primary_color: val }));
                                        setIsBrandingModified(true);
                                        updateGlobalConfig({ primary_color: val });
                                    }}
                                    className="w-12 h-9 p-1"
                                />
                                <Input
                                    type="text"
                                    value={brandingForm.primary_color || ""}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        setBrandingForm(prev => ({ ...prev, primary_color: val }));
                                        setIsBrandingModified(true);
                                        updateGlobalConfig({ primary_color: val });
                                    }}
                                    className="flex-1 font-mono uppercase"
                                    placeholder="#3B82F6"
                                />
                            </div>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="site_description">{t("visualIdentity.siteDescription")}</Label>
                        <Input
                            id="site_description"
                            placeholder={t("visualIdentity.placeholders.siteDescription")}
                            value={brandingForm.site_description || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, site_description: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="site_logo_url">{t("visualIdentity.logoUrl")}</Label>
                            <Input
                                id="site_logo_url"
                                placeholder={t("visualIdentity.placeholders.logoUrl")}
                                value={brandingForm.site_logo_url || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, site_logo_url: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="site_favicon_url">{t("visualIdentity.faviconUrl")}</Label>
                            <Input
                                id="site_favicon_url"
                                placeholder={t("visualIdentity.placeholders.faviconUrl")}
                                value={brandingForm.site_favicon_url || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, site_favicon_url: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Search className="h-5 w-5 text-primary" />
                        {t("seo.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("seo.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="space-y-2">
                        <Label htmlFor="og_image_url">{t("seo.ogImageUrl")}</Label>
                        <Input
                            id="og_image_url"
                            placeholder={t("seo.placeholders.ogImageUrl")}
                            value={brandingForm.og_image_url || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, og_image_url: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Layout className="h-5 w-5 text-primary" />
                        {t("footerLinks.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("footerLinks.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="footer_text">{t("footerLinks.footerText")}</Label>
                            <Input
                                id="footer_text"
                                placeholder={t("footerLinks.placeholders.footerText")}
                                value={brandingForm.footer_text || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, footer_text: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="organization_url">{t("footerLinks.organizationUrl")}</Label>
                            <Input
                                id="organization_url"
                                placeholder={t("footerLinks.placeholders.organizationUrl")}
                                value={brandingForm.organization_url || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, organization_url: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Layout className="h-5 w-5 text-primary" />
                        {t("legal.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("legal.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="legal_name">{t("legal.legalName")}</Label>
                            <Input
                                id="legal_name"
                                placeholder={t("legal.placeholders.legalName")}
                                value={brandingForm.legal_name || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, legal_name: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="contact_email">{t("legal.contactEmail")}</Label>
                            <Input
                                id="contact_email"
                                placeholder={t("legal.placeholders.contactEmail")}
                                value={brandingForm.contact_email || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, contact_email: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_address">{t("legal.legalAddress")}</Label>
                        <Input
                            id="legal_address"
                            placeholder={t("legal.placeholders.legalAddress")}
                            value={brandingForm.legal_address || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, legal_address: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_siret">{t("legal.legalSiret")}</Label>
                        <Input
                            id="legal_siret"
                            placeholder={t("legal.placeholders.legalSiret")}
                            value={brandingForm.legal_siret || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, legal_siret: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="dpo_email">{t("legal.dpoEmail")}</Label>
                            <Input
                                id="dpo_email"
                                placeholder={t("legal.placeholders.dpoEmail")}
                                value={brandingForm.dpo_email || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, dpo_email: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="dpo_address">{t("legal.dpoAddress")}</Label>
                            <Input
                                id="dpo_address"
                                placeholder={t("legal.placeholders.dpoAddress")}
                                value={brandingForm.dpo_address || ""}
                                onChange={(e) => {
                                    setBrandingForm(prev => ({ ...prev, dpo_address: e.target.value }));
                                    setIsBrandingModified(true);
                                }}
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="data_transfers">{t("legal.dataTransfers")}</Label>
                        <Input
                            id="data_transfers"
                            placeholder={t("legal.placeholders.dataTransfers")}
                            value={brandingForm.data_transfers || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, data_transfers: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_version">{t("legal.legalVersion")}</Label>
                        <Input
                            id="legal_version"
                            placeholder={t("legal.placeholders.legalVersion")}
                            value={brandingForm.legal_version || ""}
                            onChange={(e) => {
                                setBrandingForm(prev => ({ ...prev, legal_version: e.target.value }));
                                setIsBrandingModified(true);
                            }}
                        />
                    </div>
                </CardContent>
            </Card>

            <div className="flex justify-end p-6 border-t bg-muted/20 gap-3">
                {isBrandingModified && (
                    <Button variant="outline" onClick={handleDiscard}>
                        {t("discard")}
                    </Button>
                )}
                <Button 
                    disabled={saving || !isBrandingModified} 
                    onClick={handleSave}
                >
                    {saving ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                        <Save className="mr-2 h-4 w-4" />
                    )}
                    {t("save")}
                </Button>
            </div>
        </TabsContent>
    );
}
