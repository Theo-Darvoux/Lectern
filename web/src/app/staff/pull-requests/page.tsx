"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import { Search, ExternalLink } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

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
}

export default function StaffPRQueuePage() {
  const t = useTranslations("Moderator.pullRequests");
  const [prs, setPrs] = useState<PRItem[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const fetchPRs = async () => {
    setLoading(true);
    try {
      const data = await apiFetch<PRItem[]>("/pull-requests?status=open&limit=50");
      setPrs(data ?? []);
      setHasLoaded(true);
    } catch {
      toast.error(t("loadError"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPRs();
  }, []);

  const filtered = prs.filter((pr) =>
    pr.title.toLowerCase().includes(search.toLowerCase()),
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
          <tbody className="divide-y relative" aria-busy={loading}>
            {loading && !hasLoaded && (
              <tr>
                <td colSpan={5} className="p-8 text-center text-muted-foreground">
                  {t("loading")}
                </td>
              </tr>
            )}
            {hasLoaded && filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="p-8 text-center text-muted-foreground">
                  {t("empty")}
                </td>
              </tr>
            )}
            {filtered.map((pr) => (
              <tr key={pr.id} className="transition-colors hover:bg-muted/30 group">
                <td className="p-4 font-medium">
                  <div className="flex flex-col gap-1">
                    <div className="line-clamp-1">{pr.title}</div>
                    <div className="text-xs font-normal text-muted-foreground sm:hidden">
                      {t(("types." + pr.type) as Parameters<typeof t>[0])} •{" "}
                      {pr.author?.display_name || "[deleted]"}
                    </div>
                  </div>
                </td>
                <td className="p-4 capitalize text-muted-foreground hidden sm:table-cell">
                  <span className="bg-muted px-2 py-0.5 rounded text-xs font-medium">
                    {t(("types." + pr.type) as Parameters<typeof t>[0])}
                  </span>
                </td>
                <td className="p-4 text-muted-foreground hidden sm:table-cell">
                  {formatDistanceToNow(new Date(pr.created_at), { addSuffix: true })}
                </td>
                <td className="p-4 text-right">
                  <Link href={`/pull-requests/${pr.id}`}>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 gap-2 text-primary hover:text-primary hover:bg-primary/10"
                    >
                      {t("review")}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
