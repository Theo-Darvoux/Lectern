"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import { Loader2, Home, ChevronRight, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ConfirmDeleteDialog } from "@/components/ui/confirm-delete-dialog";
import { AuthGuard } from "@/components/auth-guard";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { validateQCMFile } from "@/lib/qcm-utils";
import type { QCMFile } from "@/lib/qcm-types";
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

const QCMViewer = dynamic(
  () => import("@/components/viewers/qcm-viewer").then((mod) => mod.QCMViewer),
  { loading: () => <Skeleton className="h-full w-full" />, ssr: false },
);

function QCMPreviewPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const removeOperation = useStagingStore((s) => s.removeOperation);
  const draftIndexParam = searchParams.get("draftIndex");
  const draftIndex = draftIndexParam !== null ? parseInt(draftIndexParam, 10) : null;

  if (draftIndex === null) {
    return (
      <div className="flex justify-center p-12 text-destructive text-sm">
        Missing draftIndex parameter
      </div>
    );
  }

  const ops = useStagingStore.getState().operations;
  const staged = ops[draftIndex];
  const op = staged ? unwrapOp(staged) : null;

  const qcmDraft = op?.op === "create_material"
    ? (op.metadata as Record<string, unknown> | undefined)?.qcm_draft
    : null;

  const title = op?.op === "create_material" ? op.title : "";

  if (!qcmDraft || !validateQCMFile(qcmDraft)) {
    return (
      <div className="flex justify-center p-12 text-destructive text-sm">
        Draft QCM data not found or invalid
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 pb-20">
      <nav className="flex items-center gap-1 text-sm text-muted-foreground mb-4">
        <Link href="/browse" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="truncate max-w-[160px] text-muted-foreground">{title}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        <span className="text-foreground font-medium shrink-0">Aperçu</span>
      </nav>
      <div className="flex items-center justify-between gap-3 mb-6">
        <h1 className="text-xl font-bold">{title}</h1>
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => router.push(`/qcm/new?draftIndex=${draftIndex}`)}
          >
            <Pencil className="h-3.5 w-3.5" />
            Modifier
          </Button>
          <ConfirmDeleteDialog
            title="Supprimer le brouillon ?"
            description={`Le brouillon « ${title} » sera définitivement supprimé des modifications en attente.`}
            onConfirm={() => {
              removeOperation(draftIndex);
              router.push("/browse");
            }}
            trigger={
              <Button variant="outline" size="sm" className="gap-1.5 text-destructive hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
                Supprimer
              </Button>
            }
          />
        </div>
      </div>
      <QCMViewer initialData={qcmDraft as QCMFile} />
    </div>
  );
}

export default function QCMPreviewPage() {
  return (
    <AuthGuard requireOnboarded>
      <Suspense fallback={<div className="flex justify-center p-12"><Loader2 className="animate-spin h-6 w-6 text-muted-foreground" /></div>}>
        <QCMPreviewPageInner />
      </Suspense>
    </AuthGuard>
  );
}
