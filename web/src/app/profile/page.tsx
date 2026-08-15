"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthGuard } from "@/components/auth-guard";
import { ProfileView, ProfileSkeleton, type UserProfile } from "@/components/profile/profile-view";
import { useTranslations } from "next-intl";
import { apiFetch } from "@/lib/api-client";
import { uploadAvatarAndAdopt } from "@/lib/avatar-upload";
import { useAuthStore } from "@/lib/stores";
import { toast } from "sonner";

function OwnProfileContent() {
    const t = useTranslations("Profile");
    const setUser = useAuthStore((state) => state.setUser);
    const [profile, setProfile] = useState<UserProfile | null>(null);
    const [isUploading, setIsUploading] = useState(false);

    const syncAuthUserFromProfile = useCallback((nextProfile: UserProfile) => {
        const currentUser = useAuthStore.getState().user;
        if (!currentUser) return;

        // Profile DTOs intentionally omit account workflow state. Preserve the
        // authenticated UserBrief fields while refreshing shared profile data.
        setUser({
            ...currentUser,
            id: nextProfile.id,
            email: nextProfile.email ?? currentUser.email,
            display_name: nextProfile.display_name,
            avatar_url: nextProfile.avatar_url,
            role: nextProfile.role,
        });
    }, [setUser]);

    const fetchProfile = useCallback(async () => {
        try {
            const data = await apiFetch<UserProfile>(`/users/me?t=${Date.now()}`);
            setProfile(data);
            syncAuthUserFromProfile(data);
        } catch {
            queueMicrotask(() => {
                toast.error(t("loadProfileError"));
            });
        }
    }, [syncAuthUserFromProfile, t]);

    useEffect(() => {
        setTimeout(fetchProfile, 0);
    }, [fetchProfile]);

    const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setIsUploading(true);
        const toastId = toast.loading(t("uploadingAvatar"));
        try {
            const updatedUser = await uploadAvatarAndAdopt<UserProfile>(file, {
                onProcessing: () => {
                    toast.loading(t("processingAndCompressing"), { id: toastId });
                },
            });

            toast.success(t("avatarUpdated"), { id: toastId });
            
            // Immediately update state with returned user data while keeping old stats if necessary
            setProfile(prev => prev ? { ...prev, ...updatedUser } : updatedUser);
            syncAuthUserFromProfile(updatedUser);
            
            // Still fetch full profile to ensure stats are perfectly synced if they changed (unlikely for avatar)
            fetchProfile();
        } catch (error) {
            toast.error(error instanceof Error ? error.message : t("uploadAvatarError"), { id: toastId });
        } finally {
            setIsUploading(false);
        }
    };

    const handleProfileUpdated = useCallback((updated: UserProfile) => {
        setProfile(prev => prev ? { ...prev, ...updated } : updated);
        syncAuthUserFromProfile(updated);
    }, [syncAuthUserFromProfile]);

    if (!profile) return <ProfileSkeleton />;

    return (
        <ProfileView
            profile={profile}
            isOwn
            onAvatarUpload={handleAvatarUpload}
            isUploadingAvatar={isUploading}
            onProfileUpdated={handleProfileUpdated}
            showRecentlyViewed
        />
    );
}

export default function ProfilePage() {
    return (
        <AuthGuard requireOnboarded>
            <OwnProfileContent />
        </AuthGuard>
    );
}
