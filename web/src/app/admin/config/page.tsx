"use client";

import { useCallback, useEffect, useState } from "react";
import {
    Shield,
    Mail,
    Loader2,
    HardDrive,
    FileCode,
    Palette,
} from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConfigStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

// Extracted Components
import { AuthConfigTab } from "./components/AuthConfigTab";
import { EmailConfigTab } from "./components/EmailConfigTab";
import { StorageConfigTab } from "./components/StorageConfigTab";
import { FilesConfigTab } from "./components/FilesConfigTab";
import { BrandingConfigTab } from "./components/BrandingConfigTab";

interface AuthConfig {
    totp_enabled: boolean;
    google_oauth_enabled: boolean;
    google_client_id: string | null;
    classic_auth_enabled: boolean;
    allow_all_domains: boolean;
    auto_approve_all_domains: boolean;
    jwt_access_expire_days: number;
    jwt_refresh_expire_days: number;
    domains: any[];
    smtp_host: string | null;
    smtp_ip: string | null;
    smtp_port: number | null;
    smtp_user: string | null;
    smtp_password: string | null;
    smtp_from: string | null;
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
}

export default function AdminConfigPage() {
    const t = useTranslations("Admin.Config");
    const [config, setConfig] = useState<AuthConfig | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const { setConfig: setGlobalConfig } = useConfigStore();

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

    const patchConfig = async (patch: Partial<Omit<AuthConfig, "domains">>) => {
        setSaving(true);
        try {
            const updated = await apiFetch<AuthConfig>("/admin/auth-config", {
                method: "PATCH",
                body: JSON.stringify(patch),
            });
            setConfig(updated);
            setGlobalConfig(updated as any);

            // Broadcast to other tabs
            const bc = new BroadcastChannel("wikint_config_updates");
            bc.postMessage("refresh");
            bc.close();
            
            return updated;
        } catch {
            toast.error(t("errors.save"));
            throw new Error("Save failed");
        } finally {
            setSaving(false);
        }
    };

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

                <AuthConfigTab config={config} saving={saving} patchConfig={patchConfig as any} />
                <EmailConfigTab config={config} saving={saving} patchConfig={patchConfig as any} />
                <StorageConfigTab config={config} saving={saving} patchConfig={patchConfig as any} />
                <FilesConfigTab config={config} saving={saving} patchConfig={patchConfig as any} />
                <BrandingConfigTab config={config} saving={saving} patchConfig={patchConfig as any} />
            </Tabs>
        </div>
    );
}
