import type { Metadata } from "next";
import { resourceMetadata, sitePath } from "@/lib/page-metadata";

type Props = { params: Promise<{ path?: string[] }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { path } = await params;
  return resourceMetadata(sitePath("browse", path));
}

// The browser is rendered by browse/layout.tsx.
export default function BrowsePage() {
  return null;
}
