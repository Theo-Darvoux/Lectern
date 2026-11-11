"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Inbox } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { PRCard } from "@/components/pr/pr-card";
import { QueryError } from "@/components/query-error";
import { useTranslations } from "next-intl";
import { type PullRequestOut } from "@/components/home/types";

interface SidebarTarget {
    type: "directory" | "material";
    id: string;
    data: Record<string, unknown>;
}

interface EditsTabProps {
    target: SidebarTarget | null;
}

export function EditsTab({ target }: EditsTabProps) {
    const t = useTranslations("Sidebar");
    const query = useQuery({
        queryKey: ["pull-requests", "for-item", target?.type, target?.id],
        queryFn: ({ signal }) =>
            apiFetch<PullRequestOut[]>(
                `/pull-requests/for-item?targetType=${target!.type}&targetId=${target!.id}`,
                { signal },
            ),
        enabled: !!target,
    });
    const prs = query.data ?? [];

    if (!target) {
        return <p className="text-sm text-muted-foreground p-4">{t("selectItemToViewEdits")}</p>;
    }

    if (query.isPending) {
        return (
            <div className="flex justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (query.isError) {
        return <QueryError onRetry={() => void query.refetch()} className="py-12" />;
    }

    if (prs.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center py-12 text-center">
                <Inbox className="mb-3 h-8 w-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                    {t("noActiveContributions", { type: t(target.type) })}
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-4 px-1 pb-4">
            <h3 className="font-semibold px-2">{t("openEdits")}</h3>
            <div className="flex flex-col gap-3">
                {prs.map((pr) => (
                    <PRCard key={pr.id} pr={pr} />
                ))}
            </div>
        </div>
    );
}
