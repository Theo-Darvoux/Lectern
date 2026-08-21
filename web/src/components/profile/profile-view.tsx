"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  Calendar,
  Camera,
  CheckCircle2,
  CircleGauge,
  Crown,
  GitPullRequest,
  GraduationCap,
  Highlighter,
  History,
  Loader2,
  MessageSquare,
  Pencil,
  Save,
  Settings,
  Sparkles,
  Star,
  TrendingUp,
  X,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ContributionList } from "@/components/profile/contribution-list";
import { RecentlyViewed } from "@/components/profile/recently-viewed";
import { getApprovedShare, getProfileCompletion } from "@/components/profile/profile-metrics";
import { apiFetch, API_BASE } from "@/lib/api-client";
import { cn } from "@/lib/utils";

export interface UserProfile {
  id: string;
  email?: string;
  display_name: string | null;
  avatar_url: string | null;
  role: string;
  bio: string | null;
  academic_year: string | null;
  created_at: string;
  prs_approved: number;
  prs_total: number;
  annotations_count: number;
  comments_count: number;
  open_pr_count?: number;
  reputation: number;
}

function AnimatedCounter({ value }: { value: number }) {
  const [display, setDisplay] = useState(value === 0 ? 0 : value);

  useEffect(() => {
    if (value === 0) {
      queueMicrotask(() => setDisplay(0));
      return;
    }
    let frame: number;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - startedAt) / 700, 1);
      setDisplay(Math.round((1 - (1 - progress) ** 3) * value));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return <>{display}</>;
}

const STATS = [
  {
    key: "prs_approved",
    labelKey: "approved",
    icon: CheckCircle2,
    color: "text-emerald-600 dark:text-emerald-400",
    iconBg: "bg-emerald-500/10",
  },
  {
    key: "prs_total",
    labelKey: "totalPrs",
    icon: GitPullRequest,
    color: "text-sky-600 dark:text-sky-400",
    iconBg: "bg-sky-500/10",
  },
  {
    key: "annotations_count",
    labelKey: "annotations",
    icon: Highlighter,
    color: "text-amber-600 dark:text-amber-400",
    iconBg: "bg-amber-500/10",
  },
  {
    key: "comments_count",
    labelKey: "comments",
    icon: MessageSquare,
    color: "text-violet-600 dark:text-violet-400",
    iconBg: "bg-violet-500/10",
  },
] as const;

function roleStyles(role: string) {
  if (role === "bureau") {
    return {
      accent: "bg-amber-500",
      wash: "from-amber-500/14 via-orange-500/5 to-transparent",
      avatar: "from-amber-500 to-orange-600",
      badge: "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
      icon: Crown,
    };
  }
  if (role === "vieux") {
    return {
      accent: "bg-violet-500",
      wash: "from-violet-500/14 via-fuchsia-500/5 to-transparent",
      avatar: "from-violet-500 to-fuchsia-600",
      badge: "border-violet-500/25 bg-violet-500/10 text-violet-700 dark:text-violet-300",
      icon: Sparkles,
    };
  }
  if (role === "moderator") {
    return {
      accent: "bg-sky-500",
      wash: "from-sky-500/12 via-cyan-500/5 to-transparent",
      avatar: "from-sky-500 to-cyan-600",
      badge: "border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300",
      icon: null,
    };
  }
  return {
    accent: "bg-primary",
    wash: "from-primary/12 via-primary/5 to-transparent",
    avatar: "from-primary to-primary/70",
    badge: "border-border bg-muted/70 text-muted-foreground",
    icon: null,
  };
}

