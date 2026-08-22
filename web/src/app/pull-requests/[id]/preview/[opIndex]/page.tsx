import { PRPreviewPageContent } from "@/components/pr/pr-preview-page-content";

export const dynamicParams = false;
export function generateStaticParams() { return [{ id: "_", opIndex: "0" }]; }
export default function PRPreviewPage() {
  return <PRPreviewPageContent />;
}
