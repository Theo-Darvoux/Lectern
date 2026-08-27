/**
 * Unified registry for material viewers to register print capabilities.
 * 
 * Viewers like MarkdownViewer or OfficeViewer register their content getters or 
 * print handlers here on mount, and the usePrint hook consumes them.
 */

interface PrintRegistryEntry {
  /** Returns the rendered HTML content of the viewer (used by Markdown) */
  getContent?: () => string | null | Promise<string | null>;
  /** Directly triggers the viewer's built-in print (used by Office/OnlyOffice) */
  print?: () => void;
}

const registry = new Map<string, PrintRegistryEntry>();

export function registerViewerPrint(materialId: string, entry: PrintRegistryEntry) {
  registry.set(materialId, entry);
}

export function unregisterViewerPrint(materialId: string) {
  registry.delete(materialId);
}

export function getViewerPrint(materialId: string): PrintRegistryEntry | undefined {
  return registry.get(materialId);
}