function EditProfileForm({
  profile,
  onSave,
  onCancel,
}: {
  profile: UserProfile;
  onSave: (updated: UserProfile) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("Profile");
  const [name, setName] = useState(profile.display_name ?? "");
  const [bio, setBio] = useState(profile.bio ?? "");
  const [year, setYear] = useState(profile.academic_year ?? "");
  const [saving, setSaving] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const updated = await apiFetch<UserProfile>("/users/me", {
        method: "PATCH",
        body: JSON.stringify({
          display_name: name || undefined,
          bio,
          academic_year: year || undefined,
        }),
      });
      toast.success(t("profileUpdated"));
      onSave(updated);
    } catch {
      toast.error(t("profileUpdateFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="overflow-hidden rounded-2xl border bg-card shadow-sm">
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4 sm:px-6">
        <div>
          <h2 className="font-semibold">{t("editProfile")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("editProfileDescription")}</p>
        </div>
        <Button type="button" variant="ghost" size="icon" onClick={onCancel} aria-label={t("cancel")}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid gap-5 p-5 sm:grid-cols-2 sm:p-6">
        <div className="space-y-2">
          <Label htmlFor="displayName">{t("displayName")}</Label>
          <Input id="displayName" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="academicYear">{t("academicYear")}</Label>
          <Select value={year} onValueChange={setYear}>
            <SelectTrigger id="academicYear">
              <SelectValue placeholder={t("selectYear")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1A">1A</SelectItem>
              <SelectItem value="2A">2A</SelectItem>
              <SelectItem value="3A+">3A+</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2 sm:col-span-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="bio">{t("bio")}</Label>
            <span className="text-xs tabular-nums text-muted-foreground">{bio.length}/500</span>
          </div>
          <Textarea
            id="bio"
            value={bio}
            onChange={(event) => setBio(event.target.value.slice(0, 500))}
            className="min-h-28 resize-none"
            placeholder={t("bioPlaceholder")}
          />
        </div>
      </div>

      <div className="flex justify-end gap-2 border-t bg-muted/20 px-5 py-4 sm:px-6">
        <Button type="button" variant="ghost" onClick={onCancel}>{t("cancel")}</Button>
        <Button type="submit" disabled={saving}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
          {saving ? t("saving") : t("save")}
        </Button>
      </div>
    </form>
  );
}

export function ProfileSkeleton() {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 pb-24 sm:p-6 lg:p-8">
      <div className="grid overflow-hidden rounded-2xl border lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-5 p-6 sm:p-8">
          <div className="flex gap-5">
            <Skeleton className="h-24 w-24 shrink-0 rounded-2xl" />
            <div className="flex-1 space-y-3 pt-2">
              <Skeleton className="h-8 w-56" />
              <Skeleton className="h-4 w-40" />
            </div>
          </div>
          <Skeleton className="h-16 w-full" />
        </div>
        <Skeleton className="min-h-64 rounded-none" />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-28 rounded-xl" />)}
      </div>
      <Skeleton className="h-96 rounded-2xl" />
    </div>
  );
}

interface ProfileViewProps {
  profile: UserProfile;
  isOwn: boolean;
  onAvatarUpload?: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onProfileUpdated?: (updated: UserProfile) => void;
  showRecentlyViewed?: boolean;
  isUploadingAvatar?: boolean;
}

export function ProfileView({
  profile,
  isOwn,
  onAvatarUpload,
  onProfileUpdated,
  showRecentlyViewed = false,
  isUploadingAvatar = false,
}: ProfileViewProps) {
  const t = useTranslations("Profile");
  const tRoles = useTranslations("Roles");
  const locale = useLocale();
  const [editing, setEditing] = useState(false);
  const [activeTab, setActiveTab] = useState("prs");

  const initials = (profile.display_name ?? profile.email ?? "?")
    .split(" ")
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const joined = new Date(profile.created_at).toLocaleDateString(locale, {
    month: "short",
    year: "numeric",
  });
  const styles = roleStyles(profile.role);
  const RoleIcon = styles.icon;
  const completion = getProfileCompletion(profile);
  const approvedShare = getApprovedShare(profile.prs_approved, profile.prs_total);

  const tabs = [
    { value: "prs", label: t("contributions"), icon: GitPullRequest },
    { value: "materials", label: t("materials"), icon: BookOpen },
    { value: "annotations", label: t("annotations"), icon: Highlighter },
    ...(showRecentlyViewed ? [{ value: "recent", label: t("recentlyViewed"), icon: History }] : []),
  ];

  return (
    <div className="min-h-full w-full bg-muted/20">
      <div className="mx-auto w-full max-w-6xl space-y-5 p-4 pb-24 sm:p-6 md:pb-8 lg:p-8">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">{isOwn ? t("yourProfile") : t("communityProfile")}</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{t("profileOverview")}</h1>
          </div>
          {isOwn && (
            <div className="flex gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link href="/settings"><Settings className="mr-2 h-4 w-4" />{t("settings")}</Link>
              </Button>
              {!editing && (
                <Button size="sm" onClick={() => setEditing(true)}>
                  <Pencil className="mr-2 h-4 w-4" />{t("editProfile")}
                </Button>
              )}
            </div>
          )}
        </div>

        <section className="relative overflow-hidden rounded-2xl border bg-card shadow-sm">
          <div className={cn("absolute inset-x-0 top-0 h-1", styles.accent)} />
          <div className={cn("pointer-events-none absolute inset-0 bg-gradient-to-br", styles.wash)} />
          <div className="relative grid lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="p-5 sm:p-8">
              <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
                <div className="group relative w-fit shrink-0">
                  <Avatar className="h-24 w-24 rounded-2xl border-4 border-background shadow-lg sm:h-28 sm:w-28">
                    <AvatarImage
                      src={profile.avatar_url ? `${API_BASE}/users/${profile.id}/avatar?v=${encodeURIComponent(profile.avatar_url)}` : undefined}
                      alt={profile.display_name ?? ""}
                    />
                    <AvatarFallback className={cn("rounded-xl bg-gradient-to-br text-2xl font-bold text-white", styles.avatar)}>{initials}</AvatarFallback>
                    {isUploadingAvatar && (
                      <div className="absolute inset-0 z-20 flex items-center justify-center rounded-xl bg-black/50">
                        <Loader2 className="h-6 w-6 animate-spin text-white" />
                      </div>
                    )}
                  </Avatar>
                  {isOwn && onAvatarUpload && (
                    <label className="absolute inset-1 flex cursor-pointer items-center justify-center rounded-xl bg-black/55 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                      <Camera className="h-5 w-5 text-white" />
                      <span className="sr-only">{t("changeAvatar")}</span>
                      <input type="file" accept="image/*" className="sr-only" onChange={onAvatarUpload} />
                    </label>
                  )}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-2xl font-bold tracking-tight sm:text-3xl">{profile.display_name ?? profile.email ?? "?"}</h2>
                    {RoleIcon && <RoleIcon className="h-5 w-5 text-current opacity-60" aria-hidden="true" />}
                    <Badge variant="outline" className={cn("capitalize", styles.badge)}>{tRoles(profile.role as never)}</Badge>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground">
                    {profile.academic_year && (
                      <span className="inline-flex items-center gap-1.5"><GraduationCap className="h-4 w-4" />{t("year", { year: profile.academic_year })}</span>
                    )}
                    <span className="inline-flex items-center gap-1.5"><Calendar className="h-4 w-4" />{t("joined", { date: joined })}</span>
                    {isOwn && profile.email && <span className="truncate">{profile.email}</span>}
                  </div>
                  {profile.bio ? (
                    <p className="mt-5 max-w-2xl whitespace-pre-wrap text-sm leading-6 text-foreground/80">{profile.bio}</p>
                  ) : isOwn ? (
                    <button onClick={() => setEditing(true)} className="mt-5 text-left text-sm text-muted-foreground underline decoration-dashed underline-offset-4 hover:text-foreground">
                      {t("addBio")}
                    </button>
                  ) : (
                    <p className="mt-5 text-sm text-muted-foreground">{t("noBio")}</p>
                  )}
                </div>
              </div>
            </div>

            <aside className="border-t bg-background/55 p-5 backdrop-blur-sm sm:p-6 lg:border-l lg:border-t-0">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("communityImpact")}</p>
                  <div className="mt-2 flex items-baseline gap-2">
                    <span className="text-4xl font-bold tracking-tight tabular-nums">{profile.reputation}</span>
                    <span className="text-sm text-muted-foreground">{t("reputationPoints")}</span>
                  </div>
                </div>
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
                  <Star className="h-5 w-5 fill-current" />
                </div>
              </div>

              <div className={cn("mt-6 grid gap-3", profile.open_pr_count !== undefined && "grid-cols-2")}>
                <div className="rounded-xl border bg-background/70 p-3">
                  <TrendingUp className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                  <p className="mt-3 text-xl font-bold tabular-nums">{profile.prs_total > 0 ? `${approvedShare}%` : "—"}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{t("approvedShare")}</p>
                </div>
                {profile.open_pr_count !== undefined && (
                  <div className="rounded-xl border bg-background/70 p-3">
                    <CircleGauge className="h-4 w-4 text-sky-600 dark:text-sky-400" />
                    <p className="mt-3 text-xl font-bold tabular-nums">{profile.open_pr_count}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t("openContributions")}</p>
                  </div>
                )}
              </div>

              {isOwn && (
                <div className="mt-5 border-t pt-5">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">{t("profileCompletion")}</span>
                    <span className="font-semibold tabular-nums">{completion}%</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div className={cn("h-full rounded-full transition-all", styles.accent)} style={{ width: `${completion}%` }} />
                  </div>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">{completion === 100 ? t("profileComplete") : t("profileCompletionHint")}</p>
                </div>
              )}
            </aside>
          </div>
        </section>

        {editing && (
          <EditProfileForm
            profile={profile}
            onSave={(updated) => {
              setEditing(false);
              onProfileUpdated?.(updated);
            }}
            onCancel={() => setEditing(false)}
          />
        )}

        <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label={t("contributionSnapshot")}>
          {STATS.map((stat) => {
            const Icon = stat.icon;
            return (
              <div key={stat.key} className="group rounded-xl border bg-card p-4 shadow-sm transition-colors hover:border-primary/25">
                <div className="flex items-start gap-3">
                  <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg", stat.iconBg, stat.color)}>
                    <Icon className="h-4 w-4" />
                  </div>
                </div>
                <p className="mt-5 text-2xl font-bold tracking-tight tabular-nums sm:text-3xl"><AnimatedCounter value={profile[stat.key]} /></p>
                <p className="mt-1 text-xs font-medium text-muted-foreground">{t(stat.labelKey)}</p>
              </div>
            );
          })}
        </section>

        <section className="overflow-hidden rounded-2xl border bg-card shadow-sm">
          <div className="border-b px-5 py-5 sm:px-6">
            <h2 className="text-lg font-semibold">{t("activityTitle")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{isOwn ? t("activityDescriptionOwn") : t("activityDescription")}</p>
          </div>
          <Tabs value={activeTab} onValueChange={setActiveTab} className="p-3 sm:p-5">
            <TabsList className="flex h-auto w-full justify-start gap-1 overflow-x-auto rounded-xl bg-muted/60 p-1">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <TabsTrigger key={tab.value} value={tab.value} className="min-h-9 shrink-0 gap-2 rounded-lg px-3 data-[state=active]:bg-background data-[state=active]:shadow-sm">
                    <Icon className="h-3.5 w-3.5" />{tab.label}
                  </TabsTrigger>
                );
              })}
            </TabsList>
            <TabsContent value="prs" className="mt-4 min-h-72">{activeTab === "prs" && <ContributionList userId={profile.id} type="prs" />}</TabsContent>
            <TabsContent value="materials" className="mt-4 min-h-72">{activeTab === "materials" && <ContributionList userId={profile.id} type="materials" />}</TabsContent>
            <TabsContent value="annotations" className="mt-4 min-h-72">{activeTab === "annotations" && <ContributionList userId={profile.id} type="annotations" />}</TabsContent>
            {showRecentlyViewed && (
              <TabsContent value="recent" className="mt-4 min-h-72">{activeTab === "recent" && <RecentlyViewed />}</TabsContent>
            )}
          </Tabs>
        </section>
      </div>
    </div>
  );
}
