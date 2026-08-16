import { apiFetch } from "@/lib/api-client";

export type SavedTargetType = "material" | "directory";

export interface SavedItem {
  target_type: SavedTargetType;
  target_id: string;
  title: string;
  item_type: string;
  description: string | null;
  href: string;
  added_at: string;
}

export interface SavedLibrary {
  items: SavedItem[];
}

export interface CollectionSummary {
  id: string;
  name: string;
  item_count: number;
  created_at: string;
  updated_at: string;
  contains_target: boolean;
}

export interface CollectionDetail extends CollectionSummary {
  items: SavedItem[];
}

export async function fetchSavedLibrary(): Promise<SavedLibrary> {
  return apiFetch<SavedLibrary>("/users/me/saved");
}

export async function fetchCollections(target?: {
  targetType: SavedTargetType;
  targetId: string;
}): Promise<CollectionSummary[]> {
  const params = new URLSearchParams();
  if (target) {
    params.set("target_type", target.targetType);
    params.set("target_id", target.targetId);
  }
  const query = params.toString();
  return apiFetch<CollectionSummary[]>(`/collections${query ? `?${query}` : ""}`);
}

export async function fetchCollection(id: string): Promise<CollectionDetail> {
  return apiFetch<CollectionDetail>(`/collections/${id}`);
}

export async function createCollection(name: string): Promise<CollectionSummary> {
  return apiFetch<CollectionSummary>("/collections", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function renameCollection(
  id: string,
  name: string,
): Promise<CollectionSummary> {
  return apiFetch<CollectionSummary>(`/collections/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deleteCollection(id: string): Promise<void> {
  await apiFetch<void>(`/collections/${id}`, { method: "DELETE" });
}

export async function addCollectionItem(
  collectionId: string,
  targetType: SavedTargetType,
  targetId: string,
): Promise<void> {
  await apiFetch<void>(`/collections/${collectionId}/items`, {
    method: "POST",
    body: JSON.stringify({ target_type: targetType, target_id: targetId }),
  });
}

export async function removeCollectionItem(
  collectionId: string,
  targetType: SavedTargetType,
  targetId: string,
): Promise<void> {
  await apiFetch<void>(
    `/collections/${collectionId}/items/${targetType}/${targetId}`,
    { method: "DELETE" },
  );
}
