import React from "react";
import { Folder, ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import { compareNatural } from "@/lib/utils";

interface PendingFoldersProps {
    pendingDirPaths: Map<string, string>;
    foldersExpanded: boolean;
    setFoldersExpanded: React.Dispatch<React.SetStateAction<boolean>>;
    editingPath: string | null;
    editValue: string;
    setEditingPath: (path: string | null) => void;
    setEditValue: (val: string) => void;
    commitRename: (path: string) => void;
}

export function PendingFolders({
    pendingDirPaths,
    foldersExpanded,
    setFoldersExpanded,
    editingPath,
    editValue,
    setEditingPath,
    setEditValue,
    commitRename,
}: PendingFoldersProps) {
    const t = useTranslations("Upload");

    if (pendingDirPaths.size === 0) return null;

    return (
        <div className="overflow-hidden rounded-lg border border-green-200 dark:border-green-800 bg-green-50/60 dark:bg-green-950/20">
            <button
                type="button"
                onClick={() => setFoldersExpanded((v) => !v)}
                className="flex w-full items-center justify-between px-3 py-2 text-xs font-medium text-green-700 dark:text-green-400 hover:bg-green-100/50 dark:hover:bg-green-900/20 transition-colors"
            >
                <span>
                    {t("foldersWillBeCreated", { count: pendingDirPaths.size })}
                </span>
                {foldersExpanded ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                )}
            </button>

            {foldersExpanded && (
                <div className="border-t border-green-200 dark:border-green-800 px-2 pb-2 pt-1.5 space-y-0.5">
                    {[...pendingDirPaths.keys()]
                        .sort((a, b) => {
                            const da = a.split("/").length;
                            const db = b.split("/").length;
                            return da !== db ? da - db : compareNatural(a, b);
                        })
                        .map((path) => {
                            const parts = path.split("/");
                            const depth = parts.length - 1;
                            const leafName = parts[parts.length - 1];
                            const isEditing = editingPath === path;

                            return (
                                <div
                                    key={path}
                                    style={{ paddingLeft: `${depth * 14 + 4}px` }}
                                    className="flex items-center gap-1.5 group/dir"
                                >
                                    <Folder className="h-3 w-3 shrink-0 text-green-600 dark:text-green-400" />
                                    {isEditing ? (
                                        <input
                                            autoFocus
                                            value={editValue}
                                            onChange={(e) => setEditValue(e.target.value)}
                                            onBlur={() => commitRename(path)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter") {
                                                    e.preventDefault();
                                                    commitRename(path);
                                                }
                                                if (e.key === "Escape") {
                                                    setEditingPath(null);
                                                    setEditValue("");
                                                }
                                            }}
                                            className="h-5 flex-1 min-w-0 rounded border border-green-400 dark:border-green-600 bg-white dark:bg-green-950 px-1.5 text-[11px] text-green-800 dark:text-green-200 outline-none focus:ring-1 focus:ring-green-500"
                                        />
                                    ) : (
                                        <button
                                            type="button"
                                            title={t("clickToRename")}
                                            onClick={() => {
                                                setEditingPath(path);
                                                setEditValue(leafName);
                                            }}
                                            className="flex-1 min-w-0 text-left text-[11px] text-green-700 dark:text-green-400 truncate hover:underline decoration-dotted underline-offset-2"
                                        >
                                            {leafName}
                                        </button>
                                    )}
                                    {!isEditing && (
                                        <button
                                            type="button"
                                            title={t("rename")}
                                            onClick={() => {
                                                setEditingPath(path);
                                                setEditValue(leafName);
                                            }}
                                            className="opacity-0 group-hover/dir:opacity-100 transition-opacity shrink-0 text-green-500 hover:text-green-700 dark:hover:text-green-300"
                                        >
                                            <Pencil className="h-2.5 w-2.5" />
                                        </button>
                                    )}
                                </div>
                            );
                        })}
                </div>
            )}
        </div>
    );
}
