"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { Loader2, Home, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import { useStagedQcm } from "@/hooks/use-staged-qcm";
import { submitDirectOperations } from "@/lib/pr-client";
import { stageQcmFile } from "@/lib/qcm-draft";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { useStagingStore, type CreateMaterialOp } from "@/lib/staging-store";

function PageSpinner() {
  return (
    <div className="flex justify-center p-12">
      <Loader2 className="animate-spin h-6 w-6 text-muted-foreground" />
    </div>
  );
}

function NewQCMPageInner() {
  const t = useTranslations("QCM.draft");
  const tAuto = useTranslations("AutoTitle");
  const router = useRouter();
  const searchParams = useSearchParams();
  const directoryId = searchParams.get("directoryId");
  const draftId = searchParams.get("draftId");

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const addOperation = useStagingStore((s) => s.addOperation);
  const updateOperation = useStagingStore((s) => s.updateOperation);
  const removeOperation = useStagingStore((s) => s.removeOperation);
  const nextTempId = useStagingStore((s) => s.nextTempId);

  const [draft] = useState<CreateMaterialOp | null>(() => {
    const entry = useStagingStore.getState().operations.find((e) => e.id === draftId);
    return entry?.operation.op === "create_material" ? entry.operation : null;
  });
  const { qcm: draftQcm, error: draftError, isLoading } = useStagedQcm(draft?.file_key);

  const buildOperation = async (qcm: QCMFile, meta: QCMMeta): Promise<CreateMaterialOp> => ({
    op: "create_material",
    temp_id: draft?.temp_id ?? nextTempId("mat"),
    directory_id: draft ? draft.directory_id : directoryId || null,
    title: meta.title,
    type: meta.type,
    description: meta.description || undefined,
    tags: meta.tags ?? [],
    ...(await stageQcmFile(qcm, meta.title)),
  });

  const handleSubmit = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSubmitting(true);
    try {
      const pr = await submitDirectOperations(
        [await buildOperation(qcm, meta)],
        t("createTitle", { title: meta.title }),
        undefined,
        tAuto,
      );
      if (!pr) {
        setIsSubmitting(false);
        return;
      }
      if (draftId) removeOperation(draftId);
      router.push(`/pull-requests/${pr.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("submitFailed"));
      setIsSubmitting(false);
    }
  };

  const handleSaveDraft = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSavingDraft(true);
    try {
      const op = await buildOperation(qcm, meta);
      if (!draftId || !updateOperation(draftId, op)) addOperation(op);
      toast.success(t("saved"));
      if (window.history.length > 1) router.back();
      else router.push("/browse");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
      setIsSavingDraft(false);
    }
  };

  if (isLoading) return <PageSpinner />;
  if (draftError) {
    return <div className="flex justify-center p-12 text-destructive text-sm">{draftError}</div>;
  }

  const initialMeta: Partial<QCMMeta> | undefined = draft
    ? { title: draft.title, description: draft.description ?? "", tags: draft.tags ?? [] }
    : undefined;

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 max-sm:pb-36 sm:pb-28">
      <nav className="flex items-center gap-1 text-sm text-muted-foreground mb-4">
        <Link href="/browse" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="text-foreground font-medium">{t("createHeading")}</span>
      </nav>
      <h1 className="text-xl font-bold mb-6">{t("createHeading")}</h1>
      <QCMEditor
        targetDirectoryId={directoryId ?? undefined}
        initialData={draftQcm ?? undefined}
        initialMeta={initialMeta}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        onSaveDraft={handleSaveDraft}
        isSavingDraft={isSavingDraft}
      />
    </div>
  );
}

export default function NewQCMPage() {
  return (
    <AuthGuard requireOnboarded>
      <Suspense fallback={<PageSpinner />}>
        <NewQCMPageInner />
      </Suspense>
    </AuthGuard>
  );
}
