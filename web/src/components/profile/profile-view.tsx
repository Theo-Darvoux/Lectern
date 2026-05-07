"use client";

import React, { useState, useEffect } from "react";
import {
  Camera,
  Calendar,
  GraduationCap,
  Star,
  GitPullRequest,
  CheckCircle2,
  MessageSquare,
  Highlighter,
  Pencil,
  Save,
  X,
  Crown,
  Sparkles,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ContributionList } from "@/components/profile/contribution-list";
import { RecentlyViewed } from "@/components/profile/recently-viewed";
import { apiFetch, API_BASE } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations, useLocale } from "next-intl";

/* ────────────────────────────────────────────────────────────────────────── */
/*  Types                                                                     */
/* ────────────────────────────────────────────────────────────────────────── */

export interface UserProfile {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  role: string;
  bio: string | null;
  academic_year: string | null;
  onboarded: boolean;
  auto_approve: boolean;
  created_at: string;
  prs_approved: number;
  prs_total: number;
  annotations_count: number;
  comments_count: number;
  open_pr_count?: number;
  reputation: number;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Animated counter                                                          */
/* ────────────────────────────────────────────────────────────────────────── */

function AnimatedCounter({ value }: { value: number }) {
  const [display, setDisplay] = useState(value === 0 ? 0 : value);

  useEffect(() => {
    if (value === 0) {
      queueMicrotask(() => setDisplay(0));
      return;
    }
    let raf: number;
    const dur = 900;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - t0) / dur, 1);
      setDisplay(Math.round((1 - (1 - p) ** 3) * value));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  return <>{display}</>;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Floating particles                                                        */
/* ────────────────────────────────────────────────────────────────────────── */

function FloatingParticles({ variant }: { variant: "bureau" | "vieux" }) {
  const [particles, setParticles] = useState<
    Array<{
      id: number;
      left: number;
      delay: number;
      dur: number;
      size: number;
      drift: number;
    }>
  >([]);

  useEffect(() => {
    const n = variant === "bureau" ? 32 : 24;
    const pts = Array.from({ length: n }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      delay: Math.random() * 8,
      dur: 3 + Math.random() * 6,
      size:
        variant === "bureau"
          ? 2 + Math.random() * 4
          : 2.5 + Math.random() * 4.5,
      drift: -40 + Math.random() * 80,
    }));
    queueMicrotask(() => setParticles(pts));
  }, [variant]);

