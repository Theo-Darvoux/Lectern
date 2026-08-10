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
    const { setUser } = useAuthStore();
    const [profile, setProfile] = useState<UserProfile | null>(null);
    const [isUploading, setIsUploading] = useState(false);

    const fetchProfile = useCallback(async () => {
        try {
            const data = await apiFetch<UserProfile>(`/users/me?t=${Date.now()}`);
            setProfile(data);
            setUser(data);
        } catch {
            queueMicrotask(() => {
                toast.error(t("loadProfileError"));
            });
        }
    }, [setUser, t]);

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
            setUser(updatedUser);
            
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
        setUser(updated);
    }, [setUser]);

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
