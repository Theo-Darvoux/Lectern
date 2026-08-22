"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { Navbar } from "@/components/navbar";
import { MobileBottomBar } from "@/components/mobile-bottom-bar";
import { Footer } from "@/components/footer";
import { ConfirmDialog } from "@/components/confirm-dialog";
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
import { WifiOff } from "lucide-react";
import { cn, normalizePathname, sanitizeNext } from "@/lib/utils";

import { useUIStore } from "@/lib/stores";
import { isGuest, isGuestBlockedPath } from "@/lib/guest";
import { useTranslations } from "next-intl";

export function LayoutShell({ children }: { children: ReactNode }) {
  const t = useTranslations("Layout");
  const { user, isAuthenticated, isLoading, bootstrapError, bootstrapAuth } = useAuth();
  const guest = isGuest(user);
  const hideFooter = useUIStore((state) => state.hideFooter);
  const navbarVisible = useUIStore((state) => state.navbarVisible);
  const rawPathname = usePathname();
  const router = useRouter();

  // `trailingSlash: true` makes route guards vulnerable to direct-load vs
  // client-navigation mismatches unless every pathname is normalized first.
  const pathname = normalizePathname(rawPathname);

  // `/setup` is a bootstrap route: on a fresh installation there cannot be an
  // authenticated user yet, so session restoration must never gate its UI.
  const isPublicPage = pathname === "/setup" || pathname === "/login" || pathname === "/register" || pathname === "/login/verify" || pathname === "/login/reset-password" || pathname === "/privacy" || pathname === "/terms";
  const isOnboardingPage = pathname === "/onboarding";
  const isPendingPage = pathname === "/pending-approval";

  useEffect(() => {
    if (isLoading || bootstrapError) return;

    const isPublic = isPublicPage;
    const isOnboarding = isOnboardingPage;
    const isPending = isPendingPage;

    if (!isAuthenticated) {
      if (!isPublic) {
        const search = typeof window !== "undefined" ? window.location.search : "";
        const next = sanitizeNext(pathname + search);
        router.push(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
      }
      return;
    }

    // Authenticated user checks
    if (!user) return; // Wait for user data

    // Read-only guests have no profile, settings, PRs, notifications, or
    // onboarding — send them back to browsing if they land on those routes.
    // Other users' profiles (/profile/<id>) remain viewable.
    if (user.role === "guest" && isGuestBlockedPath(pathname)) {
      router.push("/browse");
      return;
    }

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
  }, [isLoading, bootstrapError, isAuthenticated, user, pathname, router]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      // Ignore touch events (e.g. emulated mouse events from touch devices)
      if ("pointerType" in e && (e as PointerEvent).pointerType === "touch") return;

      // When the mouse is brought to the top of the page (within 20px of the top),
      // open the navbar if it was previously closed.
      if (e.clientY <= 20) {
        if (!useUIStore.getState().navbarVisible) {
          useUIStore.getState().setNavbarVisible(true);
        }
      }
    };

    window.addEventListener("mousemove", handleMouseMove, { passive: true });
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
    };
  }, []);

  const shouldHideContent = !isPublicPage && (
    isLoading ||
    !isAuthenticated ||
    (user && user.role !== "pending" && !user.onboarded && !isOnboardingPage) ||
    (user && user.onboarded && isOnboardingPage) ||
    (user && user.role === "pending" && !isPendingPage)
  );
  const shouldShowStartupError = !isPublicPage && !!bootstrapError;
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
      <div
        className="overflow-hidden transition-all duration-300 ease-in-out"
        style={{ maxHeight: navbarVisible ? 56 : 0 }}
      >
        {!shouldHideContent && <Navbar />}
      </div>
      <main className="flex-1 w-full grid grid-cols-1 min-h-0 overflow-y-auto overflow-x-hidden">
        {shouldShowStartupError ? (
          <div className="flex min-h-[50vh] items-center justify-center p-4" role="alert">
            <div className="w-full max-w-md rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <h1 className="text-lg font-semibold">{t("startupErrorTitle")}</h1>
              <p className="mt-2 text-sm text-muted-foreground">{t("startupErrorDescription")}</p>
              {process.env.NODE_ENV === "development" && (
                <pre className="mt-3 max-h-32 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
                  {bootstrapError}
                </pre>
              )}
              <button
                type="button"
                className="mt-5 inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => void bootstrapAuth()}
              >
                {t("retry")}
              </button>
            </div>
          </div>
        ) : shouldHideContent ? (
          <div
            className="flex flex-col items-center justify-center min-h-[50vh] animate-in fade-in duration-500"
            role="status"
            aria-live="polite"
          >
            <div
              className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent mb-4"
              aria-hidden="true"
            />
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

      {/* Upload / contribution surfaces are unavailable to read-only guests. */}
      {isAuthenticated && !guest && !shouldHideContent && (
        <>
          <StagingFab />
          <ReviewDrawer />
          <GlobalDropZone />
        </>
      )}
    </div>
  );
}
