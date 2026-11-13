import { PRPreviewPageContent } from "@/components/pr/pr-preview-page-content";

// Sits above the [opIndex] segment, so it stays mounted (and keeps its state)
// while the reviewer steps through the operations. The page reads the index
// with usePathname.
export default function PRPreviewLayout() {
  return <PRPreviewPageContent />;
}
