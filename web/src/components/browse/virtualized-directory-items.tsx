"use client";

import {
    Children,
    useCallback,
    useEffect,
    useState,
    type CSSProperties,
    type ReactNode,
} from "react";
import {
    Grid,
    List,
    useGridCallbackRef,
    useListCallbackRef,
    type CellComponentProps,
    type RowComponentProps,
} from "react-window";

const VIRTUALIZATION_THRESHOLD = 60;
const LIST_ROW_HEIGHT = 72;
const MAX_VIEWPORT_HEIGHT = 720;
const OVERSCAN_ROWS = 3;

export function shouldVirtualizeDirectoryItems(itemCount: number): boolean {
    return itemCount > VIRTUALIZATION_THRESHOLD;
}

export function directoryGridLayout(width: number, itemCount = 0) {
    const columnCount = width >= 1_280 ? 5 : width >= 768 ? 4 : width >= 640 ? 3 : 2;
    const cardWidth = Math.max(1, width / columnCount);
    const rowHeight = Math.ceil(cardWidth * 0.75 + 104);
    const rowCount = Math.ceil(itemCount / columnCount);
    const visibleRows = Math.max(1, Math.floor(MAX_VIEWPORT_HEIGHT / rowHeight));
    const viewportHeight = Math.min(rowCount, visibleRows) * rowHeight;
    return { columnCount, rowCount, rowHeight, viewportHeight };
}

interface VirtualRowData {
    items: readonly ReactNode[];
}

function DirectoryRow({
    ariaAttributes,
    index,
    style,
    items,
}: RowComponentProps<VirtualRowData>) {
    return (
        <div {...ariaAttributes} style={style} className="border-b last:border-b-0">
            {items[index]}
        </div>
    );
}

interface VirtualCellData {
    items: readonly ReactNode[];
    columnCount: number;
}

function DirectoryCell({
    ariaAttributes,
    columnIndex,
    rowIndex,
    style,
    items,
    columnCount,
}: CellComponentProps<VirtualCellData>) {
    const index = rowIndex * columnCount + columnIndex;
    if (index >= items.length) return null;
    return (
        <div
            {...ariaAttributes}
            style={{ ...style, boxSizing: "border-box", padding: 6 }}
        >
            {items[index]}
        </div>
    );
}

interface VirtualizedItemsProps {
    children: ReactNode;
    className?: string;
    focusedIndex: number | null;
}

export function VirtualizedDirectoryList({
    children,
    className,
    focusedIndex,
}: VirtualizedItemsProps) {
    const items = Children.toArray(children);
    const [listApi, setListApi] = useListCallbackRef();

    useEffect(() => {
        if (focusedIndex === null || !shouldVirtualizeDirectoryItems(items.length)) return;
        listApi?.scrollToRow({ index: focusedIndex, align: "smart", behavior: "smooth" });
    }, [focusedIndex, items.length, listApi]);

    if (!shouldVirtualizeDirectoryItems(items.length)) {
        return <div data-tutorial="browse-item" className={className}>{items}</div>;
    }

    const visibleRows = Math.max(1, Math.floor(MAX_VIEWPORT_HEIGHT / LIST_ROW_HEIGHT));
    const viewportHeight = Math.min(items.length, visibleRows) * LIST_ROW_HEIGHT;
    return (
        <List
            rowCount={items.length}
            rowHeight={LIST_ROW_HEIGHT}
            rowComponent={DirectoryRow}
            rowProps={{ items }}
            listRef={setListApi}
            overscanCount={OVERSCAN_ROWS}
            data-tutorial="browse-item"
            className={className}
            style={{ height: viewportHeight }}
        />
    );
}

export function VirtualizedDirectoryGrid({
    children,
    className,
    focusedIndex,
}: VirtualizedItemsProps) {
    const items = Children.toArray(children);
    const [width, setWidth] = useState(900);
    const [gridApi, setGridApi] = useGridCallbackRef();
    const layout = directoryGridLayout(width, items.length);
    const handleResize = useCallback(({ width: nextWidth }: { width: number }) => {
        setWidth((current) => Math.abs(current - nextWidth) > 1 ? nextWidth : current);
    }, [setWidth]);

    useEffect(() => {
        if (focusedIndex === null || !shouldVirtualizeDirectoryItems(items.length)) return;
        gridApi?.scrollToCell({
            rowIndex: Math.floor(focusedIndex / layout.columnCount),
            columnIndex: focusedIndex % layout.columnCount,
            rowAlign: "smart",
            behavior: "smooth",
        });
    }, [focusedIndex, items.length, layout.columnCount, gridApi]);

    if (!shouldVirtualizeDirectoryItems(items.length)) {
        return <div data-tutorial="browse-item" className={className}>{items}</div>;
    }

    const virtualClassName = className
        ?.split(" ")
        .filter((token) => !token.includes("grid-cols-") && token !== "grid" && !token.includes("gap-"))
        .join(" ");
    return (
        <Grid
            cellComponent={DirectoryCell}
            cellProps={{ items, columnCount: layout.columnCount }}
            columnCount={layout.columnCount}
            columnWidth={`${100 / layout.columnCount}%`}
            rowCount={layout.rowCount}
            rowHeight={layout.rowHeight}
            gridRef={setGridApi}
            onResize={handleResize}
            overscanCount={OVERSCAN_ROWS}
            defaultWidth={width}
            defaultHeight={layout.viewportHeight}
            data-tutorial="browse-item"
            className={virtualClassName}
            style={{ width: "100%", height: layout.viewportHeight } as CSSProperties}
        />
    );
}
