"use client";

import { useEffect, useState, useMemo, useRef } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import Papa from "papaparse";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";
import { ZoomControls } from "./zoom-controls";
import { useTranslations } from "next-intl";

const MIN_ZOOM = 50;
const MAX_ZOOM = 200;
const ZOOM_STEP = 10;
const MAX_FETCH_BYTES = 10 * 1024 * 1024; // 10 MiB
const PAGE_SIZE = 100;

interface CsvViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

export function CsvViewer({ materialId, fileKey }: CsvViewerProps) {
    const t = useTranslations("Viewers");
    const tableContainerRef = useRef<HTMLDivElement>(null);
    const [headers, setHeaders] = useState<string[]>([]);
    const [rows, setRows] = useState<string[][]>([]);
    const [page, setPage] = useState(0);

    const { content, loading, error, truncated, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "text",
        maxBytes: MAX_FETCH_BYTES,
    });

    const { zoom, zoomIn, zoomOut, resetZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: tableContainerRef,
        handleKeyboard: true,
    });

    useEffect(() => {
        if (!content) {
            setHeaders([]);
            setRows([]);
            setPage(0);
            return;
        }

        const result = Papa.parse<string[]>(content, {
            skipEmptyLines: true,
        });

        if (result.errors.length > 0 && result.data.length === 0) {
            // Handled as error in shell if needed, but here we just show empty
            setHeaders([]);
            setRows([]);
        } else {
            const [headerRow, ...dataRows] = result.data as string[][];
            setHeaders(headerRow ?? []);
            setRows(dataRows);
        }
        setPage(0);
    }, [content]);

    const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const pageRows = useMemo(
        () => rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
        [rows, page],
    );

    const goToPage = (p: number) => {
        setPage(p);
        tableContainerRef.current?.scrollTo({ top: 0 });
    };

    return (
        <ViewerShell
            scrollRef={tableContainerRef}
            loading={loading}
            error={error}
            onRetry={reload}
            truncatedMessage={truncated ? t("csv.truncated", { rows: rows.length.toLocaleString() }) : null}
            toolbarLeft={
                !loading && !error && (
                    <span className="text-xs font-medium text-muted-foreground">
                        {t("csv.stats", { rows: rows.length.toLocaleString(), cols: headers.length })}
                    </span>
                )
            }
            toolbarRight={
                <ZoomControls
                    zoom={zoom}
                    onZoomIn={zoomIn}
                    onZoomOut={zoomOut}
                    onReset={resetZoom}
                    min={MIN_ZOOM}
                    max={MAX_ZOOM}
                    disabled={loading}
                />
            }
        >
            <div className="flex h-full flex-col min-h-0">
                <table className="w-full border-collapse text-sm">
                    <thead className="sticky top-0 z-10 bg-muted">
                        <tr>
                            <th className="border-b border-r px-2 py-1.5 text-right font-mono text-xs text-muted-foreground select-none w-[3rem]">
                                #
                            </th>
                            {headers.map((h, i) => (
                                <th
                                    key={i}
                                    className="border-b border-r px-3 py-1.5 text-left font-medium text-foreground whitespace-nowrap last:border-r-0"
                                >
                                    {h || <span className="text-muted-foreground/50 italic">{t("csv.col", { index: i + 1 })}</span>}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {pageRows.map((row, ri) => {
                            const globalRow = page * PAGE_SIZE + ri + 1;
                            return (
                                <tr
                                    key={ri}
                                    className="border-b last:border-b-0 hover:bg-muted/40 transition-colors"
                                >
                                    <td className="border-r px-2 py-1 text-right font-mono text-xs text-muted-foreground select-none">
                                        {globalRow}
                                    </td>
                                    {headers.map((_, ci) => (
                                        <td
                                            key={ci}
                                            className="border-r px-3 py-1 last:border-r-0 max-w-[300px] truncate"
                                            title={row[ci] ?? ""}
                                        >
                                            {row[ci] ?? ""}
                                        </td>
                                    ))}
                                </tr>
                            );
                        })}
                        {pageRows.length === 0 && !loading && (
                            <tr>
                                <td colSpan={headers.length + 1} className="py-8 text-center text-muted-foreground text-sm">
                                    {t("csv.noData")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            {/* Pagination footer (fixed at bottom of viewer content area) */}
            <div className="sticky bottom-0 left-0 right-0 z-10 flex items-center justify-between border-t bg-muted/90 backdrop-blur-sm px-4 py-2 text-xs text-muted-foreground">
                <span>
                    {t("csv.page", { current: page + 1, total: totalPages })}
                </span>
                {totalPages > 1 && (
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => goToPage(page - 1)}
                            disabled={page === 0}
                            className="rounded p-0.5 hover:bg-muted disabled:opacity-40"
                            aria-label={t("csv.prevPage")}
                        >
                            <ChevronLeft className="h-4 w-4" />
                        </button>
                        <button
                            onClick={() => goToPage(page + 1)}
                            disabled={page >= totalPages - 1}
                            className="rounded p-0.5 hover:bg-muted disabled:opacity-40"
                            aria-label={t("csv.nextPage")}
                        >
                            <ChevronRight className="h-4 w-4" />
                        </button>
                    </div>
                )}
            </div>
        </ViewerShell>
    );
}
