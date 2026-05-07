"use client";

import { use, useCallback, useEffect, useState } from "react";
import { ProfileView, ProfileSkeleton, type UserProfile } from "@/components/profile/profile-view";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

export default function PublicProfilePage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const t = useTranslations("Profile");
    const { id } = use(params);
    const [profile, setProfile] = useState<UserProfile | null>(null);
    const [notFound, setNotFound] = useState(false);

    const fetchProfile = useCallback(async () => {
        try {
            const data = await apiFetch<UserProfile>(`/users/${id}`);
            setProfile(data);
        } catch {
            queueMicrotask(() => {
                setNotFound(true);
                toast.error(t("notFound"));
            });
        }
    }, [id, t]);

    useEffect(() => {
        setTimeout(fetchProfile, 0);
    }, [fetchProfile]);

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

    return <ProfileView profile={profile} isOwn={false} />;
}