  return (
    <div
      className="pointer-events-none absolute inset-0 overflow-hidden"
      aria-hidden="true"
    >
      {particles.map((p) => (
        <div
          key={p.id}
          className={
            variant === "bureau"
              ? "absolute rounded-full bg-yellow-100/90 shadow-[0_0_10px_3px_rgba(253,224,71,0.5)] dark:bg-amber-200/90 dark:shadow-[0_0_10px_3px_rgba(251,191,36,0.5)]"
              : "absolute rounded-[2px] rotate-45 bg-fuchsia-100/90 shadow-[0_0_10px_3px_rgba(232,121,249,0.5)] dark:bg-purple-200/90 dark:shadow-[0_0_10px_3px_rgba(192,132,252,0.5)]"
          }
          style={{
            left: `${p.left}%`,
            bottom: "-5%",
            width: p.size,
            height: p.size,
            animation: `pf-float ${p.dur}s ${p.delay}s infinite ease-out`,
            ["--pf-drift" as string]: `${p.drift}px`,
          }}
        />
      ))}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Inline style helpers (avoids Tailwind v4 class stripping)                 */
/* ────────────────────────────────────────────────────────────────────────── */

/** Animated flowing gradient for the banner */
function getBannerAnimStyle(role: string): React.CSSProperties | undefined {
  if (role === "bureau" || role === "vieux") {
    return {
      backgroundSize: "400% 400%",
      animation: `pf-banner-flow ${role === "bureau" ? 12 : 15}s ease infinite alternate`,
    };
  }
  return undefined;
}

/** Pulsing ring glow on avatar */
function getRingAnimStyle(role: string): React.CSSProperties | undefined {
  if (role === "bureau")
    return { animation: "pf-ring-gold 3s ease-in-out infinite" };
  if (role === "vieux")
    return { animation: "pf-ring-purple 3s ease-in-out infinite" };
  return undefined;
}

/** Shimmering gradient text via CSS custom property */
function getShimmerStyle(role: string): React.CSSProperties | undefined {
  if (role === "bureau")
    return {
      backgroundImage: "var(--pf-gold-shimmer)",
      backgroundSize: "200% auto",
      WebkitBackgroundClip: "text",
      backgroundClip: "text",
      WebkitTextFillColor: "transparent",
      animation: "pf-shimmer 3s linear infinite",
    };
  if (role === "vieux")
    return {
      backgroundImage: "var(--pf-purple-shimmer)",
      backgroundSize: "200% auto",
      WebkitBackgroundClip: "text",
      backgroundClip: "text",
      WebkitTextFillColor: "transparent",
      animation: "pf-shimmer 3.5s linear infinite",
    };
  return undefined;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Stat cards                                                                */
/* ────────────────────────────────────────────────────────────────────────── */

const STATS = [
  {
    key: "prs_approved",
    label: "Approved",
    icon: CheckCircle2,
    color: "text-emerald-500",
    bg: "bg-emerald-500/10",
    bar: "bg-emerald-500",
  },
  {
    key: "prs_total",
    label: "Total PRs",
    icon: GitPullRequest,
    color: "text-blue-500",
    bg: "bg-blue-500/10",
    bar: "bg-blue-500",
  },
  {
    key: "annotations_count",
    label: "Annotations",
    icon: Highlighter,
    color: "text-amber-500",
    bg: "bg-amber-500/10",
    bar: "bg-amber-500",
  },
  {
    key: "comments_count",
    label: "Comments",
    icon: MessageSquare,
    color: "text-violet-500",
    bg: "bg-violet-500/10",
    bar: "bg-violet-500",
  },
] as const;

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  bg,
  bar,
  delay,
}: {
  icon: React.ElementType;
  label: string;
  value: number;
  color: string;
  bg: string;
  bar: string;
  delay: number;
}) {
  return (
    <div
      className="group relative overflow-hidden rounded-xl border bg-background/80 backdrop-blur-sm p-4 transition-all duration-300 hover:shadow-md hover:-translate-y-0.5 dark:bg-card/60"
      style={{ animation: `pf-fade-up 0.5s ${delay}s both ease-out` }}
    >
      <div
        className={`absolute inset-x-0 top-0 h-px ${bar} opacity-40 transition-opacity group-hover:opacity-100`}
      />
      <div className={`mb-3 inline-flex rounded-lg p-2.5 ${bg} ${color}`}>
        <Icon className="h-4 w-4" />
      </div>
      <p className="text-3xl font-extrabold tracking-tight tabular-nums text-foreground/90">
        <AnimatedCounter value={value} />
      </p>
      <p className="mt-1 text-xs font-medium text-muted-foreground">{label}</p>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Role badge                                                                */
/* ────────────────────────────────────────────────────────────────────────── */

function getRoleBadgeClasses(role: string): string {
  switch (role) {
    case "bureau":
      return "border-amber-400/50 bg-gradient-to-r from-amber-100 to-yellow-50 text-amber-800 font-semibold dark:border-amber-600/50 dark:from-amber-950/60 dark:to-yellow-950/40 dark:text-amber-200";
    case "vieux":
      return "border-purple-400/50 bg-gradient-to-r from-purple-100 to-fuchsia-50 text-purple-800 font-semibold dark:border-purple-600/50 dark:from-purple-950/60 dark:to-fuchsia-950/40 dark:text-purple-200";
    case "moderator":
      return "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-950/40 dark:text-blue-300";
    default:
      return "";
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Edit form                                                                 */
/* ────────────────────────────────────────────────────────────────────────── */

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

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await apiFetch<UserProfile>("/users/me", {
        method: "PATCH",
        body: JSON.stringify({
          display_name: name || undefined,
          bio: bio,
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
    <form
      onSubmit={submit}
      className="space-y-4 rounded-xl border bg-background/60 p-5 backdrop-blur-sm"
      style={{ animation: "pf-fade-up 0.35s ease-out" }}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{t("editProfile")}</h3>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onCancel}
          className="h-8 w-8"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="displayName">{t("displayName")}</Label>
        <Input
          id="displayName"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="bio">{t("bio")}</Label>
        <Textarea
          id="bio"
          value={bio}
          onChange={(e) => setBio(e.target.value.slice(0, 500))}
          className="min-h-[80px] resize-none"
          placeholder={t("bioPlaceholder")}
        />
        <p className="text-right text-[10px] text-muted-foreground">
          {bio.length}/500
        </p>
      </div>
      <div className="space-y-1.5">
        <Label>{t("academicYear")}</Label>
        <Select value={year} onValueChange={setYear}>
          <SelectTrigger>
            <SelectValue placeholder={t("selectYear")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="1A">1A</SelectItem>
            <SelectItem value="2A">2A</SelectItem>
            <SelectItem value="3A+">3A+</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex gap-2 pt-1">
        <Button type="submit" size="sm" disabled={saving}>
          <Save className="mr-1.5 h-3.5 w-3.5" />
          {saving ? t("saving") : t("save")}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
    </form>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Skeleton                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */

export function ProfileSkeleton() {
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="relative overflow-hidden rounded-2xl border">
        <Skeleton className="h-36 rounded-none" />
        <div className="relative px-6 pb-6">
          <Skeleton className="-mt-14 h-28 w-28 rounded-full border-4 border-background" />
          <div className="mt-4 space-y-2.5">
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-4 w-40" />
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-[108px] rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-48 w-full rounded-xl" />
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Main profile view                                                         */
/* ────────────────────────────────────────────────────────────────────────── */

interface ProfileViewProps {
  profile: UserProfile;
  isOwn: boolean;
  onAvatarUpload?: (e: React.ChangeEvent<HTMLInputElement>) => void;
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
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const joinDate = new Date(profile.created_at);
  const isBureau = profile.role === "bureau";
  const isVieux = profile.role === "vieux";
  const special = isBureau || isVieux;

  /* Premium banner gradients */
  const bannerGradient = isBureau
    ? "bg-gradient-to-tr from-yellow-300 via-amber-500 to-orange-600 dark:from-yellow-800 dark:via-amber-700 dark:to-orange-950"
    : isVieux
      ? "bg-gradient-to-tr from-fuchsia-400 via-purple-600 to-indigo-700 dark:from-fuchsia-900 dark:via-purple-800 dark:to-indigo-950"
      : "bg-gradient-to-br from-slate-200/80 via-blue-100/60 to-violet-100/60 dark:from-zinc-800/80 dark:via-blue-950/30 dark:to-violet-950/20";

  /* Avatar: special roles get larger + no shadow class (handled by animation keyframe) */
  const avatarSize = special ? "h-32 w-32" : "h-24 w-24";
  const avatarOffset = special ? "-mt-16" : "-mt-12";
  const avatarBorder = special
    ? "border-4 border-background"
    : "border-4 border-background shadow-lg";

  const RoleIcon = isBureau ? Crown : isVieux ? Sparkles : null;
  const roleIconClass = isBureau
    ? "h-7 w-7 text-amber-500 dark:text-amber-400 drop-shadow-sm"
    : "h-7 w-7 text-purple-500 dark:text-purple-400 drop-shadow-sm";

  const tabTrigger = [
    "rounded-lg px-3.5 py-2 text-sm font-medium transition-all shrink-0",
    isBureau
      ? "data-[state=active]:bg-amber-50 data-[state=active]:text-amber-800 dark:data-[state=active]:bg-amber-950/40 dark:data-[state=active]:text-amber-200"
      : isVieux
        ? "data-[state=active]:bg-purple-50 data-[state=active]:text-purple-800 dark:data-[state=active]:bg-purple-950/40 dark:data-[state=active]:text-purple-200"
        : "",
  ].join(" ");

  return (
    <div className="w-full h-full bg-slate-50/50 dark:bg-zinc-950/40">
      {/* ── Ambient background glow ── */}
      {special && (
        <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
          <div
            className={`absolute -top-40 left-1/2 h-[700px] w-[900px] -translate-x-1/2 rounded-[100%] blur-[140px] ${
              isBureau
                ? "bg-amber-400/20 dark:bg-amber-500/10"
                : "bg-purple-400/20 dark:bg-purple-500/10"
            }`}
            style={{ animation: "pf-glow-breathe 6s ease-in-out infinite" }}
          />
        </div>
      )}

      <div className="relative z-10 mx-auto w-full max-w-3xl space-y-6 p-4 pb-20 md:p-6 md:pb-6">
        {/* ── Hero card ── */}
        <div
          className={`relative overflow-hidden rounded-2xl border bg-background transition-shadow ${
            special ? "shadow-xl" : "shadow-sm dark:shadow-none"
          }`}
          style={{ animation: "pf-fade-up 0.5s ease-out" }}
        >
          {/* Premium Gradient Banner */}
          <div
            className={`relative overflow-hidden ${special ? "h-48" : "h-32"} ${bannerGradient}`}
            style={getBannerAnimStyle(profile.role)}
          >
            {/* Premium overlays */}
            {special && (
              <>
                <div className="absolute inset-0 bg-gradient-to-b from-white/30 to-transparent dark:from-white/10 mix-blend-overlay pointer-events-none" />
                <div className="absolute inset-0 bg-gradient-to-t from-background via-background/40 to-transparent pointer-events-none z-0" />
                <div
                  className="absolute inset-0 opacity-[0.04] dark:opacity-[0.06] pointer-events-none mix-blend-overlay"
                  style={{
                    backgroundImage:
                      "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E\")",
                  }}
                />
              </>
            )}
            {isBureau && <FloatingParticles variant="bureau" />}
            {isVieux && <FloatingParticles variant="vieux" />}
          </div>

          {/* Profile content */}
          <div className="relative px-6 pb-6">
            {/* Avatar with pulsing ring (inline animation) */}
            <div
              className={`group relative ${avatarOffset} mb-4 w-fit ml-1 z-10`}
            >
              <Avatar
                className={`${avatarSize} ${avatarBorder}`}
                style={getRingAnimStyle(profile.role)}
              >
                <AvatarImage
                  src={
                    profile.avatar_url
                      ? `${API_BASE}/users/${profile.id}/avatar?v=${encodeURIComponent(profile.avatar_url)}`
                      : undefined
                  }
                />
                {isUploadingAvatar && (
                  <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/40 backdrop-blur-[1px]">
                    <Loader2 className="h-6 w-6 animate-spin text-white/70" />
                  </div>
                )}
                <AvatarFallback
                  className={`font-semibold text-white ${special ? "text-2xl" : "text-xl"} ${
                    isBureau
                      ? "bg-gradient-to-br from-amber-500 to-orange-600"
                      : isVieux
                        ? "bg-gradient-to-br from-purple-500 to-fuchsia-600"
                        : "bg-gradient-to-br from-blue-500 to-violet-500"
                  }`}
                >
                  {initials}
                </AvatarFallback>
              </Avatar>
              {isOwn && onAvatarUpload && (
                <label className="absolute inset-0 flex cursor-pointer items-center justify-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                  <Camera className="h-5 w-5 text-white" />
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={onAvatarUpload}
                  />
                </label>
              )}
            </div>

            {/* Name + role badge + reputation */}
            <div className="flex flex-wrap items-center gap-2.5 mb-1">
              <h1
                className={`flex items-center gap-2 tracking-tight ${
                  special
                    ? "text-3xl font-black"
                    : "text-2xl font-bold text-foreground"
                }`}
                style={getShimmerStyle(profile.role)}
              >
                {profile.display_name ?? profile.email}
                {RoleIcon && (
                  <RoleIcon
                    className={roleIconClass}
                    style={{ animation: "pf-fade-up 0.6s 0.2s both ease-out" }}
                  />
                )}
              </h1>
              <Badge
                variant="outline"
                className={`capitalize text-xs ${getRoleBadgeClasses(profile.role)}`}
              >
                {tRoles(profile.role as any)}
              </Badge>
              <div
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-sm font-semibold ${
                  isBureau
                    ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                    : isVieux
                      ? "bg-purple-500/10 text-purple-700 dark:text-purple-300"
                      : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                }`}
              >
                <Star className="h-3.5 w-3.5 fill-current" />
                {profile.reputation}
              </div>
            </div>

            {/* Bio */}
            {profile.bio ? (
              <p className="mt-2 text-sm text-foreground/80 leading-relaxed max-w-lg">
                {profile.bio}
              </p>
            ) : (
              isOwn &&
              !editing && (
                <button
                  onClick={() => setEditing(true)}
                  className="mt-2 text-sm text-muted-foreground/80 italic hover:text-foreground transition-colors border-b border-dashed border-transparent hover:border-muted-foreground/50 pb-0.5"
                >
                  {t("addBio")}
                </button>
              )
            )}

            {/* Meta info */}
            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted-foreground font-medium">
              {isOwn && (
                <span className="flex items-center gap-1">{profile.email}</span>
              )}
              {profile.academic_year && (
                <span className="flex items-center gap-1 text-foreground/80">
                  <GraduationCap className="h-4 w-4" />
                  {t("year", { year: profile.academic_year })}
                </span>
              )}
              <span className="flex items-center gap-1 text-foreground/80">
                <Calendar className="h-4 w-4" />
                {t("joined", { 
                  date: joinDate.toLocaleDateString(locale, {
                    month: "short",
                    year: "numeric",
                  }) 
                })}
              </span>
            </div>

            {/* Edit button */}
            {isOwn && !editing && (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setEditing(true)}
              >
                <Pencil className="mr-1.5 h-3.5 w-3.5" />
                {t("editProfile")}
              </Button>
            )}
          </div>
        </div>

        {/* ── Edit form ── */}
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

        {/* ── Stats ── */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {STATS.map((s, i) => {
            const statKeys: Record<string, string> = {
              prs_approved: "approved",
              prs_total: "totalPrs",
              annotations_count: "annotations",
              comments_count: "comments",
            };
            return (
            <StatCard
              key={s.key}
              icon={s.icon}
              label={t(statKeys[s.key] as any)}
              value={profile[s.key]}
              color={s.color}
              bg={s.bg}
              bar={s.bar}
              delay={0.3 + i * 0.07}
            />
          )})}
        </div>

        {/* ── Activity tabs ── */}
        <div style={{ animation: "pf-fade-up 0.5s 0.6s both ease-out" }}>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="flex w-full flex-wrap gap-1 rounded-xl p-1.5 !h-auto min-h-fit bg-muted/60 dark:bg-muted/30 sm:flex-nowrap sm:justify-start sm:gap-0 sm:overflow-x-auto sm:h-9">
              <TabsTrigger value="prs" className={cn(tabTrigger, "w-[calc(50%-2px)] sm:w-auto min-h-9")}>
                {t("contributions")}
              </TabsTrigger>
              <TabsTrigger value="materials" className={cn(tabTrigger, "w-[calc(50%-2px)] sm:w-auto min-h-9")}>
                {t("materials")}
              </TabsTrigger>
              <TabsTrigger value="annotations" className={cn(tabTrigger, "w-[calc(50%-2px)] sm:w-auto min-h-9")}>
                {t("annotations")}
              </TabsTrigger>
              {showRecentlyViewed && (
                <TabsTrigger value="recent" className={cn(tabTrigger, "w-[calc(50%-2px)] sm:w-auto min-h-9")}>
                  {t("recentlyViewed")}
                </TabsTrigger>
              )}
            </TabsList>
            <TabsContent
              value="prs"
              className="mt-4 min-h-[400px] sm:min-h-[600px]"
            >
              {activeTab === "prs" && (
                <ContributionList userId={profile.id} type="prs" />
              )}
            </TabsContent>
            <TabsContent
              value="materials"
              className="mt-4 min-h-[400px] sm:min-h-[600px]"
            >
              {activeTab === "materials" && (
                <ContributionList userId={profile.id} type="materials" />
              )}
            </TabsContent>
            <TabsContent
              value="annotations"
              className="mt-4 min-h-[400px] sm:min-h-[600px]"
            >
              {activeTab === "annotations" && (
                <ContributionList userId={profile.id} type="annotations" />
              )}
            </TabsContent>
            {showRecentlyViewed && (
              <TabsContent
                value="recent"
                className="mt-4 min-h-[400px] sm:min-h-[600px]"
              >
                {activeTab === "recent" && <RecentlyViewed />}
              </TabsContent>
            )}
          </Tabs>
        </div>
      </div>
    </div>
  );
}
