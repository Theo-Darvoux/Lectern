"use client";

import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { ProfileView, ProfileSkeleton, type UserProfile } from "@/components/profile/profile-view";
import { QueryError } from "@/components/query-error";
import { ApiError, apiFetch } from "@/lib/api-client";

export function ProfilePageContent() {
    const t = useTranslations("Profile");
    const pathname = usePathname();
    const id = pathname.replace(/^\/profile\//, "").replace(/\/$/, "");
    const profile = useQuery({
        queryKey: ["users", id],
        queryFn: ({ signal }) => apiFetch<UserProfile>(`/users/${id}`, { signal }),
    });

    // A malformed id (422) names no user either.
    if (profile.isError && profile.error instanceof ApiError && [404, 422].includes(profile.error.status)) {
        return (
            <div className="flex flex-col items-center justify-center p-20 text-center">
                <p className="text-lg font-medium text-muted-foreground">{t("notFound")}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                    {t("notFoundDescription")}
                </p>
            </div>
        );
    }
    if (profile.isError) {
        return <QueryError onRetry={() => void profile.refetch()} className="p-20" />;
    }
    if (!profile.data) return <ProfileSkeleton />;

    return <ProfileView key={profile.data.id} profile={profile.data} isOwn={false} />;
}
