"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState, useEffect } from "react";
import { Loader2, Home, ChevronRight } from "lucide-react";
import Link from "next/link";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { validateQCMFile } from "@/lib/qcm-utils";
import { apiFetch, getMaterialFileUrl } from "@/lib/api-client";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { toast } from "sonner";

function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function EditQCMPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const materialId = pathname.replace(/^\/qcm\//, "").replace(/\/edit\/?$/, "").replace(/\/$/, "");
  const draftIndexParam = searchParams.get("draftIndex");
  const draftIndex = draftIndexParam !== null ? parseInt(draftIndexParam, 10) : null;

  const [qcmData, setQcmData] = useState<QCMFile | null>(null);
  const [initialMeta, setInitialMeta] = useState<Partial<QCMMeta> | null>(null);
  const [versionLock, setVersionLock] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const addOperation = useStagingStore((s) => s.addOperation);
  const updateOperation = useStagingStore((s) => s.updateOperation);

  useEffect(() => {
    if (draftIndex !== null) {
      const ops = useStagingStore.getState().operations;
      const staged = ops[draftIndex];
      if (staged) {
        const op = unwrapOp(staged);
        if (op.op === "edit_material") {
          const qcmDraft = (op.metadata as Record<string, unknown> | undefined)?.qcm_draft as QCMFile | undefined;
          if (qcmDraft && validateQCMFile(qcmDraft)) {
            setQcmData(qcmDraft);
            setVersionLock(op.version_lock);
            setInitialMeta({
              title: op.title || "",
              description: op.description || "",
              tags: op.tags || [],
            });
            setLoading(false);
            return;
          }
        }
      }
    }

    let cancelled = false;

    (async () => {
      try {
        const [materialData, fileUrl] = await Promise.all([
          apiFetch<Record<string, unknown>>(`/materials/${materialId}`),
          getMaterialFileUrl(materialId),
        ]);

        const res = await fetch(fileUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!validateQCMFile(json)) throw new Error("Invalid QCM file format");

        if (!cancelled) {
          setQcmData(json);
          const versionInfo = materialData.current_version_info as Record<string, unknown> | undefined;
          setVersionLock(versionInfo?.version_lock as number | undefined);
          setInitialMeta({
            title: String(materialData.title ?? ""),
            description: String(materialData.description ?? ""),
            tags: (materialData.tags as string[] | undefined) ?? [],
          });
        }
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load QCM");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [materialId, draftIndex]);

  const handleSubmit = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSubmitting(true);
    try {
      const staged = await apiFetch<{
        file_key: string;
        sha256: string;
        file_size: number;
      }>("/qcm/stage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: qcm }),
      });

      const fileName = `${slugify(meta.title) || "qcm"}.qcm`;
      const pr = await apiFetch<{ id: string }>("/pull-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: `Modification du QCM : ${meta.title}`,
          operations: [
            {
              op: "edit_material",
              material_id: materialId,
              file_key: staged.file_key,
              file_name: fileName,
              file_size: staged.file_size,
              file_mime_type: "application/vnd.wikint.qcm+json",
              content_sha256: staged.sha256,
              diff_summary: "QCM modifié via l'éditeur",
              version_lock: versionLock,
            },
          ],
        }),
      });

      toast.success("QCM mis à jour avec succès");
      router.push(`/pull-requests/${pr.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur lors de la soumission");
      setIsSubmitting(false);
    }
  };

  const handleSaveDraft = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSavingDraft(true);
    try {
      const staged = await apiFetch<{
        file_key: string;
        sha256: string;
        file_size: number;
      }>("/qcm/stage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: qcm }),
      });
      const fileName = `${slugify(meta.title) || "qcm"}.qcm`;
      const op = {
        op: "edit_material" as const,
        material_id: materialId,
        title: meta.title,
        description: meta.description || null,
        tags: meta.tags ?? [],
        file_key: staged.file_key,
        file_name: fileName,
        file_size: staged.file_size,
        file_mime_type: "application/vnd.wikint.qcm+json",
        diff_summary: "QCM modifié via l'éditeur",
        version_lock: versionLock,
        metadata: { qcm_draft: qcm },
      };
      if (draftIndex !== null) {
        updateOperation(draftIndex, op);
      } else {
        addOperation(op);
      }
      toast.success("QCM enregistré dans le brouillon");
      router.back();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur lors de l'enregistrement");
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
        {error ?? "Failed to load QCM"}
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
        <span className="truncate max-w-[160px] text-muted-foreground">{initialMeta?.title}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        <span className="text-foreground font-medium shrink-0">Modifier</span>
      </nav>
      <h1 className="text-xl font-bold mb-6">Modifier le QCM</h1>
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
