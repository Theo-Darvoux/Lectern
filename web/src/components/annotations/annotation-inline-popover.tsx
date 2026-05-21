"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, MessageSquare } from "lucide-react";
import type { ThreadData } from "@/hooks/use-annotations";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { API_BASE } from "@/lib/api-client";
import { useTranslations } from "next-intl";

function getInitials(name: string | null): string {
    if (!name) return "?";
    return name.split(" ").map((s) => s[0]).join("").toUpperCase().slice(0, 2);
}

interface AnnotationInlinePopoverProps {
    thread: ThreadData;
    clientX: number;
    clientY: number;
    onClose: () => void;
}

export function AnnotationInlinePopover({
    thread,
    clientX,
    clientY,
    onClose,
}: AnnotationInlinePopoverProps) {
    const t = useTranslations("Annotations");
    const popoverRef = useRef<HTMLDivElement>(null);
    const [mounted, setMounted] = useState(false);
    const [style, setStyle] = useState<React.CSSProperties>({
        position: "fixed",
        visibility: "hidden",
        top: 0,
        left: 0,
    });

    useEffect(() => { setMounted(true); }, []);

    useLayoutEffect(() => {
        if (!mounted) return;
        const el = popoverRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        let top = clientY - rect.height - 10;
        let left = clientX - rect.width / 2;

        if (top < 8) top = clientY + 18;
        left = Math.max(8, Math.min(left, vw - rect.width - 8));
        if (top + rect.height > vh - 8) top = vh - 8 - rect.height;

        setStyle({ position: "fixed", top, left, visibility: "visible", zIndex: 9999 });
    }, [mounted, clientX, clientY]);

    useEffect(() => {
        const handler = (e: MouseEvent | TouchEvent) => {
            if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
                onClose();
            }
        };
        document.addEventListener("mousedown", handler);
        document.addEventListener("touchstart", handler);
        return () => {
            document.removeEventListener("mousedown", handler);
            document.removeEventListener("touchstart", handler);
        };
    }, [onClose]);

    const { root, replies } = thread;
    const authorName = root.author?.display_name ?? t("deletedUser");
    const date = new Date(root.created_at).toLocaleDateString();

    if (!mounted) return null;

    return createPortal(
        <div
            ref={popoverRef}
            style={style}
            className="w-72 rounded-lg border bg-popover p-3 shadow-lg ring-1 ring-black/5 dark:ring-white/10"
        >
            <button
                onMouseDown={(e) => e.stopPropagation()}
                onClick={onClose}
                className="absolute right-2 top-2 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
                <X className="h-3.5 w-3.5" />
            </button>

            <div className="flex gap-2">
                <Avatar className="h-6 w-6 shrink-0 mt-0.5">
                    <AvatarImage
                        src={
                            root.author?.avatar_url && root.author_id
                                ? `${API_BASE}/users/${root.author_id}/avatar?v=${encodeURIComponent(root.author.avatar_url)}`
                                : undefined
                        }
                    />
                    <AvatarFallback className="text-[9px]">
                        {getInitials(root.author?.display_name ?? null)}
                    </AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1 pr-4">
                    <div className="flex items-baseline gap-2">
                        <span className="text-xs font-semibold truncate">{authorName}</span>
                        <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums opacity-80">
                            {date}
                        </span>
                    </div>
                    <p className="text-xs text-foreground/90 leading-normal mt-0.5 line-clamp-6 whitespace-pre-wrap break-words">
                        {root.body}
                    </p>
                </div>
            </div>

            {replies.length > 0 && (
                <div className="mt-2 border-t pt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    <MessageSquare className="h-3 w-3 shrink-0" />
                    <span>{t("replyCount", { count: replies.length })}</span>
                </div>
            )}
        </div>,
        document.body,
    );
}
