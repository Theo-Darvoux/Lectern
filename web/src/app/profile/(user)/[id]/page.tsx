import type { Metadata } from "next";
import { resourceMetadata, sitePath } from "@/lib/page-metadata";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return resourceMetadata(sitePath("profile", [id]));
}

// The profile is rendered by profile/(user)/layout.tsx.
export default function ProfilePage() {
  return null;
}
