import type { Tutorial } from "./types";

/**
 * All product tutorials. Step `target`s point at `[data-tutorial="..."]`
 * anchors placed on the real UI. The engine skips any step whose target is
 * absent (e.g. a nav item the current role can't see), so role-specific steps
 * can live in an otherwise shared tutorial.
 *
 * Keep tutorial ids in sync with the server allowlist (see
 * `api/app/services/user.py` validation) and the `Tutorials.<id>` i18n keys.
 */
export const TUTORIALS: Tutorial[] = [
    {
        id: "welcome",
        minTier: "guest",
        icon: "Compass",
        autoStartOn: "/",
        steps: [
            { id: "intro", placement: "center" },
            { id: "home", target: '[data-tutorial="nav-home"]', placement: "bottom" },
            { id: "search", target: '[data-tutorial="nav-search"]', placement: "bottom" },
            {
                id: "notifications",
                target: '[data-tutorial="nav-notifications"]',
                placement: "bottom",
                waitForTarget: true,
            },
            { id: "profile", target: '[data-tutorial="nav-profile"]', placement: "left" },
            { id: "help", target: '[data-tutorial="nav-profile"]', placement: "left" },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "browse",
        minTier: "guest",
        icon: "FolderOpen",
        autoStartOn: "/browse",
        steps: [
            { id: "intro", placement: "center", route: "/browse" },
            { id: "tree", target: '[data-tutorial="sidebar-tree"]', placement: "right", waitForTarget: true },
            { id: "breadcrumb", target: '[data-tutorial="breadcrumb"]', placement: "bottom", waitForTarget: true },
            { id: "viewMode", target: '[data-tutorial="view-mode"]', placement: "bottom", waitForTarget: true },
            { id: "card", target: '[data-tutorial="browse-item"]', placement: "top", waitForTarget: true },
            { id: "search", target: '[data-tutorial="nav-search"]', placement: "bottom" },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "upload",
        minTier: "student",
        icon: "Upload",
        steps: [
            { id: "intro", placement: "center", route: "/browse" },
            { id: "create", target: '[data-tutorial="create-menu"]', placement: "bottom", waitForTarget: true },
            { id: "dropzone", placement: "center" },
            { id: "staging", target: '[data-tutorial="staging-fab"]', placement: "left", waitForTarget: true },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "contribute",
        minTier: "student",
        icon: "GitPullRequest",
        steps: [
            { id: "intro", placement: "center", route: "/browse" },
            { id: "create", target: '[data-tutorial="create-menu"]', placement: "bottom", waitForTarget: true },
            { id: "edit", placement: "center" },
            { id: "staging", target: '[data-tutorial="staging-fab"]', placement: "left", waitForTarget: true },
            { id: "track", target: '[data-tutorial="nav-contributions"]', placement: "bottom", waitForTarget: true },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "qcm",
        minTier: "student",
        icon: "ListChecks",
        autoStartOn: "/qcm/new",
        steps: [
            { id: "intro", placement: "center", route: "/qcm/new" },
            { id: "name", target: '[data-tutorial="qcm-title"]', placement: "bottom", waitForTarget: true },
            { id: "import", target: '[data-tutorial="qcm-import"]', placement: "bottom", waitForTarget: true },
            { id: "addChapter", target: '[data-tutorial="qcm-add-chapter"]', placement: "top", waitForTarget: true },
            { id: "preview", target: '[data-tutorial="qcm-preview"]', placement: "top", waitForTarget: true },
            { id: "submit", target: '[data-tutorial="qcm-submit"]', placement: "top", waitForTarget: true },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "annotations",
        minTier: "student",
        icon: "MessageSquare",
        steps: [
            { id: "intro", placement: "center" },
            { id: "select", placement: "center" },
            { id: "panel", target: '[data-tutorial="sidebar-tab-annotations"]', placement: "left", waitForTarget: true },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "review-pr",
        minTier: "staff",
        icon: "ShieldCheck",
        autoStartOn: "/moderator/pull-requests",
        steps: [
            { id: "intro", placement: "center", route: "/moderator/pull-requests" },
            { id: "queue", target: '[data-tutorial="mod-nav-prs"]', placement: "bottom", waitForTarget: true },
            { id: "open", placement: "center" },
            { id: "decide", placement: "center" },
            { id: "outro", placement: "center" },
        ],
    },
    {
        id: "moderation",
        minTier: "staff",
        icon: "Flag",
        autoStartOn: "/moderator",
        steps: [
            { id: "intro", placement: "center", route: "/moderator" },
            { id: "flags", target: '[data-tutorial="mod-nav-flags"]', placement: "bottom", waitForTarget: true },
            { id: "directories", target: '[data-tutorial="mod-nav-directories"]', placement: "bottom", waitForTarget: true },
            { id: "featured", target: '[data-tutorial="mod-nav-featured"]', placement: "bottom", waitForTarget: true },
            { id: "outro", placement: "center" },
        ],
    },
];

const BY_ID = new Map(TUTORIALS.map((t) => [t.id, t]));

export function getTutorial(id: string): Tutorial | undefined {
    return BY_ID.get(id);
}
