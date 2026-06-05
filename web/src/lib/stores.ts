import { create } from "zustand";
import { isRestrictedTarget } from "@/lib/utils";
import { safeLocalStorage } from "@/lib/safe-storage";

const TREE_SIDEBAR_STORAGE_KEY = "browse-tree-sidebar-open";

function readInitialTreeSidebarOpen(): boolean {
    if (typeof window === "undefined") return false;
    const stored = safeLocalStorage.getItem(TREE_SIDEBAR_STORAGE_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    // Default: open on wide viewports, closed otherwise
    try {
        return window.matchMedia("(min-width: 1025px)").matches;
    } catch {
        return false;
    }
}

function persistTreeSidebarOpen(open: boolean): void {
    safeLocalStorage.setItem(TREE_SIDEBAR_STORAGE_KEY, open ? "1" : "0");
}

import type { UserBrief } from "@/lib/guest";

interface AuthState {
    user: UserBrief | null;
    isAuthenticated: boolean;
    isLoading: boolean;
    setUser: (user: UserBrief | null) => void;
    setLoading: (loading: boolean) => void;
    logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
    user: null,
    isAuthenticated: false,
    isLoading: true,
    setUser: (user) => set({ user, isAuthenticated: !!user, isLoading: false }),
    setLoading: (isLoading) => set({ isLoading }),
    logout: () => set({ user: null, isAuthenticated: false, isLoading: false }),
}));

export type SidebarTab = "details" | "edits" | "chat" | "annotations";

interface SidebarTarget {
    type: "directory" | "material";
    id: string;
    data: Record<string, unknown>;
}

interface UIState {
    sidebarOpen: boolean;
    sidebarTab: SidebarTab;
    sidebarTarget: SidebarTarget | null;
    searchOpen: boolean;
    hideFooter: boolean;
    materialActionsOpen: boolean;
    treeSidebarOpen: boolean;
    activeViewerType: string | null;
    navbarVisible: boolean;
    openSidebar: (tab: SidebarTab, target: SidebarTarget) => void;
    setSidebarTarget: (target: SidebarTarget) => void;
    updateSidebarData: (data: Record<string, unknown>) => void;
    closeSidebar: () => void;
    setSidebarTab: (tab: SidebarTab) => void;
    setSidebarOpen: (open: boolean) => void;
    setSearchOpen: (open: boolean) => void;
    setMaterialActionsOpen: (open: boolean) => void;
    setHideFooter: (hide: boolean) => void;
    toggleSidebar: () => void;
    setTreeSidebarOpen: (open: boolean) => void;
    toggleTreeSidebar: () => void;
    setActiveViewerType: (type: string | null) => void;
    setNavbarVisible: (visible: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
    sidebarOpen: false,
    sidebarTab: "details",
    sidebarTarget: null,
    searchOpen: false,
    hideFooter: false,
    materialActionsOpen: false,
    treeSidebarOpen: readInitialTreeSidebarOpen(),
    activeViewerType: null,
    navbarVisible: true,
    openSidebar: (tab, target) =>
        set({ sidebarOpen: true, sidebarTab: tab, sidebarTarget: target }),
    setSidebarTarget: (target) =>
        set((state) => {
            // Auto-fallback to "details" if the new target is restricted (drafts)
            // and the current tab is one that gets disabled for restricted targets.
            const isRestricted = isRestrictedTarget(target.id);
            const restrictedTabs: SidebarTab[] = ["chat", "annotations", "edits"];
            const nextTab =
                isRestricted && restrictedTabs.includes(state.sidebarTab)
                    ? "details"
                    : state.sidebarTab;
            return { sidebarTarget: target, sidebarTab: nextTab };
        }),
    updateSidebarData: (data) =>
        set((state) => ({
            sidebarTarget: state.sidebarTarget
                ? { ...state.sidebarTarget, data: { ...state.sidebarTarget.data, ...data } }
                : null
        })),
    closeSidebar: () => set({ sidebarOpen: false }),
    setSidebarTab: (tab) => set({ sidebarTab: tab }),
    setSidebarOpen: (open) => set({ sidebarOpen: open }),
    setSearchOpen: (open) => set({ searchOpen: open }),
    setMaterialActionsOpen: (open) => set({ materialActionsOpen: open }),
    setHideFooter: (hide) => set({ hideFooter: hide }),
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
    setTreeSidebarOpen: (open) => {
        persistTreeSidebarOpen(open);
        set({ treeSidebarOpen: open });
    },
    toggleTreeSidebar: () =>
        set((state) => {
            const next = !state.treeSidebarOpen;
            persistTreeSidebarOpen(next);
            return { treeSidebarOpen: next };
        }),
    setActiveViewerType: (type) => set({ activeViewerType: type }),
    setNavbarVisible: (navbarVisible) => set({ navbarVisible }),
}));

