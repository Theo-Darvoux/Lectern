"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { Navbar } from "@/components/navbar";
import { MobileBottomBar } from "@/components/mobile-bottom-bar";
import { Footer } from "@/components/footer";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { CookieBanner } from "@/components/cookie-banner";
import { StagingFab } from "@/components/pr/staging-fab";

const ReviewDrawer = dynamic(
  () => import("@/components/pr/review-drawer").then((m) => m.ReviewDrawer),
  { ssr: false }
);
const GlobalDropZone = dynamic(
  () => import("@/components/pr/global-drop-zone").then((m) => m.GlobalDropZone),
  { ssr: false }
);
import { useAuth } from "@/hooks/use-auth";
import { useOffline } from "@/hooks/use-offline";
import { getAccessToken, hasAuthHint } from "@/lib/auth-tokens";
import { initAuthSync } from "@/lib/auth-sync";
import { useAuthStore } from "@/lib/stores";
import { WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";

import { useUIStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

export function LayoutShell({ children }: { children: ReactNode }) {
  const t = useTranslations("Layout");
  const { user, isAuthenticated, isLoading, fetchMe } = useAuth();
  const { setLoading } = useAuthStore();
  const { hideFooter } = useUIStore();
  const pathname = usePathname();
  const router = useRouter();

  const path = pathname.replace(/\/$/, "") || "/";
  const isPublicPage = path === "/login" || path === "/login/verify" || path === "/privacy" || path === "/terms";
  const isOnboardingPage = path === "/onboarding";
  const isPendingPage = path === "/pending-approval";

  useEffect(() => {
    const cleanup = initAuthSync();
    return cleanup;
  }, []);

  useEffect(() => {
    const token = getAccessToken();
    const hint = hasAuthHint();
    if ((token || hint) && !isAuthenticated && isLoading) {
      fetchMe();
    } else if (!token && !hint && isLoading) {
      // No token and no hint — user is definitely not logged in; skip the
      // network round-trip and clear the loading state directly.
      setLoading(false);
    }
  }, []);  

  useEffect(() => {
    if (isLoading) return;

    const isPublic = path === "/login" || path === "/login/verify" || path === "/privacy" || path === "/terms";
    const isOnboarding = path === "/onboarding";
    const isPending = path === "/pending-approval";

    if (!isAuthenticated) {
      if (!isPublic) router.push("/login");
      return;
    }

    // Authenticated user checks
    if (!user) return; // Wait for user data

    if (user.role === "pending" && !isPending) {
      router.push("/pending-approval");
      return;
    }

    if (user.role !== "pending" && !user.onboarded && !isOnboarding && !isPublic) {
      router.push("/onboarding");
      return;
    }

    if (user.onboarded && isOnboarding) {
      router.push("/");
    }
  }, [isLoading, isAuthenticated, user, pathname, router]);

  const shouldHideContent = !isPublicPage && (
    isLoading ||
    !isAuthenticated ||
    (user && user.role !== "pending" && !user.onboarded && !isOnboardingPage) ||
    (user && user.onboarded && isOnboardingPage) ||
    (user && user.role === "pending" && !isPendingPage)
  );
  const isOffline = useOffline();

  return (
    <div className="flex flex-col h-dvh overflow-hidden">
      {/* Offline banner (U4) */}
      <div
        className={cn(
          "bg-destructive text-destructive-foreground px-4 text-center text-xs font-medium transition-all overflow-hidden sticky top-0 z-[100] flex items-center justify-center gap-2",
          isOffline
            ? "h-auto py-1.5 opacity-100"
            : "h-0 py-0 opacity-0 pointer-events-none",
        )}
      >
        <WifiOff className="h-3.5 w-3.5" />
        {t("offlineWarning")}
      </div>
      {!shouldHideContent && <Navbar />}
      <main className="flex-1 w-full grid grid-cols-1 min-h-0 overflow-y-auto overflow-x-hidden">
        {shouldHideContent ? (
          <div className="flex flex-col items-center justify-center min-h-[50vh] animate-in fade-in duration-500">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent mb-4" />
            <p className="text-sm text-muted-foreground font-medium animate-pulse">
              {(user && !user.onboarded) ? t("redirectingToSetup") : t("recoveringSession")}
            </p>
          </div>
        ) : (
          children
        )}
      </main>
      {!hideFooter && !shouldHideContent && (
        <div className="hidden md:block">
          <Footer />
        </div>
      )}
      {!shouldHideContent && <MobileBottomBar />}
      <ConfirmDialog />

      <StagingFab />
      <ReviewDrawer />
      <GlobalDropZone />
      <CookieBanner />
    </div>
  );
}
