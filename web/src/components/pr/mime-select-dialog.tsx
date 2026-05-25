"use client";

import { useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useTranslations } from "next-intl";
import type { ScannedFile } from "@/lib/drop-utils";
import { formatFileSize } from "@/lib/file-utils";

const TEXT_MIME_OPTIONS = [
    { value: "text/plain", label: "Plain text" },
    { value: "text/markdown", label: "Markdown" },
    { value: "text/csv", label: "CSV" },
    { value: "text/html", label: "HTML" },
    { value: "text/css", label: "CSS" },
    { value: "text/javascript", label: "JavaScript" },
    { value: "application/typescript", label: "TypeScript" },
    { value: "application/json", label: "JSON" },
    { value: "application/xml", label: "XML" },
    { value: "application/x-yaml", label: "YAML" },
    { value: "application/toml", label: "TOML" },
    { value: "application/sql", label: "SQL" },
    { value: "application/graphql", label: "GraphQL" },
    { value: "application/x-tex", label: "TeX / LaTeX" },
    { value: "text/x-python", label: "Python" },
    { value: "text/x-csrc", label: "C" },
    { value: "text/x-c++src", label: "C++" },
    { value: "text/x-java-source", label: "Java" },
    { value: "text/x-kotlin", label: "Kotlin" },
    { value: "text/x-scala", label: "Scala" },
    { value: "text/x-rust", label: "Rust" },
    { value: "text/x-go", label: "Go" },
    { value: "text/x-ruby", label: "Ruby" },
    { value: "text/x-php", label: "PHP" },
    { value: "text/x-shellscript", label: "Shell script" },
    { value: "text/x-lua", label: "Lua" },
    { value: "text/x-r", label: "R" },
    { value: "text/x-haskell", label: "Haskell" },
    { value: "text/x-elixir", label: "Elixir" },
    { value: "text/x-erlang", label: "Erlang" },
    { value: "text/x-clojure", label: "Clojure" },
    { value: "text/x-julia", label: "Julia" },
    { value: "text/x-dart", label: "Dart" },
    { value: "text/x-nim", label: "Nim" },
    { value: "text/x-zig", label: "Zig" },
    { value: "text/x-powershell", label: "PowerShell" },
    { value: "text/x-diff", label: "Diff / Patch" },
    { value: "text/x-protobuf", label: "Protobuf" },
    { value: "text/x-nix", label: "Nix" },
];

interface MimeSelectDialogProps {
    files: ScannedFile[];
    onConfirm: (selections: Array<{ scanned: ScannedFile; mime: string }>) => void;
    onDismiss: () => void;
}

export function MimeSelectDialog({ files, onConfirm, onDismiss }: MimeSelectDialogProps) {
    const t = useTranslations("Upload");
    const [selections, setSelections] = useState<Record<string, string>>(() =>
        Object.fromEntries(files.map(s => [s.file.name + s.file.size, "text/plain"]))
    );

    if (files.length === 0) return null;

    const handleConfirm = () => {
        const result = files.map(s => ({
            scanned: s,
            mime: selections[s.file.name + s.file.size] ?? "text/plain",
        }));
        onConfirm(result);
    };

    return (
        <Dialog open onOpenChange={open => { if (!open) onDismiss(); }}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>{t("mimeSelectTitle")}</DialogTitle>
                    <DialogDescription>{t("mimeSelectDesc")}</DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-3 py-2 max-h-72 overflow-y-auto">
                    {files.map(s => {
                        const key = s.file.name + s.file.size;
                        return (
                            <div key={key} className="flex items-center gap-3">
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{s.file.name}</p>
                                    <p className="text-xs text-muted-foreground">{formatFileSize(s.file.size)}</p>
                                </div>
                                <Select
                                    value={selections[key]}
                                    onValueChange={v => setSelections(prev => ({ ...prev, [key]: v }))}
                                >
                                    <SelectTrigger className="w-44 shrink-0">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {TEXT_MIME_OPTIONS.map(opt => (
                                            <SelectItem key={opt.value} value={opt.value}>
                                                {opt.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        );
                    })}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={onDismiss}>
                        {t("mimeSelectSkip")}
                    </Button>
                    <Button onClick={handleConfirm}>
                        {t("mimeSelectConfirm")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
