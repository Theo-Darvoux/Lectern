"use client";

import { useState } from "react";
import {
  Download,
  Trash2,
  Shield,
  AlertTriangle,
  Sun,
  Moon,
  Monitor,
  Zap,
  Globe,
  Info,
  ExternalLink,
  ChevronRight,
} from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { apiFetch } from "@/lib/api-client";
import { performLogout } from "@/lib/auth-sync";
import { useTranslations } from "next-intl";
import { useChangeLocale } from "@/hooks/use-change-locale";
import { useAuthStore, useConfigStore } from "@/lib/stores";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const commitSha = process.env.NEXT_PUBLIC_COMMIT_SHA;

export default function SettingsPage() {
  const t = useTranslations("Settings");
  const tLayout = useTranslations("Layout");
  const tLanguages = useTranslations("Languages");
  const { config } = useConfigStore();
  const repoUrl = config?.repo_url || process.env.NEXT_PUBLIC_REPO_URL || "";
  const shortCommit = commitSha?.slice(0, 7);
  const { locale, changeLocale, isPending: localePending } = useChangeLocale();
  const [exporting, setExporting] = useState(false);
  const { show } = useConfirmDialog();
  const { theme, setTheme } = useTheme();
  const { user, setUser } = useAuthStore();
  const [updating, setUpdating] = useState(false);

  const handleLanguageChange = (newLocale: string) => {
    void changeLocale(newLocale);
  };

  const isStaff = user?.role === "bureau" || user?.role === "vieux" || user?.role === "moderator";

  const handleToggleAutoApprove = async () => {
    if (!user) return;
    setUpdating(true);
    const newValue = !user.auto_approve;
    try {
      const updated = await apiFetch<{ auto_approve: boolean }>("/users/me", {
        method: "PATCH",
        body: JSON.stringify({ auto_approve: newValue }),
      });
      setUser({ ...user, auto_approve: updated.auto_approve });
      toast.success(
        newValue ? t("contributions.autoApproveEnabled") : t("contributions.autoApproveDisabled"),
      );
    } catch {
      toast.error(t("updateFailed"));
    } finally {
      setUpdating(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const data = await apiFetch<Record<string, unknown>>(
        "/users/me/data-export",
      );
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "my-data-export.json";
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("export.success"));
    } catch {
      toast.error(t("export.error"));
    } finally {
      setExporting(false);
    }
  };

  const handleDeleteAccount = () => {
    show(
      t("deleteAccount.confirmTitle"),
      t("deleteAccount.confirmDesc"),
      async () => {
        try {
          await apiFetch("/users/me", { method: "DELETE" });
          performLogout();
          toast.success(
            t("deleteAccount.success"),
          );
          window.location.href = "/login";
        } catch {
          toast.error(t("deleteAccount.error"));
        }
      },
    );
  };

  return (
    <div className="w-full mx-auto max-w-2xl space-y-6 p-6 pb-24 md:pb-6">
      <div className="flex items-center gap-3">
        <Shield className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">{t("title")}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            {theme === "dark" ? (
              <Moon className="h-4 w-4" />
            ) : theme === "light" ? (
              <Sun className="h-4 w-4" />
            ) : (
              <Monitor className="h-4 w-4" />
            )}
            {t("appearance.title")}
          </CardTitle>
          <CardDescription>
            {t("appearance.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2">
            <Button
              variant={theme === "light" ? "default" : "outline"}
              className="w-full"
              onClick={() => setTheme("light")}
            >
              <Sun className="mr-2 h-4 w-4" />
              {t("appearance.light")}
            </Button>
            <Button
              variant={theme === "dark" ? "default" : "outline"}
              className="w-full"
              onClick={() => setTheme("dark")}
            >
              <Moon className="mr-2 h-4 w-4" />
              {t("appearance.dark")}
            </Button>
            <Button
              variant={theme === "system" ? "default" : "outline"}
              className="w-full"
              onClick={() => setTheme("system")}
            >
              <Monitor className="mr-2 h-4 w-4" />
              {t("appearance.system")}
            </Button>
          </div>

          <div className="pt-4 mt-4 border-t">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="text-sm font-medium flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  {t("appearance.language")}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("appearance.languageDesc")}
                </div>
              </div>
              <Select value={locale} onValueChange={handleLanguageChange} disabled={localePending}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="en">{tLanguages("en")}</SelectItem>
                  <SelectItem value="fr">{tLanguages("fr")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {isStaff && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Zap className="h-4 w-4 text-amber-500" />
              {t("contributions.title")}
            </CardTitle>
            <CardDescription>
              {t("contributions.description")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
              <div className="space-y-0.5">
                <p className="text-sm font-medium">{t("contributions.autoApprove")}</p>
                <p className="text-xs text-muted-foreground mr-8">
                  {t("contributions.autoApproveDesc")}
                </p>
              </div>
              <Switch
                checked={!!user?.auto_approve}
                onCheckedChange={handleToggleAutoApprove}
                disabled={updating}
              />
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Download className="h-4 w-4" />
            {t("export.title")}
          </CardTitle>
          <CardDescription>
            {t("export.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={handleExport} disabled={exporting}>
            {exporting ? t("export.preparing") : t("export.button")}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Info className="h-4 w-4" />
            {t("about.title")}
          </CardTitle>
          <CardDescription>{t("about.description")}</CardDescription>
        </CardHeader>
        <CardContent className="divide-y">
          <Link
            href="/privacy"
            className="flex items-center justify-between py-3 text-sm transition-colors hover:text-foreground text-muted-foreground first:pt-0"
          >
            <span>{tLayout("privacyPolicy")}</span>
            <ChevronRight className="h-4 w-4 shrink-0" />
          </Link>
          <Link
            href="/terms"
            className="flex items-center justify-between py-3 text-sm transition-colors hover:text-foreground text-muted-foreground"
          >
            <span>{tLayout("termsOfUse")}</span>
            <ChevronRight className="h-4 w-4 shrink-0" />
          </Link>
          {(config?.organization_url || repoUrl) && (
            <a
              href={config?.organization_url || repoUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-between py-3 text-sm transition-colors hover:text-foreground text-muted-foreground"
            >
              <span>{config?.organization_url ? tLayout("organization") : tLayout("github")}</span>
              <ExternalLink className="h-4 w-4 shrink-0" />
            </a>
          )}
          {shortCommit && repoUrl && (
            <a
              href={`${repoUrl}/commit/${commitSha}`}
              target="_blank"
              rel="noopener noreferrer"
              title={commitSha}
              className="flex items-center justify-between py-3 text-sm transition-colors hover:text-foreground text-muted-foreground last:pb-0"
            >
              <span>{tLayout("commit")}</span>
              <span className="flex items-center gap-1.5 font-mono text-xs">
                #{shortCommit}
                <ExternalLink className="h-4 w-4 shrink-0" />
              </span>
            </a>
          )}
          {config?.footer_text && (
            <p className="pt-3 text-xs text-muted-foreground">{config.footer_text}</p>
          )}
        </CardContent>
      </Card>

      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base text-destructive">
            <Trash2 className="h-4 w-4" />
            {t("deleteAccount.title")}
          </CardTitle>
          <CardDescription>
            {t("deleteAccount.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-start gap-3 rounded-md bg-destructive/5 p-3">
            <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
            <div className="space-y-1 text-sm">
              <p>
                {t("deleteAccount.warning1")}
              </p>
              <div className="text-muted-foreground">
                {t.rich("deleteAccount.warning2", {
                  link: (chunks) => (
                    <a href="/privacy" className="underline hover:text-foreground">
                      {chunks}
                    </a>
                  )
                })}
              </div>
            </div>
          </div>
          <Button
            variant="destructive"
            className="mt-3"
            onClick={handleDeleteAccount}
          >
            {t("deleteAccount.button")}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
