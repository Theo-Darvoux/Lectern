import type { MaterialDetail } from "@/components/home/types";

export const PROFILE_MATERIAL_GRID =
  "grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4";

export interface ProfileMaterialSummary {
  id: string;
  title?: string;
  description?: string | null;
  type?: string;
  slug?: string;
  created_at?: string;
  updated_at?: string;
  directory_id?: string | null;
  directory_path?: string | null;
  download_count?: number;
  total_views?: number;
  like_count?: number;
  is_liked?: boolean;
  is_favourited?: boolean;
  metadata?: Record<string, unknown>;
  author?: { id: string } | null;
}

export function toProfileMaterialDetail(item: ProfileMaterialSummary): MaterialDetail {
  const createdAt = item.created_at ?? new Date(0).toISOString();

  return {
    id: item.id,
    directory_id: item.directory_id ?? null,
    directory_path: item.directory_path ?? null,
    title: item.title ?? item.id,
    slug: item.slug ?? item.id,
    description: item.description ?? null,
    type: item.type ?? "document",
    current_version: 0,
    parent_material_id: null,
    author_id: item.author?.id ?? null,
    metadata: item.metadata ?? {},
    download_count: item.download_count ?? 0,
    total_views: item.total_views ?? 0,
    views_today: 0,
    like_count: item.like_count ?? 0,
    is_liked: item.is_liked ?? false,
    is_favourited: item.is_favourited ?? false,
    attachment_count: 0,
    tags: [],
    created_at: createdAt,
    updated_at: item.updated_at ?? createdAt,
    current_version_info: null,
  };
}
