"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import { Search, ExternalLink } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { QueryError } from "@/components/query-error";
import { apiFetch } from "@/lib/api-client";
import { useTranslations } from "next-intl";
import { AnonymousBadge } from "@/components/pr/anonymous-badge";
import { useDateLocale } from "@/hooks/use-date-locale";

interface PRItem {
  id: string;
  title: string;
  type: "new" | "update" | "delete" | "batch" | "revert";
  status: "open" | "approved" | "rejected";
  created_at: string;
  author: {
    id: string;
    display_name: string | null;
  } | null;
  is_anonymous?: boolean;
}

const COLUMN_COUNT = 4;

export default function StaffPRQueuePage() {
  const t = useTranslations("Moderator.pullRequests");
  const tCommon = useTranslations("Common");
  const tPRs = useTranslations("PRs");
  const dateLocale = useDateLocale();
  const [search, setSearch] = useState("");
  const { data: prs, isPending, isError, refetch } = useQuery({
    queryKey: ["pull-requests", "staff-queue"],
    queryFn: ({ signal }) => apiFetch<PRItem[]>("/pull-requests?status=open&limit=50", { signal }),
  });

  const needle = search.toLowerCase();
  const filtered = (prs ?? []).filter((pr) => pr.title.toLowerCase().includes(needle));

  const messageRow = (content: React.ReactNode) => (
    <tr>
      <td colSpan={COLUMN_COUNT} className="p-8 text-center text-muted-foreground">
        {content}
      </td>
    </tr>
  );

  return (
    <div className="space-y-4">
      <div className="relative">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder={t("searchPlaceholder")}
          className="max-w-md pl-9"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="rounded-lg border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b bg-muted/50 text-muted-foreground">
            <tr>
              <th className="p-4 font-medium min-w-[300px]">{t("columnTitle")}</th>
              <th className="p-4 font-medium hidden sm:table-cell">{t("columnType")}</th>
              <th className="p-4 font-medium hidden sm:table-cell">{t("columnSubmitted")}</th>
              <th className="p-4 font-medium text-right">{t("columnAction")}</th>
            </tr>
          </thead>
          <tbody className="divide-y relative" aria-busy={isPending}>
            {isPending && messageRow(t("loading"))}
            {isError && messageRow(<QueryError message={t("loadError")} onRetry={() => void refetch()} />)}
            {prs && filtered.length === 0 && messageRow(t("empty"))}
            {filtered.map((pr) => (
              <tr key={pr.id} className="transition-colors hover:bg-muted/30 group">
                <td className="p-4 font-medium">
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-1.5">
                      <span className="line-clamp-1">{pr.title}</span>
                      {pr.is_anonymous && <AnonymousBadge className="shrink-0" />}
                    </div>
                    <div className="text-xs font-normal text-muted-foreground sm:hidden">
                      {t(`types.${pr.type}`)} •{" "}
                      {pr.author?.display_name ??
                        (pr.is_anonymous ? tPRs("anonymous") : tCommon("deletedUser"))}
                    </div>
                  </div>
                </td>
                <td className="p-4 capitalize text-muted-foreground hidden sm:table-cell">
                  <span className="bg-muted px-2 py-0.5 rounded text-xs font-medium">
                    {t(`types.${pr.type}`)}
                  </span>
                </td>
                <td className="p-4 text-muted-foreground hidden sm:table-cell">
                  {formatDistanceToNow(new Date(pr.created_at), {
                    addSuffix: true,
                    locale: dateLocale,
                  })}
                </td>
                <td className="p-4 text-right">
                  <Button
                    asChild
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-2 text-primary hover:text-primary hover:bg-primary/10"
                  >
                    <Link href={`/pull-requests/${pr.id}`}>
                      {t("review")}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Link>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
