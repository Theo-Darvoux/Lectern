"use client";

import React, { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { Plus, Trash2, Copy, Check, Bold, Italic } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { AVAILABLE_FONTS, parseSegments, type NameSegment } from "@/lib/fonts";
import { SiteName } from "@/components/site-name";

interface WordmarkBuilderProps {
    siteName: string;
    siteNameStyle: string | null;
}

export function WordmarkBuilder({ siteName, siteNameStyle }: WordmarkBuilderProps) {
    const t = useTranslations("Admin.Config.Branding.visualIdentity.wordmarkBuilder");

    // Initialize segments state
    const [segments, setSegments] = useState<NameSegment[]>(() => {
        const parsed = parseSegments(siteNameStyle);
        if (parsed && parsed.length > 0) {
            return parsed;
        }
        // Fallback: split name into a single starting segment
        return [
            {
                text: siteName || "Lectern",
                font: "Inter",
                color: "",
                bold: true,
                italic: false,
            },
        ];
    });

    const [copied, setCopied] = useState(false);

    // Auto-update starting segment text if the main siteName changes and there is only 1 segment
    useEffect(() => {
        if (segments.length === 1 && segments[0].text === "" && siteName) {
            setSegments([{ ...segments[0], text: siteName }]);
        }
    }, [siteName]);

    const handleAddSegment = () => {
        setSegments([
            ...segments,
            {
                text: "",
                font: "Inter",
                color: "",
                bold: false,
                italic: false,
            },
        ]);
    };

    const handleRemoveSegment = (index: number) => {
        if (segments.length <= 1) return;
        const next = [...segments];
        next.splice(index, 1);
        setSegments(next);
    };

    const handleUpdateSegment = <K extends keyof NameSegment>(
        index: number,
        key: K,
        value: NameSegment[K]
    ) => {
        const next = [...segments];
        next[index] = {
            ...next[index],
            [key]: value,
        };
        setSegments(next);
    };

    // Construct the final .env value
    // We filter out empty segments or format color keys appropriately (null if empty)
    const processedSegments = segments
        // Drop blank rows so they don't leak empty <span>s into the saved value.
        // (The editing list keeps them so you can still type into a new segment.)
        .filter((seg) => seg.text.trim() !== "")
        .map((seg) => ({
            text: seg.text,
            font: seg.font || "Inter",
            color: seg.color && seg.color.trim() !== "" ? seg.color.trim() : null,
            bold: !!seg.bold,
            italic: !!seg.italic,
        }));

    // No surrounding quotes: both Docker Compose v2 and pydantic-settings read the
    // raw value as-is (JSON has no spaces/comments to escape), and omitting quotes
    // avoids a collision when a segment's text contains a single quote (e.g. "d'X").
    const envValue = `SITE_NAME_STYLE=${JSON.stringify(processedSegments)}`;

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(envValue);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error("Failed to copy code", err);
        }
    };

    return (
        <Card className="border border-border/80 shadow-md">
            <CardHeader>
                <CardTitle className="text-base font-bold flex items-center gap-2">
                    {t("title")}
                </CardTitle>
                <CardDescription>
                    {t("description")}
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Live Preview Canvas */}
                <div className="space-y-2">
                    <Label className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                        {t("preview")}
                    </Label>
                    <div className="relative overflow-hidden rounded-lg border bg-muted/20 p-8 flex items-center justify-center min-h-[120px] shadow-inner dark:bg-zinc-950/40">
                        {/* Decorative designer grid background */}
                        <div className="absolute inset-0 bg-[linear-gradient(to_right,#80808008_1px,transparent_1px),linear-gradient(to_bottom,#80808008_1px,transparent_1px)] bg-[size:16px_16px] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_50%,#000_70%,transparent_100%)]" />
                        <span className="text-4xl font-extrabold tracking-tight relative z-10 select-none">
                            <SiteName name={siteName} style={JSON.stringify(processedSegments)} />
                        </span>
                    </div>
                </div>

                {/* Segments Configuration List */}
                <div className="space-y-4">
                    {segments.map((seg, index) => (
                        <div
                            key={index}
                            className="p-4 rounded-lg border bg-card/50 flex flex-col md:flex-row md:items-end gap-3 transition-colors hover:border-muted-foreground/30 relative group"
                        >
                            {/* Segment Number Badge */}
                            <span className="absolute -top-2.5 -left-2.5 flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] font-bold text-muted-foreground border">
                                {index + 1}
                            </span>

                            {/* Text Input */}
                            <div className="flex-1 space-y-1.5">
                                <Label htmlFor={`seg-text-${index}`} className="text-xs">
                                    {t("text")}
                                </Label>
                                <Input
                                    id={`seg-text-${index}`}
                                    value={seg.text}
                                    placeholder={t("placeholderText")}
                                    onChange={(e) => handleUpdateSegment(index, "text", e.target.value)}
                                    className="h-9"
                                />
                            </div>

                            {/* Font Selector */}
                            <div className="w-full md:w-[180px] space-y-1.5">
                                <Label className="text-xs">{t("font")}</Label>
                                <Select
                                    value={seg.font || "Inter"}
                                    onValueChange={(val) => handleUpdateSegment(index, "font", val)}
                                >
                                    <SelectTrigger className="h-9">
                                        <SelectValue placeholder="Font" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {AVAILABLE_FONTS.map((font) => (
                                            <SelectItem key={font.name} value={font.name}>
                                                <span style={{ fontFamily: `'${font.name}', sans-serif` }}>
                                                    {font.name}
                                                </span>
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            {/* Color Selector */}
                            <div className="w-full md:w-[150px] space-y-1.5">
                                <Label htmlFor={`seg-color-${index}`} className="text-xs">
                                    {t("color")}
                                </Label>
                                <div className="flex gap-1.5 items-center">
                                    {/* Inline Color Picker */}
                                    <input
                                        type="color"
                                        value={seg.color && seg.color.startsWith("#") ? seg.color : "#ffffff"}
                                        onChange={(e) => handleUpdateSegment(index, "color", e.target.value)}
                                        className="h-9 w-9 p-0 rounded-md border cursor-pointer bg-transparent shrink-0"
                                    />
                                    <Input
                                        id={`seg-color-${index}`}
                                        value={seg.color || ""}
                                        placeholder={t("placeholderColor")}
                                        onChange={(e) => handleUpdateSegment(index, "color", e.target.value)}
                                        className="h-9 font-mono text-xs uppercase"
                                    />
                                </div>
                            </div>

                            {/* Formatting Toolbar */}
                            <div className="flex gap-1.5 items-center h-9 md:mb-0 mb-2">
                                <Button
                                    type="button"
                                    variant={seg.bold ? "default" : "outline"}
                                    size="icon"
                                    onClick={() => handleUpdateSegment(index, "bold", !seg.bold)}
                                    className="h-9 w-9 shrink-0"
                                    title={t("bold")}
                                >
                                    <Bold className="h-4 w-4" />
                                </Button>
                                <Button
                                    type="button"
                                    variant={seg.italic ? "default" : "outline"}
                                    size="icon"
                                    onClick={() => handleUpdateSegment(index, "italic", !seg.italic)}
                                    className="h-9 w-9 shrink-0"
                                    title={t("italic")}
                                >
                                    <Italic className="h-4 w-4" />
                                </Button>
                            </div>

                            {/* Remove button */}
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                disabled={segments.length <= 1}
                                onClick={() => handleRemoveSegment(index)}
                                className="h-9 w-9 text-muted-foreground hover:text-destructive shrink-0 md:mb-0 mb-2"
                                title={t("delete")}
                            >
                                <Trash2 className="h-4 w-4" />
                            </Button>
                        </div>
                    ))}

                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={handleAddSegment}
                        className="w-full py-5 border-dashed border-2 hover:border-primary/50 hover:bg-primary/5 flex items-center justify-center gap-1.5"
                    >
                        <Plus className="h-4 w-4" />
                        {t("addSegment")}
                    </Button>
                </div>

                {/* Env Output Code & Instructions */}
                <div className="pt-4 border-t space-y-4">
                    <div className="space-y-1.5">
                        <Label className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                            {t("generatedConfig")}
                        </Label>
                        <div className="flex gap-2">
                            <Input
                                readOnly
                                value={envValue}
                                className="font-mono text-xs bg-muted/40 cursor-text select-all"
                            />
                            <Button
                                type="button"
                                variant={copied ? "secondary" : "default"}
                                onClick={handleCopy}
                                className="shrink-0 h-9"
                            >
                                {copied ? (
                                    <>
                                        <Check className="h-4 w-4 mr-1.5" />
                                        {t("copiedBtn")}
                                    </>
                                ) : (
                                    <>
                                        <Copy className="h-4 w-4 mr-1.5" />
                                        {t("copyBtn")}
                                    </>
                                )}
                            </Button>
                        </div>
                    </div>

                    <div className="rounded-lg bg-muted/30 p-4 border border-border/60 text-xs text-muted-foreground space-y-2">
                        <p className="font-semibold text-foreground">{t("instructionsTitle")}</p>
                        <ol className="list-decimal list-inside space-y-1 pl-1">
                            <li>{t("instructionsStep1")}</li>
                            <li>{t("instructionsStep2")}</li>
                            <li>{t("instructionsStep3")}</li>
                        </ol>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
