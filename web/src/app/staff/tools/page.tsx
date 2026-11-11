"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Mail, Send, Sparkles, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api-client";
import { WordmarkBuilder } from "@/components/staff/wordmark-builder";
import { isAdminRole } from "@/lib/guest";
import { AdminOnlyNotice } from "@/components/staff/admin-only-notice";
import { QueryError } from "@/components/query-error";

interface AuthConfigResponse {
  site_name?: string;
  site_name_style?: string | null;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_from?: string | null;
  smtp_sender_name?: string | null;
}

export default function StaffToolsPage() {
  const t = useTranslations("Staff.tools");
  const { user } = useAuth();
  const isAdmin = isAdminRole(user?.role);

  const [testEmail, setTestEmail] = useState("");
  const [testingEmail, setTestingEmail] = useState(false);
  const [lastTestResult, setLastTestResult] = useState<{
    success: boolean;
    email: string;
    timestamp: string;
  } | null>(null);

  const configQuery = useQuery({
    queryKey: ["admin", "auth-config"],
    queryFn: ({ signal }) => apiFetch<AuthConfigResponse>("/admin/auth-config", { signal }),
    enabled: isAdmin,
  });
  const config = configQuery.data;

  if (!isAdmin) {
    return <AdminOnlyNotice />;
  }

  const handleTestEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!testEmail.trim()) return;
    setTestingEmail(true);
    try {
      await apiFetch("/admin/auth-config/test-email", {
        method: "POST",
        body: JSON.stringify({ email: testEmail.trim() }),
      });
      toast.success(t("emailTest.success", { email: testEmail.trim() }));
      setLastTestResult({
        success: true,
        email: testEmail.trim(),
        timestamp: new Date().toLocaleTimeString(),
      });
    } catch {
      toast.error(t("emailTest.error"));
      setLastTestResult({
        success: false,
        email: testEmail.trim(),
        timestamp: new Date().toLocaleTimeString(),
      });
    } finally {
      setTestingEmail(false);
    }
  };

  if (configQuery.isError) {
    return <QueryError onRetry={() => void configQuery.refetch()} className="py-12" />;
  }

  if (configQuery.isPending) {
    return (
      <div className="space-y-6">
        <div className="h-20 w-full animate-pulse rounded-2xl bg-muted/50" />
        <div className="h-96 w-full animate-pulse rounded-2xl bg-muted/40" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-12">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">{t("title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>
      </div>

      <Tabs defaultValue="wordmark" className="space-y-6">
        <TabsList className="grid w-full grid-cols-2 max-w-md">
          <TabsTrigger value="wordmark" className="gap-2">
            <Sparkles className="h-4 w-4" />
            {t("tabs.wordmark")}
          </TabsTrigger>
          <TabsTrigger value="email" className="gap-2">
            <Mail className="h-4 w-4" />
            {t("tabs.emailTest")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="wordmark" className="space-y-4">
          <WordmarkBuilder
            siteName={config?.site_name || "INTellect"}
            siteNameStyle={config?.site_name_style || null}
          />
        </TabsContent>

        <TabsContent value="email" className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Mail className="h-5 w-5 text-primary" />
                <CardTitle>{t("emailTest.title")}</CardTitle>
              </div>
              <CardDescription>{t("emailTest.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <form onSubmit={handleTestEmail} className="space-y-4 max-w-xl">
                <div className="space-y-2">
                  <Label htmlFor="test-email-address">
                    {t("recipientEmail")}
                  </Label>
                  <div className="flex gap-2">
                    <Input
                      id="test-email-address"
                      type="email"
                      required
                      placeholder={t("emailTest.placeholder")}
                      value={testEmail}
                      onChange={(e) => setTestEmail(e.target.value)}
                      disabled={testingEmail}
                      className="flex-1"
                    />
                    <Button type="submit" disabled={testingEmail || !testEmail.trim()} className="gap-2 shrink-0">
                      {testingEmail ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          {t("emailTest.sending")}
                        </>
                      ) : (
                        <>
                          <Send className="h-4 w-4" />
                          {t("emailTest.send")}
                        </>
                      )}
                    </Button>
                  </div>
                </div>
              </form>

              {lastTestResult && (
                <div
                  className={`flex items-start gap-3 p-4 rounded-xl border ${
                    lastTestResult.success
                      ? "bg-green-500/10 border-green-500/30 text-green-700 dark:text-green-300"
                      : "bg-red-500/10 border-red-500/30 text-red-700 dark:text-red-300"
                  }`}
                >
                  {lastTestResult.success ? (
                    <CheckCircle2 className="h-5 w-5 shrink-0 mt-0.5" />
                  ) : (
                    <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
                  )}
                  <div className="text-xs space-y-0.5">
                    <p className="font-semibold">
                      {lastTestResult.success
                        ? t("emailTest.success", { email: lastTestResult.email })
                        : t("emailTest.error")}
                    </p>
                    <p className="opacity-70">
                      {t("timestamp")}
                      {lastTestResult.timestamp}
                    </p>
                  </div>
                </div>
              )}

              {config?.smtp_host && (
                <div className="rounded-xl border bg-muted/30 p-4 text-xs space-y-2">
                  <p className="font-bold uppercase tracking-wider text-muted-foreground text-[10px]">
                    {t("currentSmtpServiceDetails")}
                  </p>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-muted-foreground">
                    <div>
                      <span className="font-medium text-foreground">{t("host")}</span>
                      {config.smtp_host}
                    </div>
                    {config.smtp_port && (
                      <div>
                        <span className="font-medium text-foreground">{t("port")}</span>
                        {config.smtp_port}
                      </div>
                    )}
                    {config.smtp_from && (
                      <div>
                        <span className="font-medium text-foreground">{t("from")}</span>
                        {config.smtp_from}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
