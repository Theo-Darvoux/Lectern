"use client";

import { useState, useEffect, useRef } from "react";
import { Palette, Layout, Loader2, Save, Search, Upload, Download, X, Plus, Trash2, Bold, Italic, RotateCcw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { useConfigStore } from "@/lib/stores";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import {
    AVAILABLE_FONTS,
    ALL_FONTS_URL,
    NameSegment,
    parseSegments,
    segmentStyle,
    segmentsToPlainText,
} from "@/lib/fonts";
import { SiteName } from "@/components/site-name";

interface AuthConfig {
    site_name: string;
    site_name_style: string | null;
    site_description: string;
    site_logo_url: string | null;
    site_favicon_url: string | null;
    primary_color: string;
    footer_text: string;
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
    saving: boolean;
    patchConfig: (patch: Partial<AuthConfig>) => Promise<void>;
}

// ── Image upload field ────────────────────────────────────────────────────────

interface ImageUploadFieldProps {
    label: string;
    currentUrl: string | null;
    uploadEndpoint: string;
    accept: string;
    onUploaded: (url: string) => void;
    onClear: () => void;
    showDownload?: boolean;
    previewSize?: "sm" | "md";
}

function ImageUploadField({
    label,
    currentUrl,
    uploadEndpoint,
    accept,
    onUploaded,
    onClear,
    showDownload = false,
    previewSize = "md",
}: ImageUploadFieldProps) {
    const [uploading, setUploading] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const previewHeight = previewSize === "sm" ? "h-10" : "h-20";

    const handleFile = async (file: File) => {
        setUploading(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { url } = await apiFetch<{ url: string }>(`/admin/${uploadEndpoint}`, {
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
            <div className="flex items-start gap-3">
                {currentUrl && (
                    <div className="relative flex-shrink-0 group">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={currentUrl}
                            alt={label}
                            className={`${previewHeight} w-auto max-w-[120px] object-contain rounded border bg-muted/30 p-1`}
                        />
                        {showDownload && (
                            <a
                                href={currentUrl}
                                download
                                target="_blank"
                                rel="noopener noreferrer"
                                className="absolute inset-0 flex items-center justify-center bg-black/50 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                                title="Download at full resolution"
                            >
                                <Download className="h-4 w-4 text-white" />
                            </a>
                        )}
                    </div>
                )}
                <div className="flex flex-col gap-2">
                    <div className="flex gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={uploading}
                            onClick={() => inputRef.current?.click()}
                        >
                            {uploading ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Upload className="mr-2 h-4 w-4" />
                            )}
                            {currentUrl ? "Replace" : "Upload"}
                        </Button>
                        {currentUrl && (
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={onClear}
                                title="Remove"
                            >
                                <X className="h-4 w-4" />
                            </Button>
                        )}
                    </div>
                    {currentUrl && showDownload && (
                        <a
                            href={currentUrl}
                            download
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-muted-foreground hover:text-foreground underline flex items-center gap-1"
                        >
                            <Download className="h-3 w-3" />
                            Download at full resolution
                        </a>
                    )}
                </div>
            </div>
            <input
                ref={inputRef}
                type="file"
                accept={accept}
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

// ── Font selector ─────────────────────────────────────────────────────────────

const FONT_CATEGORIES = [...new Set(AVAILABLE_FONTS.map((f) => f.category))];
const DEFAULT_SEGMENT: NameSegment = { text: "", font: "Inter", color: null, bold: false, italic: false };

function FontSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
    return (
        <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            style={{ fontFamily: value ? `'${value}', sans-serif` : undefined, minWidth: 160 }}
        >
            {FONT_CATEGORIES.map((cat) => (
                <optgroup key={cat} label={cat}>
                    {AVAILABLE_FONTS.filter((f) => f.category === cat).map((f) => (
                        <option
                            key={f.name}
                            value={f.name}
                            style={{ fontFamily: `'${f.name}', sans-serif` }}
                        >
                            {f.name}
                        </option>
                    ))}
                </optgroup>
            ))}
        </select>
    );
}

// ── Segment row ───────────────────────────────────────────────────────────────

function SegmentRow({
    seg,
    onChange,
    onDelete,
    canDelete,
}: {
    seg: NameSegment;
    onChange: (patch: Partial<NameSegment>) => void;
    onDelete: () => void;
    canDelete: boolean;
}) {
    return (
        <div className="flex items-center gap-2 flex-wrap">
            <Input
                value={seg.text}
                onChange={(e) => onChange({ text: e.target.value })}
                placeholder="Text…"
                className="h-8 w-28 font-mono text-sm"
            />
            <FontSelect value={seg.font} onChange={(font) => onChange({ font })} />
            {/* Color */}
            <div className="flex items-center gap-1">
                <input
                    type="color"
                    value={seg.color ?? "#000000"}
                    onChange={(e) => onChange({ color: e.target.value })}
                    className="h-8 w-8 cursor-pointer rounded border border-input p-0.5"
                    title="Text color"
                />
                {seg.color && (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        title="Use default color"
                        onClick={() => onChange({ color: null })}
                    >
                        <X className="h-3 w-3" />
                    </Button>
                )}
            </div>
            {/* Bold */}
            <Button
                type="button"
                variant={seg.bold ? "default" : "outline"}
                size="icon"
                className="h-8 w-8"
                onClick={() => onChange({ bold: !seg.bold })}
                title="Bold"
            >
                <Bold className="h-4 w-4" />
            </Button>
            {/* Italic */}
            <Button
                type="button"
                variant={seg.italic ? "default" : "outline"}
                size="icon"
                className="h-8 w-8"
                onClick={() => onChange({ italic: !seg.italic })}
                title="Italic"
            >
                <Italic className="h-4 w-4" />
            </Button>
            {canDelete && (
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive hover:text-destructive"
                    onClick={onDelete}
                    title="Remove segment"
                >
                    <Trash2 className="h-3.5 w-3.5" />
                </Button>
            )}
        </div>
    );
}

// ── Site name editor ──────────────────────────────────────────────────────────

function SiteNameEditor({
    siteName,
    siteNameStyle,
    onChange,
}: {
    siteName: string;
    siteNameStyle: string | null;
    onChange: (name: string, style: string | null) => void;
}) {
    const [segments, setSegments] = useState<NameSegment[]>(() => {
        const parsed = parseSegments(siteNameStyle);
        return parsed ?? [{ ...DEFAULT_SEGMENT, text: siteName, font: "Inter" }];
    });

    // Sync preview name + style JSON upward whenever segments change
    useEffect(() => {
        const plain = segmentsToPlainText(segments);
        const isUnstyled =
            segments.length === 1 &&
            !segments[0].color &&
            !segments[0].bold &&
            !segments[0].italic &&
            segments[0].font === "Inter";
        onChange(plain, isUnstyled ? null : JSON.stringify(segments));
    }, [segments]);

    const update = (index: number, patch: Partial<NameSegment>) => {
        setSegments((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
    };

    const remove = (index: number) => {
        setSegments((prev) => prev.filter((_, i) => i !== index));
    };

    const add = () => {
        setSegments((prev) => [...prev, { ...DEFAULT_SEGMENT }]);
    };

    const reset = () => {
        setSegments([{ ...DEFAULT_SEGMENT, text: siteName, font: "Inter" }]);
    };

    const previewStyle = JSON.stringify(segments);

    return (
        <div className="space-y-3">
            {/* Preview */}
            <div className="rounded-lg border bg-muted/30 px-4 py-3 text-xl font-extrabold tracking-tight">
                <SiteName
                    name={segmentsToPlainText(segments) || "Site name"}
                    style={previewStyle}
                    gradientClassName="bg-linear-to-br from-foreground to-foreground/70 bg-clip-text text-transparent"
                />
            </div>

            {/* Segment rows */}
            <div className="space-y-2">
                {segments.map((seg, i) => (
                    <SegmentRow
                        key={i}
                        seg={seg}
                        onChange={(patch) => update(i, patch)}
                        onDelete={() => remove(i)}
                        canDelete={segments.length > 1}
                    />
                ))}
            </div>

            <div className="flex gap-2">
                <Button type="button" variant="outline" size="sm" onClick={add}>
                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                    Add segment
                </Button>
                {segments.length > 1 || segments[0].color || segments[0].bold || segments[0].italic || segments[0].font !== "Inter" ? (
                    <Button type="button" variant="ghost" size="sm" onClick={reset}>
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                        Reset styling
                    </Button>
                ) : null}
            </div>
        </div>
    );
}

// ── Main tab ──────────────────────────────────────────────────────────────────

export function BrandingConfigTab({ config, saving, patchConfig }: BrandingConfigTabProps) {
    const t = useTranslations("Admin.Config.Branding");
    const [brandingForm, setBrandingForm] = useState<Partial<AuthConfig>>({});
    const [isBrandingModified, setIsBrandingModified] = useState(false);
    const { updateConfig: updateGlobalConfig } = useConfigStore();

    // Inject all fonts so the picker previews work
    useEffect(() => {
        if (!document.querySelector("link[data-wikint-all-fonts]")) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = ALL_FONTS_URL;
            link.setAttribute("data-wikint-all-fonts", "1");
            document.head.appendChild(link);
        }
    }, []);

    useEffect(() => {
        setBrandingForm({
            site_name: config.site_name,
            site_name_style: config.site_name_style,
            site_description: config.site_description,
            site_logo_url: config.site_logo_url,
            site_favicon_url: config.site_favicon_url,
            primary_color: config.primary_color,
            footer_text: config.footer_text,
            organization_url: config.organization_url,
            og_image_url: config.og_image_url,
            bg_watermark_url: config.bg_watermark_url,
            bg_watermark_opacity_light: config.bg_watermark_opacity_light,
            bg_watermark_opacity_dark: config.bg_watermark_opacity_dark,
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
            site_name_style: config.site_name_style,
            site_description: config.site_description,
            site_logo_url: config.site_logo_url,
            site_favicon_url: config.site_favicon_url,
            primary_color: config.primary_color,
            footer_text: config.footer_text,
            organization_url: config.organization_url,
            og_image_url: config.og_image_url,
            bg_watermark_url: config.bg_watermark_url,
            bg_watermark_opacity_light: config.bg_watermark_opacity_light,
            bg_watermark_opacity_dark: config.bg_watermark_opacity_dark,
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
                    {/* Site name — rich editor */}
                    <div className="space-y-2">
                        <Label>{t("visualIdentity.siteName")}</Label>
                        <SiteNameEditor
                            siteName={brandingForm.site_name ?? config.site_name}
                            siteNameStyle={brandingForm.site_name_style ?? config.site_name_style ?? null}
                            onChange={(name, style) => {
                                setBrandingForm((prev) => ({
                                    ...prev,
                                    site_name: name,
                                    site_name_style: style,
                                }));
                                setIsBrandingModified(true);
                                updateGlobalConfig({ site_name: name, site_name_style: style });
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
                        <ImageUploadField
                            label={t("visualIdentity.logo")}
                            currentUrl={brandingForm.site_logo_url ?? null}
                            uploadEndpoint="auth-config/upload-logo"
                            accept="image/png,image/jpeg,image/svg+xml,image/webp,image/gif"
                            showDownload
                            previewSize="md"
                            onUploaded={(url) => {
                                setBrandingForm(prev => ({ ...prev, site_logo_url: url }));
                            }}
                            onClear={() => {
                                setBrandingForm(prev => ({ ...prev, site_logo_url: null }));
                                setIsBrandingModified(true);
                            }}
                        />
                        <ImageUploadField
                            label={t("visualIdentity.favicon")}
                            currentUrl={brandingForm.site_favicon_url ?? null}
                            uploadEndpoint="auth-config/upload-favicon"
                            accept="image/png,image/x-icon,image/vnd.microsoft.icon,image/svg+xml"
                            previewSize="sm"
                            onUploaded={(url) => {
                                setBrandingForm(prev => ({ ...prev, site_favicon_url: url }));
                            }}
                            onClear={() => {
                                setBrandingForm(prev => ({ ...prev, site_favicon_url: null }));
                                setIsBrandingModified(true);
                            }}
                        />
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
                    <ImageUploadField
                        label={t("seo.ogImageUrl")}
                        currentUrl={brandingForm.og_image_url ?? null}
                        uploadEndpoint="auth-config/upload-og-image"
                        accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                        showDownload
                        previewSize="md"
                        onUploaded={(url) => {
                            setBrandingForm(prev => ({ ...prev, og_image_url: url }));
                            setIsBrandingModified(true);
                        }}
                        onClear={() => {
                            setBrandingForm(prev => ({ ...prev, og_image_url: null }));
                            setIsBrandingModified(true);
                        }}
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
                    <ImageUploadField
                        label={t("watermark.image")}
                        currentUrl={brandingForm.bg_watermark_url ?? null}
                        uploadEndpoint="auth-config/upload-bg-watermark"
                        accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                        showDownload
                        previewSize="md"
                        onUploaded={(url) => {
                            setBrandingForm(prev => ({ ...prev, bg_watermark_url: url }));
                            setIsBrandingModified(true);
                            updateGlobalConfig({ bg_watermark_url: url });
                        }}
                        onClear={() => {
                            setBrandingForm(prev => ({ ...prev, bg_watermark_url: null }));
                            setIsBrandingModified(true);
                            updateGlobalConfig({ bg_watermark_url: null });
                        }}
                    />
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <Label htmlFor="bg_watermark_opacity_light">{t("watermark.opacityLight")}</Label>
                                <span className="text-sm text-muted-foreground tabular-nums">
                                    {Math.round((brandingForm.bg_watermark_opacity_light ?? 0.05) * 100)}%
                                </span>
                            </div>
                            <input
                                id="bg_watermark_opacity_light"
                                type="range"
                                min={0}
                                max={1}
                                step={0.01}
                                value={brandingForm.bg_watermark_opacity_light ?? 0.05}
                                onChange={(e) => {
                                    const val = parseFloat(e.target.value);
                                    setBrandingForm(prev => ({ ...prev, bg_watermark_opacity_light: val }));
                                    setIsBrandingModified(true);
                                    updateGlobalConfig({ bg_watermark_opacity_light: val });
                                }}
                                className="w-full accent-primary"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <Label htmlFor="bg_watermark_opacity_dark">{t("watermark.opacityDark")}</Label>
                                <span className="text-sm text-muted-foreground tabular-nums">
                                    {Math.round((brandingForm.bg_watermark_opacity_dark ?? 0.05) * 100)}%
                                </span>
                            </div>
                            <input
                                id="bg_watermark_opacity_dark"
                                type="range"
                                min={0}
                                max={1}
                                step={0.01}
                                value={brandingForm.bg_watermark_opacity_dark ?? 0.05}
                                onChange={(e) => {
                                    const val = parseFloat(e.target.value);
                                    setBrandingForm(prev => ({ ...prev, bg_watermark_opacity_dark: val }));
                                    setIsBrandingModified(true);
                                    updateGlobalConfig({ bg_watermark_opacity_dark: val });
                                }}
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
