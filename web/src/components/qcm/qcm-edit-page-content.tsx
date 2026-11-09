"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { Loader2, Home, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import { applyStagedEdits } from "@/hooks/use-augmented-listing";
import { getMaterialFileUrl, apiFetch, invalidateMaterialFileUrl } from "@/lib/api-client";
import { MIME_QCM } from "@/lib/file-utils";
import { submitDirectOperations } from "@/lib/pr-client";
import { fetchStagedQcm, stageQcmFile } from "@/lib/qcm-draft";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { validateQCMFile } from "@/lib/qcm-utils";
import { useStagingStore, type EditMaterialOp, type StagedOperation } from "@/lib/staging-store";

/** The draft's pending edit of this material, if any. */
function findDraftEdit(materialId: string): (StagedOperation & { operation: EditMaterialOp }) | null {
  const entry = useStagingStore
    .getState()
    .operations.find((e) => e.operation.op === "edit_material" && e.operation.material_id === materialId);
  return (entry as (StagedOperation & { operation: EditMaterialOp }) | undefined) ?? null;
}

async function fetchLiveQcm(materialId: string): Promise<QCMFile> {
  const res = await fetch(await getMaterialFileUrl(materialId));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  if (!validateQCMFile(json)) throw new Error("Invalid QCM file format");
  return json;
}

function EditQCMPageInner() {
  const t = useTranslations("QCM.draft");
  const tAuto = useTranslations("AutoTitle");
  const router = useRouter();
  const pathname = usePathname();
  const materialId = pathname.replace(/^\/qcm\//, "").replace(/\/edit\/?$/, "").replace(/\/$/, "");

  const [qcmData, setQcmData] = useState<QCMFile | null>(null);
  const [initialMeta, setInitialMeta] = useState<Partial<QCMMeta> | null>(null);
  const [versionLock, setVersionLock] = useState<number | undefined>(undefined);
  const [materialTitle, setMaterialTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const addOperation = useStagingStore((s) => s.addOperation);
  const removeOperation = useStagingStore((s) => s.removeOperation);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const draft = findDraftEdit(materialId)?.operation;
        const hasDraftFile = !!draft?.file_key && draft.file_mime_type === MIME_QCM;
        const [materialData, qcm] = await Promise.all([
          apiFetch<Record<string, unknown>>(`/materials/${materialId}`),
          hasDraftFile ? fetchStagedQcm(draft.file_key!) : fetchLiveQcm(materialId),
        ]);
        if (cancelled) return;

        const merged = applyStagedEdits(materialData, draft ? [draft] : undefined);
        const versionInfo = materialData.current_version_info as Record<string, unknown> | undefined;
        setQcmData(qcm);
        setVersionLock(draft?.version_lock ?? (versionInfo?.version_lock as number | undefined));
        setMaterialTitle(String(materialData.title ?? ""));
        setInitialMeta({
          title: String(merged.title ?? ""),
          description: String(merged.description ?? ""),
          tags: (merged.tags as string[] | undefined) ?? [],
        });
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : t("loadFailed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [materialId, t]);

  const buildOperation = async (qcm: QCMFile, meta: QCMMeta): Promise<EditMaterialOp> => ({
    op: "edit_material",
    material_id: materialId,
    target_title: materialTitle,
    title: meta.title,
    description: meta.description,
    tags: meta.tags ?? [],
    diff_summary: t("diffSummary"),
    version_lock: versionLock,
    ...(await stageQcmFile(qcm, meta.title)),
  });

  const handleSubmit = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSubmitting(true);
    try {
      const pr = await submitDirectOperations(
        [await buildOperation(qcm, meta)],
        t("editTitle", { title: meta.title }),
        undefined,
        tAuto,
      );
      if (!pr) {
        setIsSubmitting(false);
        return;
      }
      const draft = findDraftEdit(materialId);
      if (draft) removeOperation(draft.id);

      // The edit may have applied immediately (auto-approved moderators), creating
      // a new version with a new file_key. Drop the cached presigned URL so the
      // viewer/editor re-fetches the updated content instead of the stale version.
      invalidateMaterialFileUrl(materialId);

      router.push(`/pull-requests/${pr.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("submitFailed"));
      setIsSubmitting(false);
    }
  };

  const handleSaveDraft = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSavingDraft(true);
    try {
      addOperation(await buildOperation(qcm, meta));
      toast.success(t("saved"));
      if (window.history.length > 1) router.back();
      else router.push("/browse");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
      setIsSavingDraft(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center p-12">
        <Loader2 className="animate-spin h-6 w-6 text-muted-foreground" />
      </div>
    );
  }

  if (error || !qcmData || !initialMeta) {
    return (
      <div className="flex justify-center p-12 text-destructive text-sm">
        {error ?? t("loadFailed")}
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 max-sm:pb-36 sm:pb-28">
      <nav className="flex items-center gap-1 text-sm text-muted-foreground mb-4">
        <Link href="/browse" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="truncate max-w-[160px] text-muted-foreground">{initialMeta.title}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        <span className="text-foreground font-medium shrink-0">{t("edit")}</span>
      </nav>
      <h1 className="text-xl font-bold mb-6">{t("editHeading")}</h1>
      <QCMEditor
        initialData={qcmData}
        initialMeta={initialMeta}
        materialId={materialId}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        onSaveDraft={handleSaveDraft}
        isSavingDraft={isSavingDraft}
      />
    </div>
  );
}

export function QCMEditPageContent() {
  return (
    <AuthGuard requireOnboarded>
      <EditQCMPageInner />
    </AuthGuard>
  );
}
