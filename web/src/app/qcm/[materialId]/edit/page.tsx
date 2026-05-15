"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { validateQCMFile } from "@/lib/qcm-utils";
import { apiFetch, getMaterialFileUrl } from "@/lib/api-client";
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
  const params = useParams();
  const materialId = String(params.materialId ?? "");

  const [qcmData, setQcmData] = useState<QCMFile | null>(null);
  const [initialMeta, setInitialMeta] = useState<Partial<QCMMeta> | null>(null);
  const [versionLock, setVersionLock] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // Fetch material metadata and QCM file in parallel
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

    return () => {
      cancelled = true;
    };
  }, [materialId]);

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
    <div className="container mx-auto max-w-3xl px-4 py-6 max-sm:pb-36 sm:pb-28">
      <h1 className="text-xl font-bold mb-6">Modifier le QCM</h1>
      <QCMEditor
        initialData={qcmData}
        initialMeta={initialMeta}
        materialId={materialId}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
      />
    </div>
  );
}

export default function EditQCMPage() {
  return (
    <AuthGuard requireOnboarded>
      <EditQCMPageInner />
    </AuthGuard>
  );
}
