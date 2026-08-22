"use client";

import { useEffect } from "react";
import { Clock, LogOut, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { useRouter } from "next/navigation";

import { useTranslations } from "next-intl";

export default function PendingApprovalPage() {
    const t = useTranslations("PendingApproval");
    const { user, isAuthenticated, isLoading, logout } = useAuth();
    const router = useRouter();

    // Redirect away if user is approved or not logged in
    useEffect(() => {
        if (!isLoading) {
            if (!isAuthenticated) {
                router.replace("/login");
            } else if (user && user.role !== "pending") {
                router.replace("/");
            }
        }
    }, [isLoading, isAuthenticated, user, router]);

    const handleLogout = async () => {
        await logout();
        router.replace("/login");
    };

    return (
        <div className="flex min-h-screen flex-col items-center justify-center p-4">
            <div className="w-full max-w-md space-y-8 text-center">
                {/* Icon */}
                <div className="flex justify-center">
                    <div className="flex h-24 w-24 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/30">
                        <Clock className="h-12 w-12 text-amber-600 dark:text-amber-400" />
                    </div>
                </div>

                {/* Heading */}
                <div className="space-y-2">
                    <h1 className="text-3xl font-bold tracking-tight">{t("title")}</h1>
                    <p className="text-muted-foreground">
                        {t("description")}
                    </p>
                </div>

                {/* Info card */}
                <div className="rounded-xl border bg-card p-6 text-left shadow-sm space-y-4">
                    <div className="flex items-start gap-3">
                        <Mail className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                        <div>
                            <p className="font-medium text-sm">{t("registeredAs")}</p>
                            <p className="text-sm text-muted-foreground">{user?.email ?? "—"}</p>
                        </div>
                    </div>
                    <div className="border-t pt-4">
                        <p className="text-sm text-muted-foreground leading-relaxed">
                            {t("info")}
                        </p>
                    </div>
                </div>

                {/* Actions */}
                <div className="flex flex-col gap-3">
                    <Button
                        variant="outline"
                        className="w-full"
                        onClick={handleLogout}
                    >
                        <LogOut className="mr-2 h-4 w-4" />
                        {t("signOut")}
                    </Button>
                </div>
            </div>
        </div>
    );
}
