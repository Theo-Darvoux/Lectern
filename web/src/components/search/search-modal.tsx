"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { FolderIcon, FileTextIcon, Loader2, ThumbsUp } from "lucide-react";

import { useSearch, type SearchResult } from "./use-search";
import { 
    CommandDialog,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command";
import { Badge } from "@/components/ui/badge";
import { 
    getFileBadgeColor, 
    getFileBadgeLabel, 
    getFileExtension 
} from "@/lib/file-utils";
import { TYPE_ICONS, EXT_ICONS } from "@/lib/material-icons";
import { useTranslations } from "next-intl";

export function SearchList({ 
    query, 
    onSelect, 
    loading, 
    results 
}: { 
    query: string; 
    onSelect: (result: SearchResult) => void;
    loading: boolean;
    results: SearchResult[];
}) {
    const t = useTranslations("Search");
    return (
        <CommandList>
            <CommandEmpty>
                {loading ? (
                    <div className="flex items-center justify-center p-4">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        <span>{t("searching")}</span>
                    </div>
                ) : (
                    query.trim() === "" ? t("typeCommandOrSearch") : t("noResults")
                )}
            </CommandEmpty>

            {results.length > 0 && (
                <CommandGroup heading={t("results")}>
                    {results.map((result) => {
                        const isDir = result.search_type === "directory";
                        const title = result.title || result.name || "";
                        const extension = getFileExtension(title) || getFileExtension(result.browse_path);
                        
                        let Icon: React.ElementType = isDir ? FolderIcon : FileTextIcon;
                        if (!isDir) {
                            if (result.type && TYPE_ICONS[result.type]) {
                                Icon = TYPE_ICONS[result.type];
                            } else if (extension && EXT_ICONS[extension]) {
                                Icon = EXT_ICONS[extension];
                            }
                        }

                        const badgeLabel = isDir ? null : getFileBadgeLabel(result.file_name || title, result.file_mime_type);
                        const badgeColor = isDir ? "" : getFileBadgeColor(result.file_name || title, result.file_mime_type);

                        return (
                            <CommandItem
                                key={`${result.search_type}-${result.id}`}
                                value={`${title} ${result.id}`}
                                onSelect={() => onSelect(result)}
                                className="flex items-center gap-2 cursor-pointer"
                            >
                                <Icon className={`h-4 w-4 shrink-0 ${isDir ? "text-primary" : "text-blue-500"}`} />
                                <div className="flex flex-col flex-1 min-w-0">
                                    <span className="font-medium truncate">{title}</span>
                                    {result.browse_path && (
                                        <span className="text-[10px] text-muted-foreground truncate opacity-70 font-mono">
                                            {result.browse_path.replace(/^\/browse/, "") || "/"}
                                        </span>
                                    )}
                                </div>
                                <div className="text-xs text-muted-foreground flex items-center gap-2">
                                    {badgeLabel && (
                                        <Badge variant="secondary" className={`${badgeColor} border-none text-[10px] px-1.5 h-4.5 font-bold uppercase tracking-tighter`}>
                                            {badgeLabel}
                                        </Badge>
                                    )}
                                    <span className="flex items-center gap-0.5 ml-1 border-l pl-2">
                                        <ThumbsUp className={`h-3 w-3 ${(result.is_liked) ? "fill-primary text-primary" : ""}`} />
                                        {result.like_count || 0}
                                    </span>
                                </div>
                            </CommandItem>
                        );
                    })}
                </CommandGroup>
            )}
        </CommandList>
    );
}

export function SearchModal({
    open,
    onOpenChange,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    const t = useTranslations("Search");
    const router = useRouter();
    const [query, setQuery] = React.useState("");
    const { results, loading } = useSearch(query);

    const onSelect = (result: SearchResult) => {
        onOpenChange(false);
        if (result.browse_path) {
            router.push(result.browse_path);
        } else {
            if (result.search_type === "directory") {
                router.push(`/directories/${result.id}`);
            } else {
                router.push(`/materials/${result.id}`);
            }
        }
    };

    return (
        <CommandDialog open={open} onOpenChange={onOpenChange} shouldFilter={false}>
            <CommandInput
                placeholder={t("searchMaterialsDirs")}
                value={query}
                onValueChange={setQuery}
            />
            <SearchList 
                query={query}
                onSelect={onSelect}
                loading={loading}
                results={results}
            />
        </CommandDialog>
    );
}
