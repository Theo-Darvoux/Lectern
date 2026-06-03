"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { useSearch } from "./use-search";
import { SearchList } from "./search-modal";
import { Command } from "@/components/ui/command";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

export function SearchInline({ className }: { className?: string } = {}) {
    const t = useTranslations("Search");
    const router = useRouter();
    const [open, setOpen] = React.useState(false);
    const [query, setQuery] = React.useState("");
    const { results, loading } = useSearch(query);
    const inputRef = React.useRef<HTMLInputElement>(null);

    const onSelect = (result: {
        browse_path?: string;
        search_type?: string;
        id?: string;
    }) => {
        setOpen(false);
        setQuery("");
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

    // Shortcut to focus input
    React.useEffect(() => {
        const down = (e: KeyboardEvent) => {
            if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                inputRef.current?.focus();
            }
        };
        document.addEventListener("keydown", down);
        return () => document.removeEventListener("keydown", down);
    }, []);

    return (
        <div className={cn("relative w-full max-w-md pointer-events-auto", className)}>
            <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                    <div className="relative group">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors group-focus-within:text-primary z-10" />
                        <Input
                            ref={inputRef}
                            placeholder={t("searchMaterials")}
                            className="w-full pl-9 h-9 bg-white/50 dark:bg-black/20 hover:bg-white/80 dark:hover:bg-black/40 backdrop-blur-md rounded-xl border-white/20 dark:border-white/10 shadow-sm transition-all focus:ring-2 focus:ring-primary/20 focus:bg-white dark:focus:bg-black pr-12"
                            value={query}
                            onChange={(e) => {
                                setQuery(e.target.value);
                                if (!open) setOpen(true);
                            }}
                            onFocus={() => {
                                if (query.length > 0) setOpen(true);
                            }}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" && results.length > 0) {
                                    onSelect(results[0]);
                                }
                                if (e.key === "Escape") {
                                    setOpen(false);
                                }
                            }}
                        />
                        <div className="absolute right-3 top-1/2 -translate-y-1/2 hidden sm:flex items-center gap-1 pointer-events-none">
                            <kbd className="h-5 select-none items-center gap-1 rounded bg-muted/80 px-1.5 font-mono text-[10px] font-medium opacity-60 flex border shadow-sm">
                                <span className="text-xs">⌘</span>K
                            </kbd>
                        </div>
                    </div>
                </PopoverTrigger>
                <PopoverContent 
                    className="p-0 w-[var(--radix-popover-trigger-width)] overflow-hidden shadow-2xl border-white/20 dark:border-white/10 rounded-xl mt-1" 
                    align="start"
                    onOpenAutoFocus={(e) => e.preventDefault()}
                >
                    <Command shouldFilter={false} className="h-auto">
                        <SearchList 
                            query={query}
                            onSelect={onSelect}
                            loading={loading}
                            results={results}
                        />
                    </Command>
                </PopoverContent>
            </Popover>
        </div>
    );
}
