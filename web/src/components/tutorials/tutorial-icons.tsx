import type { ElementType } from "react";
import {
    Compass,
    FolderOpen,
    Upload,
    GitPullRequest,
    ListChecks,
    MessageSquare,
    ShieldCheck,
    Flag,
    Settings,
    UserCircle,
    HelpCircle,
} from "lucide-react";

/**
 * Icons referenced by tutorials (`Tutorial.icon`) and the Help center. Kept as
 * an explicit map so the bundle only pulls the icons we use.
 */
const ICONS: Record<string, ElementType> = {
    Compass,
    FolderOpen,
    Upload,
    GitPullRequest,
    ListChecks,
    MessageSquare,
    ShieldCheck,
    Flag,
    Settings,
    UserCircle,
    HelpCircle,
};

export function tutorialIcon(name: string): ElementType {
    return ICONS[name] ?? HelpCircle;
}
