import type { Metadata } from "next";
import { resourceMetadata, sitePath } from "@/lib/page-metadata";

type Props = { params: Promise<{ materialId: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { materialId } = await params;
  return resourceMetadata(sitePath("qcm", [materialId]));
}

export default function MaterialPage() {
  return null;
}
