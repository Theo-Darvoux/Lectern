"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { Loader2, Home, ChevronRight } from "lucide-react";
import Link from "next/link";
import { AuthGuard } from "@/components/auth-guard";
import { QCMEditor } from "@/components/qcm/qcm-editor";
import type { QCMFile, QCMMeta } from "@/lib/qcm-types";
import { apiFetch } from "@/lib/api-client";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import { MIME_QCM } from "@/lib/file-utils";
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
  const draftIndexParam = searchParams.get("draftIndex");
  const draftIndex = draftIndexParam !== null ? parseInt(draftIndexParam, 10) : null;

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const addOperation = useStagingStore((s) => s.addOperation);
  const updateOperation = useStagingStore((s) => s.updateOperation);

  // If editing a draft, seed the editor with the staged data (read once at mount).
  const draftOp = (() => {
    if (draftIndex === null) return null;
    const ops = useStagingStore.getState().operations;
    const staged = ops[draftIndex];
    if (!staged) return null;
    const op = unwrapOp(staged);
    return op.op === "create_material" ? op : null;
  })();
  const initialData = (draftOp?.metadata as Record<string, unknown> | undefined)?.qcm_draft as QCMFile | undefined;
  const initialMeta: Partial<QCMMeta> | undefined = draftOp
    ? { title: draftOp.title ?? "", description: draftOp.description ?? "", tags: draftOp.tags ?? [] }
    : undefined;

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
              file_mime_type: MIME_QCM,
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
        op: "create_material" as const,
        title: meta.title,
        type: meta.type,
        description: meta.description || undefined,
        directory_id: directoryId || null,
        file_key: staged.file_key,
        file_name: fileName,
        file_size: staged.file_size,
        file_mime_type: MIME_QCM,
        tags: meta.tags ?? [],
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

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 max-sm:pb-36 sm:pb-28">
      <nav className="flex items-center gap-1 text-sm text-muted-foreground mb-4">
        <Link href="/browse" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="text-foreground font-medium">Créer un QCM</span>
      </nav>
      <h1 className="text-xl font-bold mb-6">Créer un QCM</h1>
      <QCMEditor
        targetDirectoryId={directoryId ?? undefined}
        initialData={initialData}
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