// ---------------------------------------------------------------------------
// Like overrides — client-side cache of like state toggled this session.
// Lets MaterialLineItem / DirectoryLineItem / InteractionBar stay in sync
// without waiting for a re-fetch, and prevents the sidebar from reverting
// to stale server data when reopened after a like.
// ---------------------------------------------------------------------------
interface LikeOverridesState {
    materialOverrides: Record<string, { isLiked: boolean; likeCount: number }>;
    directoryOverrides: Record<string, { isLiked: boolean; likeCount: number }>;
    setMaterialLike: (id: string, isLiked: boolean, likeCount: number) => void;
    setDirectoryLike: (id: string, isLiked: boolean, likeCount: number) => void;
}

export const useLikeOverrides = create<LikeOverridesState>((set) => ({
    materialOverrides: {},
    directoryOverrides: {},
    setMaterialLike: (id, isLiked, likeCount) =>
        set((s) => ({
            materialOverrides: { ...s.materialOverrides, [id]: { isLiked, likeCount } },
        })),
    setDirectoryLike: (id, isLiked, likeCount) =>
        set((s) => ({
            directoryOverrides: { ...s.directoryOverrides, [id]: { isLiked, likeCount } },
        })),
}));

// ---------------------------------------------------------------------------
// Browse refresh store — incremented after a direct-approved PR so the browse
// page re-fetches immediately without a manual page reload.
// ---------------------------------------------------------------------------
interface BrowseRefreshState {
    refreshCount: number;
    triggerBrowseRefresh: () => void;
}

export const useBrowseRefreshStore = create<BrowseRefreshState>((set) => ({
    refreshCount: 0,
    triggerBrowseRefresh: () =>
        set((state) => ({ refreshCount: state.refreshCount + 1 })),
}));

interface DirectoryIconOverrideState {
    overrides: Map<string, string | null>;
    setIconOverride: (directoryId: string, icon: string | null) => void;
}

export const useDirectoryIconOverrides = create<DirectoryIconOverrideState>((set) => ({
    overrides: new Map(),
    setIconOverride: (directoryId, icon) =>
        set((state) => {
            const next = new Map(state.overrides);
            next.set(directoryId, icon);
            return { overrides: next };
        }),
}));

interface DirectoryColorOverrideState {
    overrides: Map<string, string | null>;
    setColorOverride: (directoryId: string, color: string | null) => void;
}

export const useDirectoryColorOverrides = create<DirectoryColorOverrideState>((set) => ({
    overrides: new Map(),
    setColorOverride: (directoryId, color) =>
        set((state) => {
            const next = new Map(state.overrides);
            next.set(directoryId, color);
            return { overrides: next };
        }),
}));

interface NotificationState {
    unreadCount: number;
    setUnreadCount: (count: number) => void;
    increment: () => void;
    decrement: (by?: number) => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
    unreadCount: 0,
    setUnreadCount: (count) => set({ unreadCount: Math.max(0, count) }),
    increment: () => set((state) => ({ unreadCount: state.unreadCount + 1 })),
    decrement: (by = 1) =>
        set((state) => ({ unreadCount: Math.max(0, state.unreadCount - by) })),
}));

interface PRState {
    openPRCount: number;
    setOpenPRCount: (count: number) => void;
}

export const usePRStore = create<PRState>((set) => ({
    openPRCount: 0,
    setOpenPRCount: (count) => set({ openPRCount: Math.max(0, count) }),
}));

export interface PublicConfig {
    site_name: string;
    site_name_style: string | null;
    site_description: string;
    site_logo_url: string | null;
    site_favicon_url: string | null;
    primary_color: string;
    footer_text: string;
    footer_logo_url: string | null;
    organization_url: string | null;
    og_image_url: string | null;
    bg_watermark_url: string | null;
    bg_watermark_opacity_light: number | null;
    bg_watermark_opacity_dark: number | null;
    legal_name: string | null;
    legal_address: string | null;
    legal_siret: string | null;
    contact_email: string | null;
    dpo_email: string | null;
    dpo_address: string | null;
    data_transfers: string | null;
    legal_version: string | null;
    totp_enabled: boolean;
    google_enabled: boolean;
    google_client_id: string | null;
    classic_enabled: boolean;
    allow_all_domains: boolean;
    guest_access_enabled: boolean;
    max_contribution_note_length: number;
}

interface ConfigState {
    config: PublicConfig | null;
    setConfig: (config: PublicConfig) => void;
    updateConfig: (patch: Partial<PublicConfig>) => void;
}

export const useConfigStore = create<ConfigState>((set) => ({
    config: null,
    setConfig: (config) => set({ config }),
    updateConfig: (patch) => set((state) => ({
        config: state.config ? { ...state.config, ...patch } : null
    })),
}));

