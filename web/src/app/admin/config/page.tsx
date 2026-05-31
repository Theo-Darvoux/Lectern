"use client";

import { useCallback, useEffect, useState } from "react";
import {
    Shield,
    Mail,
    Loader2,
    HardDrive,
    FileCode,
    Palette,
    Info,
} from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTranslations } from "next-intl";

// Extracted Components
import { AuthConfigTab } from "./components/AuthConfigTab";
import { EmailConfigTab } from "./components/EmailConfigTab";
import { StorageConfigTab } from "./components/StorageConfigTab";
import { FilesConfigTab } from "./components/FilesConfigTab";
import { BrandingConfigTab } from "./components/BrandingConfigTab";

export interface AuthConfig {
    totp_enabled: boolean;
    google_oauth_enabled: boolean;
    google_client_id: string | null;
    classic_auth_enabled: boolean;
    allow_all_domains: boolean;
    auto_approve_all_domains: boolean;
    guest_access_enabled: boolean;
    domains_from_env: boolean;
    jwt_access_expire_days: number;
    jwt_refresh_expire_days: number;
    domains: any[];
    smtp_host: string | null;
    smtp_ip: string | null;
    smtp_port: number | null;
    smtp_user: string | null;
    smtp_password: string | null;
    smtp_from: string | null;
    smtp_sender_name: string | null;
    smtp_avatar_url: string | null;
    smtp_use_tls: boolean;
    s3_endpoint: string | null;
    s3_access_key: string | null;
    s3_secret_key: string | null;
    s3_bucket: string | null;
    s3_public_endpoint: string | null;
    s3_region: string | null;
    s3_use_ssl: boolean;
    max_storage_gb: number | null;
    max_file_size_mb: number;
    max_image_size_mb: number;
    max_audio_size_mb: number;
    max_video_size_mb: number;
    max_document_size_mb: number;
    max_office_size_mb: number;
    max_text_size_mb: number;
    pdf_quality: number | null;
    video_compression_profile: string | null;
    thumbnail_quality: number | null;
    thumbnail_size_px: number | null;
    allowed_extensions: string | null;
    allowed_mime_types: string | null;
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

export default function AdminConfigPage() {
    const t = useTranslations("Admin.Config");
    const [config, setConfig] = useState<AuthConfig | null>(null);
    const [loading, setLoading] = useState(true);

    const fetchConfig = useCallback(async () => {
        try {
            const data = await apiFetch<AuthConfig>("/admin/auth-config");
            setConfig(data);
        } catch {
            toast.error(t("errors.load"));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        fetchConfig();
    }, [fetchConfig]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (!config) return null;

    return (
        <div className="space-y-6">
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800/40 dark:bg-amber-900/20 dark:text-amber-300">
                <Info className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{t("envBanner")}</span>
            </div>

            <Tabs defaultValue="authentication" className="w-full space-y-6">
                <TabsList className="bg-background border p-1 h-12">
                    <TabsTrigger
                        value="authentication"
                        className="flex items-center gap-2 px-6 data-[state=active]:bg-primary/10 data-[state=active]:text-primary transition-all font-medium"
                    >
                        <Shield className="h-4 w-4" />
                        {t("tabs.authentication")}
                    </TabsTrigger>
                    <TabsTrigger
                        value="email"
                        className="flex items-center gap-2 px-6 data-[state=active]:bg-primary/10 data-[state=active]:text-primary transition-all font-medium"
                    >
                        <Mail className="h-4 w-4" />
                        {t("tabs.email")}
                    </TabsTrigger>
                    <TabsTrigger
                        value="storage"
                        className="flex items-center gap-2 px-6 data-[state=active]:bg-primary/10 data-[state=active]:text-primary transition-all font-medium"
                    >
                        <HardDrive className="h-4 w-4" />
                        {t("tabs.storage")}
                    </TabsTrigger>
                    <TabsTrigger
                        value="files"
                        className="flex items-center gap-2 px-6 data-[state=active]:bg-primary/10 data-[state=active]:text-primary transition-all font-medium"
                    >
                        <FileCode className="h-4 w-4" />
                        {t("tabs.files")}
                    </TabsTrigger>
                    <TabsTrigger
                        value="branding"
                        className="flex items-center gap-2 px-6 data-[state=active]:bg-primary/10 data-[state=active]:text-primary transition-all font-medium"
                    >
                        <Palette className="h-4 w-4" />
                        {t("tabs.branding")}
                    </TabsTrigger>
                </TabsList>

                <AuthConfigTab config={config} />
                <EmailConfigTab config={config} />
                <StorageConfigTab config={config} />
                <FilesConfigTab config={config} />
                <BrandingConfigTab config={config} />
            </Tabs>
        </div>
    );
}
