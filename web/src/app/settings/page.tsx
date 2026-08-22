"use client";

import { useState, type ElementType, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  AlertTriangle,
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Database,
  Download,
  ExternalLink,
  Globe,
  Info,
  KeyRound,
  Monitor,
  Moon,
  Palette,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Trash2,
  UserRound,
  Zap,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useConfirmDialog } from "@/components/confirm-dialog";
import { useChangeLocale } from "@/hooks/use-change-locale";
import { apiFetch } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import { performLogout } from "@/lib/auth-sync";
import { useAuthStore, useConfigStore } from "@/lib/stores";
import { cn } from "@/lib/utils";

const commitSha = process.env.NEXT_PUBLIC_COMMIT_SHA;
type SetPasswordRequest = components["schemas"]["SetPasswordIn"];

function SettingsSection({
  id,
  icon: Icon,
  title,
  description,
  children,
  destructive = false,
}: {
  id: string;
  icon: ElementType;
  title: string;
  description: string;
  children: ReactNode;
  destructive?: boolean;
}) {
  return (
    <section id={id} className={cn("scroll-mt-6 overflow-hidden rounded-2xl border bg-card shadow-xs", destructive && "border-destructive/25")}>
      <div className="flex items-center gap-3 border-b px-4 py-3.5 sm:px-6 sm:py-4">
        <div className={cn("flex h-9 w-9 sm:h-10 sm:w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary", destructive && "bg-destructive/10 text-destructive")}>
          <Icon className="h-4 w-4 sm:h-5 sm:w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className={cn("text-sm sm:text-base font-semibold", destructive && "text-destructive")}>{title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground sm:text-sm">{description}</p>
        </div>
      </div>
      <div className="p-4 sm:p-6">{children}</div>
    </section>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const t = useTranslations("Settings");
  const tLayout = useTranslations("Layout");
  const tLanguages = useTranslations("Languages");
  const config = useConfigStore((state) => state.config);
  const repoUrl = config?.repo_url || process.env.NEXT_PUBLIC_REPO_URL || "";
  const shortCommit = commitSha?.slice(0, 7);
  const { locale, changeLocale, isPending: localePending } = useChangeLocale();
  const { show } = useConfirmDialog();
  const { theme, setTheme } = useTheme();
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const [exporting, setExporting] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [creatingPassword, setCreatingPassword] = useState(false);

  const isStaff = user?.role === "bureau" || user?.role === "vieux" || user?.role === "moderator";
  const canCreatePassword = config?.classic_enabled === true && user?.has_password === false;
  const navItems = [
    { id: "general", label: t("navigation.general"), icon: SlidersHorizontal },
    ...(isStaff ? [{ id: "contributions", label: t("navigation.contributions"), icon: Zap }] : []),
    ...(canCreatePassword ? [{ id: "security", label: t("navigation.security"), icon: KeyRound }] : []),
    { id: "privacy", label: t("navigation.privacy"), icon: ShieldCheck },
    { id: "about", label: t("navigation.about"), icon: Info },
    { id: "danger", label: t("navigation.danger"), icon: AlertTriangle },
  ];
  const themeOptions = [
    { value: "light", label: t("appearance.light"), description: t("appearance.lightDesc"), icon: Sun },
    { value: "dark", label: t("appearance.dark"), description: t("appearance.darkDesc"), icon: Moon },
    { value: "system", label: t("appearance.system"), description: t("appearance.systemDesc"), icon: Monitor },
  ];

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
      toast.success(newValue ? t("contributions.autoApproveEnabled") : t("contributions.autoApproveDisabled"));
    } catch {
      toast.error(t("updateFailed"));
    } finally {
      setUpdating(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const data = await apiFetch<Record<string, unknown>>("/users/me/data-export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "my-data-export.json";
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(t("export.success"));
    } catch {
      toast.error(t("export.error"));
    } finally {
      setExporting(false);
    }
  };

  const handleCreatePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!user) return;
    if (password.length < 8) {
      toast.error(t("password.tooShort"));
      return;
    }
    if (password !== confirmPassword) {
      toast.error(t("password.mismatch"));
      return;
    }

    setCreatingPassword(true);
    try {
      const payload: SetPasswordRequest = { password };
      await apiFetch<Record<string, string>>("/auth/password", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setUser({ ...user, has_password: true });
      setPassword("");
      setConfirmPassword("");
      toast.success(t("password.success"));
    } catch {
      toast.error(t("password.error"));
    } finally {
      setCreatingPassword(false);
    }
  };

  const handleDeleteAccount = () => {
    show(t("deleteAccount.confirmTitle"), t("deleteAccount.confirmDesc"), async () => {
      try {
        await apiFetch("/users/me", { method: "DELETE" });
        performLogout();
        toast.success(t("deleteAccount.success"));
        router.push("/login");
      } catch {
        toast.error(t("deleteAccount.error"));
      }
    });
  };

  return (
    <div className="w-full">
      <div className="mx-auto w-full max-w-6xl p-4 pb-12 sm:p-6 md:pb-8 lg:p-8">
        <header className="flex items-center justify-between gap-3 border-b pb-4 sm:pb-6">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary sm:text-xs">{t("eyebrow")}</p>
            <h1 className="mt-0.5 text-2xl font-bold tracking-tight sm:text-3xl">{t("title")}</h1>
            <p className="hidden sm:block mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground sm:text-sm">{t("description")}</p>
          </div>
          <Button variant="outline" size="sm" asChild className="shrink-0 h-9">
            <Link href="/profile">
              <UserRound className="mr-1.5 h-4 w-4 shrink-0" />
              <span className="truncate max-w-28 sm:max-w-44">{user?.display_name || t("viewProfile")}</span>
              <ArrowUpRight className="ml-1 h-3.5 w-3.5 text-muted-foreground" />
            </Link>
          </Button>
        </header>

        <div className="mt-6 lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:items-start lg:gap-6">
          <aside className="hidden lg:block lg:sticky lg:top-6">
            <nav
              aria-label={t("navigation.label")}
              className="flex flex-col gap-1 rounded-xl border bg-card p-1.5 shadow-xs"
            >
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <a
                    key={item.id}
                    href={`#${item.id}`}
                    className="flex min-h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span>{item.label}</span>
                  </a>
                );
              })}
            </nav>
          </aside>

          <div className="min-w-0 space-y-5">
            <SettingsSection id="general" icon={Palette} title={t("appearance.title")} description={t("appearance.description")}>
              <div>
                <p className="text-xs font-medium sm:text-sm">{t("appearance.theme")}</p>
                <div className="mt-2.5 grid grid-cols-3 gap-2 sm:gap-3">
                  {themeOptions.map((option) => {
                    const Icon = option.icon;
                    const selected = theme === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setTheme(option.value)}
                        aria-pressed={selected}
                        className={cn(
                          "group relative flex flex-col items-center justify-center gap-1.5 rounded-xl border p-2.5 text-center transition-all hover:border-primary/30 hover:bg-muted/30 sm:items-start sm:p-4 sm:text-left",
                          selected && "border-primary bg-primary/5 ring-1 ring-primary/20",
                        )}
                      >
                        <div className="flex w-full items-center justify-center sm:justify-between">
                          <div className={cn("flex h-7 w-7 sm:h-8 sm:w-8 items-center justify-center rounded-lg transition-colors", selected ? "bg-primary/10 text-primary" : "bg-muted/70 text-muted-foreground group-hover:text-foreground")}>
                            <Icon className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                          </div>
                          <span className={cn("hidden h-2 w-2 rounded-full border sm:inline-block", selected && "border-primary bg-primary")} />
                        </div>
                        <p className="text-xs font-semibold sm:mt-2 sm:text-sm">{option.label}</p>
                        <p className="hidden text-xs leading-4 text-muted-foreground sm:block sm:mt-0.5">{option.description}</p>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3.5">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Globe className="h-4 w-4" /></div>
                  <div className="min-w-0">
                    <p className="text-xs font-medium sm:text-sm">{t("appearance.language")}</p>
                    <p className="hidden sm:block text-xs text-muted-foreground">{t("appearance.languageDesc")}</p>
                  </div>
                </div>
                <Select value={locale} onValueChange={(nextLocale) => void changeLocale(nextLocale)} disabled={localePending}>
                  <SelectTrigger className="w-32 sm:w-44 h-8 sm:h-9 text-xs sm:text-sm shrink-0"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="en">{tLanguages("en")}</SelectItem>
                    <SelectItem value="fr">{tLanguages("fr")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {config?.tutorials_enabled !== false && (
                <div className="mt-3.5 flex items-center justify-between gap-3 border-t pt-3.5">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><CircleHelp className="h-4 w-4" /></div>
                    <div className="min-w-0">
                      <p className="text-xs font-medium sm:text-sm">{t("guidance.title")}</p>
                      <p className="hidden sm:block text-xs text-muted-foreground">{t("guidance.description")}</p>
                    </div>
                  </div>
                  <Button variant="outline" size="sm" asChild className="h-8 sm:h-9 text-xs sm:text-sm shrink-0">
                    <Link href="/help">{t("guidance.button")}<ChevronRight className="ml-1 h-3.5 w-3.5" /></Link>
                  </Button>
                </div>
              )}
            </SettingsSection>

            {isStaff && (
              <SettingsSection id="contributions" icon={Zap} title={t("contributions.title")} description={t("contributions.description")}>
                <div className="flex items-start justify-between gap-3 rounded-xl border bg-muted/20 p-3 sm:p-4">
                  <div className="min-w-0 pr-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-xs font-medium sm:text-sm">{t("contributions.autoApprove")}</p>
                      <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">{t("contributions.staffOnly")}</span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("contributions.autoApproveDesc")}</p>
                  </div>
                  <Switch checked={!!user?.auto_approve} onCheckedChange={handleToggleAutoApprove} disabled={updating} aria-label={t("contributions.autoApprove")} className="shrink-0 mt-0.5" />
                </div>
              </SettingsSection>
            )}

            {canCreatePassword && (
              <SettingsSection id="security" icon={KeyRound} title={t("password.title")} description={t("password.description")}>
                <form onSubmit={handleCreatePassword} className="max-w-xl space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="settings-new-password" className="text-xs sm:text-sm">{t("password.newPassword")}</Label>
                    <Input
                      id="settings-new-password"
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      minLength={8}
                      maxLength={128}
                      autoComplete="new-password"
                      required
                      disabled={creatingPassword}
                    />
                    <p className="text-xs text-muted-foreground">{t("password.hint")}</p>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="settings-confirm-password" className="text-xs sm:text-sm">{t("password.confirmPassword")}</Label>
                    <Input
                      id="settings-confirm-password"
                      type="password"
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      minLength={8}
                      maxLength={128}
                      autoComplete="new-password"
                      required
                      disabled={creatingPassword}
                    />
                  </div>
                  <Button type="submit" disabled={creatingPassword || !password || !confirmPassword} className="w-full sm:w-auto">
                    <KeyRound className="mr-2 h-4 w-4" />
                    {creatingPassword ? t("password.creating") : t("password.button")}
                  </Button>
                </form>
              </SettingsSection>
            )}

            <SettingsSection id="privacy" icon={ShieldCheck} title={t("privacy.title")} description={t("privacy.description")}>
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 sm:h-10 sm:w-10 shrink-0 items-center justify-center rounded-xl bg-sky-500/10 text-sky-600 dark:text-sky-400"><Database className="h-4 w-4 sm:h-5 sm:w-5" /></div>
                  <div>
                    <p className="text-xs font-medium sm:text-sm">{t("export.title")}</p>
                    <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-muted-foreground">{t("export.description")}</p>
                  </div>
                </div>
                <Button variant="outline" onClick={handleExport} disabled={exporting} className="w-full sm:w-auto shrink-0">
                  <Download className="mr-2 h-4 w-4" />{exporting ? t("export.preparing") : t("export.button")}
                </Button>
              </div>
              <div className="mt-4 flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                <span>{t("privacy.exportNote")}</span>
              </div>
            </SettingsSection>

            <SettingsSection id="about" icon={Info} title={t("about.title")} description={t("about.description")}>
              <div className="divide-y text-xs sm:text-sm">
                <Link href="/privacy" className="group flex items-center justify-between py-2.5 sm:py-3 first:pt-0">
                  <span>{tLayout("privacyPolicy")}</span><ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </Link>
                <Link href="/terms" className="group flex items-center justify-between py-2.5 sm:py-3">
                  <span>{tLayout("termsOfUse")}</span><ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </Link>
                {(config?.organization_url || repoUrl) && (
                  <a href={config?.organization_url || repoUrl} target="_blank" rel="noopener noreferrer" className="flex items-center justify-between py-2.5 sm:py-3">
                    <span>{config?.organization_url ? tLayout("organization") : tLayout("github")}</span><ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                  </a>
                )}
                {shortCommit && repoUrl && (
                  <a href={`${repoUrl}/commit/${commitSha}`} target="_blank" rel="noopener noreferrer" title={commitSha} className="flex items-center justify-between py-2.5 sm:py-3">
                    <span>{tLayout("commit")}</span><span className="flex items-center gap-1 font-mono text-[11px] sm:text-xs text-muted-foreground">#{shortCommit}<ExternalLink className="h-3 w-3" /></span>
                  </a>
                )}
                {config?.footer_text && <p className="pt-2.5 text-xs leading-relaxed text-muted-foreground">{config.footer_text}</p>}
              </div>
            </SettingsSection>

            <SettingsSection id="danger" icon={Trash2} title={t("deleteAccount.title")} description={t("deleteAccount.description")} destructive>
              <div className="rounded-xl border border-destructive/15 bg-destructive/5 p-3.5 sm:p-4">
                <div className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                  <div className="space-y-1.5 text-xs sm:text-sm">
                    <p className="font-medium">{t("deleteAccount.warning1")}</p>
                    <div className="text-xs leading-relaxed text-muted-foreground">
                      {t.rich("deleteAccount.warning2", {
                        link: (chunks) => <a href="/privacy" className="underline underline-offset-2 hover:text-foreground">{chunks}</a>,
                      })}
                    </div>
                  </div>
                </div>
                <Button variant="destructive" className="w-full sm:w-auto mt-3.5" onClick={handleDeleteAccount}>
                  <Trash2 className="mr-2 h-4 w-4" />{t("deleteAccount.button")}
                </Button>
              </div>
            </SettingsSection>
            <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
          </div>
        </div>
      </div>
    </div>
  );
}
