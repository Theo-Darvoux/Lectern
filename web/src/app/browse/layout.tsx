import { BrowsePageContent } from "@/components/browse/browse-page-content";

// Sits above the [[...path]] segment, so it stays mounted (and keeps its state)
// while the user moves between folders and documents. The page reads the path
// with usePathname.
export default function BrowseLayout() {
  return <BrowsePageContent />;
}
