"use client";

import { useMemo, useState } from "react";
import {
    Settings2, HardDrive, FileCode, Sliders,
    Info, Image as ImageIcon, FileText, Code2, RefreshCw,
    Search, Shield
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { TabsContent } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Checkbox } from "@/components/ui/checkbox";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { useTranslations } from "next-intl";

interface AuthConfig {
    max_file_size_mb: number;
    max_image_size_mb: number;
    max_audio_size_mb: number;
    max_video_size_mb: number;
    max_document_size_mb: number;
    max_office_size_mb: number;
    max_text_size_mb: number;
    pdf_quality: number | null;
    video_compression_profile: string | null;
    thumbnail_quality: number | null;
    thumbnail_size_px: number | null;
    allowed_extensions: string | null;
    allowed_mime_types: string | null;
}

interface FilesConfigTabProps {
    config: AuthConfig;
}

interface FileFormat {
    id: string;
    label: string;
    extensions: string[];
    mimes: string[];
}

interface FileGroup {
    name: string;
    icon: any;
    formats: FileFormat[];
}

const FILE_GROUPS: FileGroup[] = [
    {
        name: "Images",
        icon: ImageIcon,
        formats: [
            { id: "jpeg", label: "JPEG / JPG", extensions: [".jpg", ".jpeg"], mimes: ["image/jpeg"] },
            { id: "png", label: "PNG", extensions: [".png"], mimes: ["image/png"] },
            { id: "webp", label: "WebP", extensions: [".webp"], mimes: ["image/webp"] },
            { id: "gif", label: "GIF", extensions: [".gif"], mimes: ["image/gif"] },
            { id: "svg", label: "SVG", extensions: [".svg"], mimes: ["image/svg+xml"] },
        ]
    },
    {
        name: "Documents",
        icon: FileText,
        formats: [
            { id: "pdf", label: "PDF", extensions: [".pdf"], mimes: ["application/pdf"] },
            { id: "epub", label: "ePUB", extensions: [".epub"], mimes: ["application/epub+zip"] },
            { id: "djvu", label: "DjVu", extensions: [".djvu", ".djv"], mimes: ["image/vnd.djvu", "image/x-djvu"] },
        ]
    },
    {
        name: "Office",
        icon: FileText,
        formats: [
            { id: "word", label: "Word (.docx, .doc)", extensions: [".docx", ".doc"], mimes: ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"] },
            { id: "excel", label: "Excel (.xlsx, .xls)", extensions: [".xlsx", ".xls"], mimes: ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"] },
            { id: "powerpoint", label: "PowerPoint (.pptx, .ppt)", extensions: [".pptx", ".ppt"], mimes: ["application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.ms-powerpoint"] },
            { id: "odt", label: "OpenDocument Text (.odt)", extensions: [".odt"], mimes: ["application/vnd.oasis.opendocument.text"] },
            { id: "ods", label: "OpenDocument Sheet (.ods)", extensions: [".ods"], mimes: ["application/vnd.oasis.opendocument.spreadsheet"] },
        ]
    },
    {
        name: "Code & Development",
        icon: Code2,
        formats: [
            { id: "markdown", label: "Markdown (.md, .markdown)", extensions: [".md", ".markdown"], mimes: ["text/markdown", "text/x-markdown"] },
            { id: "python", label: "Python (.py)", extensions: [".py", ".pyw", ".pyi"], mimes: ["text/x-python", "application/x-python"] },
            { id: "javascript", label: "JS / TS", extensions: [".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"], mimes: ["text/javascript", "application/javascript", "application/typescript", "text/typescript"] },
            { id: "web", label: "Web (HTML, CSS)", extensions: [".html", ".htm", ".css", ".scss", ".sass"], mimes: ["text/html", "text/css"] },
            { id: "c_cpp", label: "C / C++", extensions: [".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx"], mimes: ["text/x-c", "text/x-csrc", "text/x-chdr", "text/x-c++", "text/x-c++src", "text/x-c++hdr"] },
            { id: "rust_go", label: "Rust & Go", extensions: [".rs", ".go"], mimes: ["text/x-rust", "text/x-go"] },
            { id: "java_jvm", label: "Java / Kotlin / JVM", extensions: [".java", ".kt", ".kts", ".scala", ".groovy"], mimes: ["text/x-java-source", "text/x-java", "text/x-kotlin", "text/x-scala"] },
            { id: "shell", label: "Shell Scripts", extensions: [".sh", ".bash", ".zsh", ".ps1"], mimes: ["text/x-shellscript", "application/x-sh", "application/x-bash", "text/x-powershell"] },
            { id: "data", label: "Data (JSON, XML, YAML, SQL)", extensions: [".json", ".json5", ".xml", ".yaml", ".yml", ".toml", ".sql"], mimes: ["application/json", "application/xml", "text/xml", "application/x-yaml", "text/yaml", "application/toml", "application/sql", "text/x-sql"] },
            { id: "latex", label: "TeX / LaTeX", extensions: [".tex", ".latex", ".sty", ".cls", ".bib"], mimes: ["application/x-tex", "text/x-tex"] },
        ]
    },
    {
        name: "Audio & Video",
        icon: RefreshCw,
        formats: [
            { id: "mp4", label: "MP4 Video", extensions: [".mp4"], mimes: ["video/mp4"] },
            { id: "webm", label: "WebM Video", extensions: [".webm"], mimes: ["video/webm"] },
            { id: "mp3", label: "MP3 Audio", extensions: [".mp3"], mimes: ["audio/mpeg", "audio/mp3"] },
            { id: "wav", label: "WAV Audio", extensions: [".wav"], mimes: ["audio/wav"] },
            { id: "ogg", label: "OGG (Audio/Video)", extensions: [".ogg"], mimes: ["audio/ogg", "video/ogg"] },
            { id: "flac", label: "FLAC Audio", extensions: [".flac"], mimes: ["audio/flac"] },
            { id: "aac", label: "AAC Audio", extensions: [".aac", ".m4a"], mimes: ["audio/aac", "audio/mp4"] },
        ]
    }
];

function ReadOnlySlider({
    label,
    value,
    min = 1,
    max = 100,
    suffix = "",
    tooltip,
}: {
    label: string;
    value: number | null;
    min?: number;
    max?: number;
    suffix?: string;
    tooltip?: string;
}) {
    return (
        <div className="space-y-3 p-4 rounded-xl bg-muted/30 border border-muted/50">
            <div className="flex justify-between items-center">
                <div className="flex items-center gap-2">
                    <Label className="text-sm font-semibold">{label}</Label>
                    {tooltip && (
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-[200px]">
                                    {tooltip}
                                </TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    )}
                </div>
                <span className="text-xs font-mono font-bold text-primary bg-primary/10 px-2.5 py-1 rounded-full shadow-sm">
                    {value ?? min}{suffix}
                </span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                value={value ?? min}
                readOnly
                disabled
                className="w-full h-1.5 bg-secondary rounded-lg appearance-none accent-primary"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground font-medium px-0.5">
                <span>{min}{suffix}</span>
                <span>{max}{suffix}</span>
            </div>
        </div>
    );
}

export function FilesConfigTab({ config }: FilesConfigTabProps) {
    const t = useTranslations("Admin.Config.Files");
    const [searchQuery, setSearchQuery] = useState("");

    const currentExtensions = useMemo(() =>
        new Set(config.allowed_extensions?.split(",").map(s => s.trim().toLowerCase()).filter(Boolean) || []),
    [config.allowed_extensions]);

    const filteredGroups = useMemo(() => {
        if (!searchQuery) return FILE_GROUPS;
        return FILE_GROUPS.map(group => ({
            ...group,
            formats: group.formats.filter(f =>
                f.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
                f.extensions.some(e => e.toLowerCase().includes(searchQuery.toLowerCase()))
            )
        })).filter(group => group.formats.length > 0);
    }, [searchQuery]);

    const isFormatActive = (format: FileFormat) =>
        format.extensions.every(e => currentExtensions.has(e.toLowerCase()));

    return (
        <TabsContent value="files" className="mt-6 space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
            {/* File Size Limits */}
            <Card className="overflow-hidden border-none shadow-xl bg-card/50 backdrop-blur-sm">
                <CardHeader className="bg-gradient-to-r from-primary/5 to-transparent border-b pb-6">
                    <div className="flex items-center gap-3">
                        <div className="p-2.5 bg-primary/10 rounded-xl">
                            <Settings2 className="h-5 w-5 text-primary" />
                        </div>
                        <div>
                            <CardTitle className="text-xl">{t("limits.title")}</CardTitle>
                            <CardDescription>
                                {t("limits.description")}
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-8">
                    <div className="grid gap-8 md:grid-cols-2 lg:grid-cols-4">
                        {[
                            { id: "max_file_size_mb", label: t("limits.global"), icon: HardDrive, color: "text-blue-500" },
                            { id: "max_image_size_mb", label: t("limits.images"), icon: ImageIcon, color: "text-purple-500" },
                            { id: "max_video_size_mb", label: t("limits.video"), icon: FileCode, color: "text-red-500" },
                            { id: "max_audio_size_mb", label: t("limits.audio"), icon: RefreshCw, color: "text-amber-500" },
                            { id: "max_document_size_mb", label: t("limits.document"), icon: FileText, color: "text-emerald-500" },
                            { id: "max_office_size_mb", label: t("limits.office"), icon: FileText, color: "text-orange-500" },
                            { id: "max_text_size_mb", label: t("limits.text"), icon: Code2, color: "text-indigo-500" },
                        ].map((item) => (
                            <div key={item.id} className="group space-y-3 p-4 rounded-xl bg-muted/20 border border-transparent">
                                <Label htmlFor={item.id} className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                    <item.icon className={`h-3.5 w-3.5 ${item.color}`} />
                                    {item.label}
                                </Label>
                                <div className="relative">
                                    <Input
                                        id={item.id}
                                        type="number"
                                        readOnly
                                        className="h-11 pl-4 pr-12 font-mono text-lg bg-muted/30 border-muted-foreground/20 rounded-lg cursor-default"
                                        value={config[item.id as keyof AuthConfig] ?? ""}
                                    />
                                    <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs font-bold text-muted-foreground/60">MB</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>

            {/* Processing & Compression */}
            <Card className="overflow-hidden border-none shadow-xl bg-card/50 backdrop-blur-sm">
                <CardHeader className="bg-gradient-to-r from-primary/5 to-transparent border-b pb-6">
                    <div className="flex items-center gap-3">
                        <div className="p-2.5 bg-primary/10 rounded-xl">
                            <Sliders className="h-5 w-5 text-primary" />
                        </div>
                        <div>
                            <CardTitle className="text-xl">{t("processing.title")}</CardTitle>
                            <CardDescription>
                                {t("processing.description")}
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-8 space-y-10">
                    <div className="grid gap-8 md:grid-cols-2">
                        <ReadOnlySlider
                            label={t("processing.pdfQuality")}
                            value={config.pdf_quality ?? 80}
                            tooltip={t("processing.pdfQualityTooltip")}
                            suffix="%"
                        />

                        <div className="space-y-3 p-4 rounded-xl bg-muted/30 border border-muted/50">
                            <div className="flex items-center gap-2">
                                <Label htmlFor="video_compression" className="text-sm font-semibold">
                                    {t("processing.videoProfile")}
                                </Label>
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                                        </TooltipTrigger>
                                        <TooltipContent className="max-w-[250px]">
                                            {t("processing.videoProfileTooltip")}
                                        </TooltipContent>
                                    </Tooltip>
                                </TooltipProvider>
                            </div>
                            <select
                                id="video_compression"
                                disabled
                                className="w-full h-11 rounded-lg border border-muted-foreground/20 bg-muted/30 px-4 py-2 text-sm appearance-none cursor-default"
                                value={config.video_compression_profile || "balanced"}
                            >
                                <option value="fast">{t("processing.videoProfiles.fast")}</option>
                                <option value="balanced">{t("processing.videoProfiles.balanced")}</option>
                                <option value="thorough">{t("processing.videoProfiles.thorough")}</option>
                            </select>
                        </div>

                        <ReadOnlySlider
                            label={t("processing.thumbnailQuality")}
                            value={config.thumbnail_quality ?? 80}
                            tooltip={t("processing.thumbnailQualityTooltip")}
                            suffix="%"
                        />

                        <ReadOnlySlider
                            label={t("processing.thumbnailSize")}
                            min={100}
                            max={1280}
                            value={config.thumbnail_size_px ?? 400}
                            tooltip={t("processing.thumbnailSizeTooltip")}
                            suffix="px"
                        />
                    </div>
                </CardContent>
            </Card>

            {/* Whitelisting */}
            <Card className="overflow-hidden border-none shadow-xl bg-card/50 backdrop-blur-sm">
                <CardHeader className="bg-gradient-to-r from-primary/5 to-transparent border-b pb-6">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2.5 bg-primary/10 rounded-xl">
                                <Shield className="h-5 w-5 text-primary" />
                            </div>
                            <div>
                                <CardTitle className="text-xl">{t("whitelist.title")}</CardTitle>
                                <CardDescription>
                                    {t("whitelist.description")}
                                </CardDescription>
                            </div>
                        </div>
                        <div className="relative w-64">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder={t("whitelist.search")}
                                className="pl-9 h-10 bg-background/50 border-muted-foreground/20"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <Accordion type="multiple" className="px-8 py-4" defaultValue={["Images", "Documents"]}>
                        {filteredGroups.map((group) => (
                            <AccordionItem key={group.name} value={group.name} className="border-muted-foreground/10">
                                <AccordionTrigger className="hover:no-underline py-6">
                                    <div className="flex items-center gap-4 w-full text-left">
                                        <div className="p-2 bg-muted rounded-lg">
                                            <group.icon className="h-4 w-4 text-muted-foreground" />
                                        </div>
                                        <span className="font-bold text-base">{t(`whitelist.groups.${group.name}` as any)}</span>
                                        <div className="ml-auto mr-4">
                                            <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                                                {group.formats.filter(f => isFormatActive(f)).length} / {group.formats.length}
                                            </span>
                                        </div>
                                    </div>
                                </AccordionTrigger>
                                <AccordionContent className="pb-8">
                                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-12 gap-y-4 pt-2 px-1">
                                        {group.formats.map((format) => (
                                            <div key={format.id} className="flex items-center space-x-3 py-1">
                                                <Checkbox
                                                    id={format.id}
                                                    checked={isFormatActive(format)}
                                                    disabled
                                                    aria-readonly="true"
                                                />
                                                <div className="grid gap-1.5 leading-none">
                                                    <label
                                                        htmlFor={format.id}
                                                        className="text-sm font-medium leading-none"
                                                    >
                                                        {format.label}
                                                    </label>
                                                    <p className="text-[10px] text-muted-foreground font-mono">
                                                        {format.extensions.join(", ")}
                                                    </p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </AccordionContent>
                            </AccordionItem>
                        ))}
                    </Accordion>

                    <div className="p-8 border-t border-muted/50 bg-muted/10 space-y-4">
                        <div className="flex items-center gap-2">
                            <Label className="text-sm font-bold uppercase tracking-wider text-muted-foreground">{t("whitelist.overrides")}</Label>
                            <TooltipProvider>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                                    </TooltipTrigger>
                                    <TooltipContent>
                                        {t("whitelist.overridesTooltip")}
                                    </TooltipContent>
                                </Tooltip>
                            </TooltipProvider>
                        </div>

                        <div className="grid gap-6 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label className="text-[10px] font-bold uppercase text-muted-foreground/60">{t("whitelist.extensions")}</Label>
                                <div className="min-h-[2.5rem] rounded-md border bg-muted/30 px-3 py-2 text-sm font-mono break-all">
                                    {config.allowed_extensions || <span className="text-muted-foreground italic">—</span>}
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label className="text-[10px] font-bold uppercase text-muted-foreground/60">{t("whitelist.mimes")}</Label>
                                <div className="min-h-[2.5rem] rounded-md border bg-muted/30 px-3 py-2 text-sm font-mono break-all">
                                    {config.allowed_mime_types || <span className="text-muted-foreground italic">—</span>}
                                </div>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </TabsContent>
    );
}
