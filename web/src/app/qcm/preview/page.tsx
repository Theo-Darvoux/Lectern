"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import { Loader2, Home, ChevronRight, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { AuthGuard } from "@/components/auth-guard";
import { useStagedQcm } from "@/hooks/use-staged-qcm";
import { useStagingStore } from "@/lib/staging-store";

const QCMViewer = dynamic(
  () => import("@/components/viewers/qcm-viewer").then((mod) => mod.QCMViewer),
  { loading: () => <Skeleton className="h-full w-full" />, ssr: false },
);

function PageSpinner() {
  return (
    <div className="flex justify-center p-12">
      <Loader2 className="animate-spin h-6 w-6 text-muted-foreground" />
    </div>
  );
}

function QCMPreviewPageInner() {
  const t = useTranslations("QCM.draft");
  const searchParams = useSearchParams();
  const router = useRouter();
  const draftId = searchParams.get("draftId");
  const removeOperation = useStagingStore((s) => s.removeOperation);
  const entry = useStagingStore((s) => s.operations.find((e) => e.id === draftId));
  const op = entry?.operation.op === "create_material" ? entry.operation : null;
  const { qcm, isLoading } = useStagedQcm(op?.file_key);

  if (isLoading) return <PageSpinner />;
  if (!draftId || !op || !qcm) {
    return <div className="flex justify-center p-12 text-destructive text-sm">{t("notFound")}</div>;
  }

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 pb-20">
      <nav className="flex items-center gap-1 text-sm text-muted-foreground mb-4">
        <Link href="/browse" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="truncate max-w-[160px] text-muted-foreground">{op.title}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        <span className="text-foreground font-medium shrink-0">{t("preview")}</span>
      </nav>
      <div className="flex items-center justify-between gap-3 mb-6">
        <h1 className="text-xl font-bold">{op.title}</h1>
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => router.push(`/qcm/new?draftId=${draftId}`)}
          >
            <Pencil className="h-3.5 w-3.5" />
            {t("edit")}
          </Button>
          <ConfirmDeleteDialog
            title={t("deleteTitle")}
            description={t("deleteDescription", { title: op.title })}
            onConfirm={() => {
              removeOperation(draftId);
              router.push("/browse");
            }}
            trigger={
              <Button variant="outline" size="sm" className="gap-1.5 text-destructive hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
                {t("delete")}
              </Button>
            }
          />
        </div>
      </div>
      <QCMViewer initialData={qcm} />
    </div>
  );
}

export default function QCMPreviewPage() {
  return (
    <AuthGuard requireOnboarded>
      <Suspense fallback={<PageSpinner />}>
        <QCMPreviewPageInner />
      </Suspense>
    </AuthGuard>
  );
}
