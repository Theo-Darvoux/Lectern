import { PRDetailPageContent } from "@/components/pr/pr-detail-page-content";

export const dynamicParams = false;
export function generateStaticParams() { return [{ id: "_" }]; }
export default function PRDetailPage() {
  return <PRDetailPageContent />;
}
