"use client";

import { useEffect } from "react";
import { Palette, Layout, Search } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { TabsContent } from "@/components/ui/tabs";
import { useTranslations } from "next-intl";
import {
    ALL_FONTS_URL,
    parseSegments,
} from "@/lib/fonts";
import { SiteName } from "@/components/site-name";
import { WordmarkBuilder } from "./WordmarkBuilder";

interface AuthConfig {
    site_name: string;
    site_name_style: string | null;
    site_description: string;
    site_logo_url: string | null;
    site_favicon_url: string | null;
    primary_color: string;
    footer_text: string;
    footer_logo_url: string | null;
    organization_url: string | null;
    og_image_url: string | null;
    bg_watermark_url: string | null;
    bg_watermark_opacity_light: number | null;
    bg_watermark_opacity_dark: number | null;
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
}

function ImagePreview({ label, url, previewSize = "md" }: { label: string; url: string | null; previewSize?: "sm" | "md" }) {
    const previewHeight = previewSize === "sm" ? "h-10" : "h-20";
    return (
        <div className="space-y-2">
            <Label>{label}</Label>
            {url ? (
                <div className="flex items-start gap-3">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                        src={url}
                        alt={label}
                        className={`${previewHeight} w-auto max-w-[120px] object-contain rounded border bg-muted/30 p-1`}
                    />
                    <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-muted-foreground hover:text-foreground underline break-all"
                    >
                        {url}
                    </a>
                </div>
            ) : (
                <p className="text-sm text-muted-foreground italic">—</p>
            )}
        </div>
    );
}

export function BrandingConfigTab({ config }: BrandingConfigTabProps) {
    const t = useTranslations("Admin.Config.Branding");

    useEffect(() => {
        if (!document.querySelector("link[data-lectern-all-fonts]")) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = ALL_FONTS_URL;
            link.setAttribute("data-lectern-all-fonts", "1");
            document.head.appendChild(link);
        }
    }, []);

    const segments = parseSegments(config.site_name_style);

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
                    <div className="space-y-2">
                        <Label>{t("visualIdentity.siteName")}</Label>
                        <div className="rounded-lg border bg-muted/30 px-4 py-3 text-xl font-extrabold tracking-tight">
                            <SiteName
                                name={config.site_name}
                                style={config.site_name_style}
                                gradientClassName="bg-linear-to-br from-foreground to-foreground/70 bg-clip-text text-transparent"
                            />
                        </div>
                        {segments && segments.length > 1 && (
                            <p className="text-[11px] text-muted-foreground font-mono">{config.site_name_style}</p>
                        )}
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="primary_color">{t("visualIdentity.primaryColor")}</Label>
                        <div className="flex gap-2 items-center">
                            <div
                                className="h-9 w-12 rounded border border-input"
                                style={{ backgroundColor: config.primary_color || "#3b82f6" }}
                            />
                            <Input
                                id="primary_color"
                                readOnly
                                value={config.primary_color || ""}
                                className="flex-1 font-mono uppercase bg-muted/30 cursor-default max-w-[200px]"
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="site_description">{t("visualIdentity.siteDescription")}</Label>
                        <Input
                            id="site_description"
                            readOnly
                            value={config.site_description || ""}
                            className="bg-muted/30 cursor-default"
                        />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <ImagePreview
                            label={t("visualIdentity.logo")}
                            url={config.site_logo_url}
                            previewSize="md"
                        />
                        <ImagePreview
                            label={t("visualIdentity.favicon")}
                            url={config.site_favicon_url}
                            previewSize="sm"
                        />
                    </div>
                </CardContent>
            </Card>

            <WordmarkBuilder
                siteName={config.site_name}
                siteNameStyle={config.site_name_style}
            />

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
                    <ImagePreview
                        label={t("seo.ogImageUrl")}
                        url={config.og_image_url}
                        previewSize="md"
                    />
                    <p className="text-xs text-muted-foreground">{t("seo.ogImageUrlHint")}</p>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Layout className="h-5 w-5 text-primary" />
                        {t("watermark.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("watermark.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <ImagePreview
                        label={t("watermark.image")}
                        url={config.bg_watermark_url}
                        previewSize="md"
                    />
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <Label>{t("watermark.opacityLight")}</Label>
                                <span className="text-sm text-muted-foreground tabular-nums">
                                    {Math.round((config.bg_watermark_opacity_light ?? 0.05) * 100)}%
                                </span>
                            </div>
                            <input
                                type="range"
                                min={0}
                                max={1}
                                step={0.01}
                                disabled
                                value={config.bg_watermark_opacity_light ?? 0.05}
                                className="w-full accent-primary"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <Label>{t("watermark.opacityDark")}</Label>
                                <span className="text-sm text-muted-foreground tabular-nums">
                                    {Math.round((config.bg_watermark_opacity_dark ?? 0.05) * 100)}%
                                </span>
                            </div>
                            <input
                                type="range"
                                min={0}
                                max={1}
                                step={0.01}
                                disabled
                                value={config.bg_watermark_opacity_dark ?? 0.05}
                                className="w-full accent-primary"
                            />
                        </div>
                    </div>
                    <p className="text-xs text-muted-foreground">{t("watermark.opacityHint")}</p>
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
                    <ImagePreview
                        label={t("footerLinks.footerLogo")}
                        url={config.footer_logo_url}
                        previewSize="sm"
                    />
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="footer_text">{t("footerLinks.footerText")}</Label>
                            <Input
                                id="footer_text"
                                readOnly
                                value={config.footer_text || ""}
                                className="bg-muted/30 cursor-default"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="organization_url">{t("footerLinks.organizationUrl")}</Label>
                            <Input
                                id="organization_url"
                                readOnly
                                value={config.organization_url || ""}
                                className="bg-muted/30 cursor-default"
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
                            <Input id="legal_name" readOnly value={config.legal_name || ""} className="bg-muted/30 cursor-default" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="contact_email">{t("legal.contactEmail")}</Label>
                            <Input id="contact_email" readOnly value={config.contact_email || ""} className="bg-muted/30 cursor-default" />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_address">{t("legal.legalAddress")}</Label>
                        <Input id="legal_address" readOnly value={config.legal_address || ""} className="bg-muted/30 cursor-default" />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_siret">{t("legal.legalSiret")}</Label>
                        <Input id="legal_siret" readOnly value={config.legal_siret || ""} className="bg-muted/30 cursor-default" />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="dpo_email">{t("legal.dpoEmail")}</Label>
                            <Input id="dpo_email" readOnly value={config.dpo_email || ""} className="bg-muted/30 cursor-default" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="dpo_address">{t("legal.dpoAddress")}</Label>
                            <Input id="dpo_address" readOnly value={config.dpo_address || ""} className="bg-muted/30 cursor-default" />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="data_transfers">{t("legal.dataTransfers")}</Label>
                        <Input id="data_transfers" readOnly value={config.data_transfers || ""} className="bg-muted/30 cursor-default" />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="legal_version">{t("legal.legalVersion")}</Label>
                        <Input id="legal_version" readOnly value={config.legal_version || ""} className="bg-muted/30 cursor-default" />
                    </div>
                </CardContent>
            </Card>
        </TabsContent>
    );
}
