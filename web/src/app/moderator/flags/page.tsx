"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, X, Shield, ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

interface FlagData {
    id: string;
    reporter_id: string | null;
    reporter: {
        id: string;
        display_name: string | null;
        email: string;
    } | null;
    target_type: string;
    target_id: string;
    target: {
        exists: boolean;
        preview: string | null;
        link: string | null;
    } | null;
    reason: string;
    description: string | null;
    status: string;
    resolved_by: string | null;
    resolved_at: string | null;
    created_at: string;
}

interface PaginatedFlags {
    items: FlagData[];
    total: number;
    page: number;
    pages: number;
}

const STATUS_COLORS: Record<string, string> = {
    open: "bg-yellow-500/10 text-yellow-600",
    reviewing: "bg-blue-500/10 text-blue-600",
    resolved: "bg-green-500/10 text-green-600",
    dismissed: "bg-gray-500/10 text-gray-500",
};

// TARGET_LABELS removed as it was replaced by i18n t() calls

export default function ModeratorFlagsPage() {
    const t = useTranslations("Moderator.flags");
    const tFlags = useTranslations("Flags");
    const tCommon = useTranslations("Common");
    const [flags, setFlags] = useState<FlagData[]>([]);
    const [page, setPage] = useState(1);
    const [pages, setPages] = useState(1);
    const [statusFilter, setStatusFilter] = useState("open");
    const [targetTypeFilter, setTargetTypeFilter] = useState("all");
    const [loading, setLoading] = useState(false);

    const fetchFlags = useCallback(async (p: number) => {
        setLoading(true);
        try {
            const params = new URLSearchParams({ page: String(p), limit: "20" });
            if (statusFilter && statusFilter !== "all") params.set("status", statusFilter);
            if (targetTypeFilter && targetTypeFilter !== "all") params.set("targetType", targetTypeFilter);

            const data = await apiFetch<PaginatedFlags>(`/flags?${params}`);
            setFlags(data.items);
            setPage(data.page);
            setPages(data.pages);
        } catch {
            toast.error(t("loadError"));
        } finally {
            setLoading(false);
        }
    }, [statusFilter, targetTypeFilter, t]);

    useEffect(() => {
        fetchFlags(1);
    }, [fetchFlags]);

    const handleAction = async (flagId: string, status: "resolved" | "dismissed") => {
        try {
            await apiFetch(`/flags/${flagId}`, {
                method: "PATCH",
                body: JSON.stringify({ status }),
            });
            toast.success(status === "resolved" ? t("resolved") : t("dismissed"));
            fetchFlags(page);
        } catch {
            toast.error(t("updateError"));
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex gap-3">
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder={t("placeholderStatus")} />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("status.all")}</SelectItem>
                        <SelectItem value="open">{t("status.open")}</SelectItem>
                        <SelectItem value="reviewing">{t("status.reviewing")}</SelectItem>
                        <SelectItem value="resolved">{t("status.resolved")}</SelectItem>
                        <SelectItem value="dismissed">{t("status.dismissed")}</SelectItem>
                    </SelectContent>
                </Select>

                <Select value={targetTypeFilter} onValueChange={setTargetTypeFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder={t("placeholderType")} />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("types.all")}</SelectItem>
                        <SelectItem value="material">{t("types.material")}</SelectItem>
                        <SelectItem value="annotation">{t("types.annotation")}</SelectItem>
                        <SelectItem value="pull_request">{t("types.pull_request")}</SelectItem>
                        <SelectItem value="comment">{t("types.comment")}</SelectItem>
                        <SelectItem value="pr_comment">{t("types.pr_comment")}</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {loading && flags.length === 0 && (
                <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
            )}

            {!loading && flags.length === 0 && (
                <Card>
                    <CardContent className="py-12 text-center">
                        <Shield className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
                        <p className="text-sm text-muted-foreground">{t("noFlags")}</p>
                    </CardContent>
                </Card>
            )}

            <div className="space-y-3">
                {flags.map((flag) => (
                    <Card key={flag.id}>
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <CardTitle className="text-sm font-medium">
                                        {t(`types.${flag.target_type}` as Parameters<typeof t>[0])}
                                    </CardTitle>
                                    <Badge
                                        variant="outline"
                                        className={STATUS_COLORS[flag.status] ?? ""}
                                    >
                                        {t(`status.${flag.status}` as Parameters<typeof t>[0])}
                                    </Badge>
                                    <Badge variant="outline">
                                        {tFlags.has(`reasons.${flag.reason}`) ? tFlags(`reasons.${flag.reason}` as any) : flag.reason}
                                    </Badge>
                                </div>
                                <span className="text-xs text-muted-foreground">
                                    {new Date(flag.created_at).toLocaleDateString()}
                                </span>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-2">
                                <p className="text-xs text-muted-foreground">
                                    {t("reportedBy", { name: flag.reporter?.display_name ?? flag.reporter?.email ?? "Unknown" })}
                                </p>
                                {flag.target?.exists ? (
                                    <div className="rounded-md border bg-muted/30 p-2">
                                        <p className="text-xs font-medium text-muted-foreground">
                                            {t("reportedContent")}
                                        </p>
                                        {flag.target.preview && (
                                            <p className="mt-1 line-clamp-3 text-sm">
                                                {flag.target.preview}
                                            </p>
                                        )}
                                        {flag.target.link && (
                                            <a
                                                href={flag.target.link}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                                            >
                                                <ExternalLink className="h-3.5 w-3.5" />
                                                {t("viewContent")}
                                            </a>
                                        )}
                                    </div>
                                ) : (
                                    <p className="text-xs text-muted-foreground italic">
                                        {t("contentDeleted")}
                                    </p>
                                )}
                                {flag.description && (
                                    <p className="rounded-md bg-muted/50 p-2 text-sm">
                                        {flag.description}
                                    </p>
                                )}
                                {flag.status === "open" && (
                                    <div className="flex gap-2 pt-2">
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => handleAction(flag.id, "resolved")}
                                        >
                                            <Check className="mr-1 h-3.5 w-3.5" />
                                            {t("resolve")}
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => handleAction(flag.id, "dismissed")}
                                        >
                                            <X className="mr-1 h-3.5 w-3.5" />
                                            {t("dismiss")}
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {pages > 1 && (
                <div className="flex items-center justify-center gap-3 pt-2">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={page <= 1}
                        onClick={() => fetchFlags(page - 1)}
                    >
                        <ChevronLeft className="h-4 w-4" />
                        {tCommon("previous")}
                    </Button>
                    <span className="text-sm text-muted-foreground">
                        {tCommon("page", { page, total: pages })}
                    </span>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={page >= pages}
                        onClick={() => fetchFlags(page + 1)}
                    >
                        {tCommon("next")}
                        <ChevronRight className="h-4 w-4" />
                    </Button>
                </div>
            )}
        </div>
    );
}
