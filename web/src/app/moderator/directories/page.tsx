"use client";

import { useEffect, useState } from "react";
import { Folder, FileBox, Loader2, ShieldAlert } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";

interface DirectoryItem {
    id: string;
    parent_id: string | null;
    name: string;
    slug: string;
    type: "module" | "folder";
    is_system: boolean;
}

export default function ModeratorDirectoriesPage() {
    const t = useTranslations("Moderator.directories");
    const [directories, setDirectories] = useState<DirectoryItem[]>([]);
    const [loading, setLoading] = useState(true);

    const fetchDirectories = () => {
        apiFetch<DirectoryItem[]>("/moderator/directories")
            .then(setDirectories)
            .catch(() => toast.error(t("loadError")))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchDirectories();
         
    }, []);

    const buildTree = (items: DirectoryItem[], parentId: string | null = null): React.ReactNode[] => {
        const children = items.filter((i) => i.parent_id === parentId);
        if (children.length === 0) return [];

        return children.map((child) => (
            <div key={child.id} className="ml-6 mt-2 border-l pl-4 border-muted">
                <div className="flex items-center gap-2 group">
                    {child.type === "module" ? (
                        <FileBox className="h-4 w-4 text-primary" />
                    ) : (
                        <Folder className="h-4 w-4 text-blue-500" />
                    )}
                    <span className="text-sm font-medium">{child.name}</span>
                    <span className="text-xs text-muted-foreground hidden sm:inline">
                        ({child.slug})
                    </span>
                    {child.is_system && (
                        <span className="text-[10px] font-medium bg-muted px-1.5 py-0.5 rounded text-muted-foreground uppercase">
                            {t("system")}
                        </span>
                    )}
                </div>
                {buildTree(items, child.id)}
            </div>
        ));
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                <span className="ml-2 text-muted-foreground">{t("loading")}</span>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">{t("title")}</h1>
                    <p className="text-muted-foreground">
                        {t("readOnly")}
                    </p>
                </div>
            </div>

            <Card className="p-4 border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-900/50">
                <div className="flex items-start gap-3">
                    <ShieldAlert className="h-5 w-5 text-amber-600 mt-0.5" />
                    <div className="text-sm text-amber-800 dark:text-amber-300">
                        <span className="font-bold uppercase tracking-wider text-[10px] bg-amber-200 dark:bg-amber-800 px-1.5 py-0.5 rounded mr-2">
                            {t("system")}
                        </span>
                        {t("readOnly")}
                    </div>
                </div>
            </Card>

            <div className="rounded-lg border bg-card p-4 overflow-x-auto min-h-[300px]">
                {directories.length === 0 ? (
                    <div className="text-center text-muted-foreground py-12">
                        {t("empty")}
                    </div>
                ) : (
                    <div className="-ml-6">
                        {buildTree(directories, null)}
                    </div>
                )}
            </div>
        </div>
    );
}
