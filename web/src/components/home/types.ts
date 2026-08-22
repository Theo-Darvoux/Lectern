export interface MaterialVersionInfo {
    id: string;
    material_id: string;
    version_number: number;
    file_key: string | null;
    file_name: string | null;
    file_size: number | null;
    file_mime_type: string | null;
    diff_summary: string | null;
    author_id: string | null;
    pr_id: string | null;
    virus_scan_result: string;
    created_at: string;
}

export interface MaterialDetail {
    id: string;
    directory_id: string | null;
    directory_path: string | null;
    title: string;
    slug: string;
    description: string | null;
    type: string;
    current_version: number;
    parent_material_id: string | null;
    author_id: string | null;
    metadata: Record<string, unknown>;
    download_count: number;
    total_views: number;
    views_today: number;
    like_count: number;
    is_liked: boolean;
    is_favourited: boolean;
    attachment_count: number;
    tags: string[];
    created_at: string;
    updated_at: string;
    current_version_info: MaterialVersionInfo | null;
}

export interface DirectoryDetail {
    id: string;
    parent_id: string | null;
    name: string;
    slug: string;
    type: string;
    description: string | null;
    metadata: Record<string, unknown>;
    sort_order: number;
    is_system: boolean;
    tags: string[];
    full_path: string | null;
    preview_material_ids: string[];
    created_at: string;
}

export interface FeaturedItem {
    id: string;
    material: MaterialDetail | null;
    directory: DirectoryDetail | null;
    title: string | null;
    description: string | null;
    start_at: string;
    end_at: string;
    priority: number;
}

export interface PullRequestOut {
    id: string;
    type: string;
    status: string;
    title: string;
    description: string | null;
    author: { id: string; display_name: string | null; avatar_url: string | null } | null;
    created_at: string;
    updated_at?: string;
    approved_at?: string | null;
    summary_types?: string[];
    virus_scan_result?: string;
    reverted_by_pr_id?: string | null;
    reverts_pr_id?: string | null;
}

export interface HomeStats {
    total_materials: number;
    total_directories: number;
    open_prs: number;
    my_contributions: number;
}

export interface HomeData {
    featured: FeaturedItem[];
    popular_today: MaterialDetail[];
    popular_14d: MaterialDetail[];
    recent_prs: PullRequestOut[];
    recent_favourites: MaterialDetail[];
    recently_viewed: MaterialDetail[];
    recently_added: MaterialDetail[];
    stats: HomeStats;
}
