"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { Loader2 } from "lucide-react";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { apiFetch } from "@/lib/api-client";
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

function NewQCMPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const directoryId = searchParams.get("directoryId");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (qcm: QCMFile, meta: QCMMeta) => {
    setIsSubmitting(true);
    try {
      // 1. Stage the QCM file in the CAS
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

      // 2. Create a PR with a create_material operation
      const pr = await apiFetch<{ id: string }>("/pull-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: `Création du QCM : ${meta.title}`,
          operations: [
            {
              op: "create_material",
              title: meta.title,
              type: meta.type,
              description: meta.description || undefined,
              directory_id: directoryId || null,
              file_key: staged.file_key,
              file_name: fileName,
              file_size: staged.file_size,
              file_mime_type: "application/vnd.wikint.qcm+json",
              content_sha256: staged.sha256,
              tags: meta.tags ?? [],
            },
          ],
        }),
      });

      toast.success("QCM soumis avec succès");
      router.push(`/pull-requests/${pr.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur lors de la soumission");
      setIsSubmitting(false);
    }
  };

  return (
    <div className="container mx-auto max-w-3xl px-4 py-6 max-sm:pb-36 sm:pb-28">
      <h1 className="text-xl font-bold mb-6">Créer un QCM</h1>
      <QCMEditor
        targetDirectoryId={directoryId ?? undefined}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
      />
    </div>
  );
}

export default function NewQCMPage() {
  return (
    <AuthGuard requireOnboarded>
      <Suspense
        fallback={
          <div className="flex justify-center p-12">
            <Loader2 className="animate-spin h-6 w-6 text-muted-foreground" />
          </div>
        }
      >
        <NewQCMPageInner />
      </Suspense>
    </AuthGuard>
  );
}
