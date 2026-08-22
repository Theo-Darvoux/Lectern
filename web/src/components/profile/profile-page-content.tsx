"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ProfileView, ProfileSkeleton, type UserProfile } from "@/components/profile/profile-view";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

export function ProfilePageContent() {
    const t = useTranslations("Profile");
    const pathname = usePathname();
    const id = pathname.replace(/^\/profile\//, "").replace(/\/$/, "");
    const [profile, setProfile] = useState<UserProfile | null>(null);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setProfile(null);
        setNotFound(false);
        const timer = window.setTimeout(() => {
            apiFetch<UserProfile>(`/users/${id}`, { signal: controller.signal })
                .then((data) => setProfile(data))
                .catch(() => {
                    if (controller.signal.aborted) return;
                    setNotFound(true);
                    toast.error(t("notFound"));
                });
        }, 0);
        return () => {
            window.clearTimeout(timer);
            controller.abort();
        };
    }, [id, t]);

    if (notFound) {
        return (
            <div className="flex flex-col items-center justify-center p-20 text-center">
                <p className="text-lg font-medium text-muted-foreground">{t("notFound")}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                    {t("notFoundDescription")}
                </p>
            </div>
        );
    }

    if (!profile) return <ProfileSkeleton />;

    return <ProfileView key={profile.id} profile={profile} isOwn={false} />;
}
