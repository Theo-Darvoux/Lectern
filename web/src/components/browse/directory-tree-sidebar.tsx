"use client";

import {
  createElement,
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ArrowRight,
  ChevronRight,
  File,
  Folder,
  FolderOpen,
  Home,
  ListChecks,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from "lucide-react";

import { apiFetch, apiFetchRetry } from "@/lib/api-client";

// Tree payloads are small; bound each request so a stalled connection can't
// leave a node spinner (loadingIds) or the root spinner stuck until reload.
const TREE_TIMEOUT_MS = 15_000;
import { cn } from "@/lib/utils";
import { useBrowseRefreshStore, useUIStore } from "@/lib/stores";
import { createSSEConnection, SSEConnection } from "@/lib/sse-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslations } from "next-intl";
import { EXT_ICONS, TYPE_ICONS } from "@/lib/material-icons";
import {
  getFileExtension,
  getFileIconColor,
  MATERIAL_TYPE_ICON_COLORS,
  MIME_QCM,
} from "@/lib/file-utils";

interface DirNode {
  id: string;
  name: string;
  slug: string;
  full_path?: string;
  child_directory_count?: number;
  child_material_count?: number;
  parent_id?: string | null;
}

interface MaterialNode {
  id: string;
  title: string;
  slug: string;
  type: string;
  file_name?: string;
  file_mime_type?: string;
}

interface ChildrenPayload {
  directories: DirNode[];
  materials: MaterialNode[];
}

function normalizeMaterials(raw: unknown[]): MaterialNode[] {
  const out: MaterialNode[] = [];
  for (const m of raw) {
    if (!m || typeof m !== "object") continue;
    const r = m as Record<string, unknown>;
    const ver = (r.current_version_info as Record<string, unknown> | undefined) ?? {};
    out.push({
      id: String(r.id ?? ""),
      title: String(r.title ?? ""),
      slug: String(r.slug ?? ""),
      type: String(r.type ?? "other"),
      file_name: ver.file_name ? String(ver.file_name) : undefined,
      file_mime_type: ver.file_mime_type ? String(ver.file_mime_type) : undefined,
    });
  }
  return out;
}

function pickMaterialIcon(mat: MaterialNode): React.ElementType {
  if (mat.type === "document") {
    const ext = mat.file_name ? getFileExtension(mat.file_name) : "";
    if (ext && EXT_ICONS[ext]) return EXT_ICONS[ext];
    if (mat.file_mime_type === MIME_QCM) return ListChecks;
  }
  return TYPE_ICONS[mat.type] ?? File;
}

function pickMaterialIconColor(mat: MaterialNode): string {
  if (mat.type === "document") {
    return getFileIconColor(mat.file_name ?? "", mat.file_mime_type);
  }
  return MATERIAL_TYPE_ICON_COLORS[mat.type] ?? "text-muted-foreground";
}

// Module-level caches survive component remounts so the tree feels instant
// when the user navigates around the browse page.
const rootsCache: { dirs: DirNode[] | null; mats: MaterialNode[] } = {
  dirs: null,
  mats: [],
};
const childrenCache = new Map<string, ChildrenPayload>();
// Expanded node ids also survive remounts, so navigating between directories
// keeps previously-opened folders open instead of collapsing everything except
// the path that was just navigated to.
let expandedCache = new Set<string>();

function clearTreeCaches() {
  rootsCache.dirs = null;
  rootsCache.mats = [];
  childrenCache.clear();
}

function buildDirHref(fullPath?: string): string {
  if (!fullPath) return "/browse";
  return `/browse/${fullPath}`;
}

function buildMaterialHref(parentPath: string, slug: string): string {
  const base = parentPath ? `/browse/${parentPath}` : "/browse";
  return `${base}/${slug}`;
}

interface MaterialLeafProps {
  material: MaterialNode;
  depth: number;
  parentPath: string;
  activeId: string | null;
  onActivate: (id: string) => void;
}

const MaterialLeaf = memo(function MaterialLeaf({
  material,
  depth,
  parentPath,
  activeId,
  onActivate,
}: MaterialLeafProps) {
  const isActive = activeId === material.id;
  const title = material.title || "Untitled";

  return (
    <li>
      <Link
        href={buildMaterialHref(parentPath, material.slug)}
        onClick={() => onActivate(material.id)}
        className={cn(
          "group/leaf flex items-center gap-1.5 rounded-md pr-1 min-w-0",
          "text-[13px] leading-tight transition-colors py-1.5",
          "outline-none focus-visible:ring-1 focus-visible:ring-ring",
          isActive
            ? "bg-accent text-accent-foreground"
            : "hover:bg-accent/50 text-foreground/90",
        )}
        title={title}
      >
        {/* Reserve indent + chevron slot so the icon aligns with sibling folders */}
        <span
          className="inline-block shrink-0"
          style={{ width: `${depth * 10 + 20}px` }}
          aria-hidden
        />
        {createElement(pickMaterialIcon(material), {
          className: cn(
            "h-4 w-4 shrink-0",
            isActive ? "text-primary" : pickMaterialIconColor(material),
          ),
        })}
        <span className={cn("truncate font-mono", isActive && "font-medium")}>
          {title}
        </span>
      </Link>
    </li>
  );
}, (prev, next) =>
  prev.material === next.material &&
  prev.depth === next.depth &&
  prev.parentPath === next.parentPath &&
  prev.onActivate === next.onActivate &&
  // Only re-render this leaf when its own active state flips — a sibling/route
  // change that moves `activeId` elsewhere shouldn't repaint every leaf.
  (prev.activeId === prev.material.id) === (next.activeId === next.material.id),
);

interface TreeNodeProps {
  node: DirNode;
  depth: number;
  parentPath: string;
  expanded: Set<string>;
  childrenMap: Map<string, ChildrenPayload>;
  loadingIds: Set<string>;
  activeId: string | null;
  filter: string;
  onToggle: (node: DirNode) => void;
  onExpand: (node: DirNode) => void;
  onActivate: (id: string) => void;
}

const TreeNode = memo(function TreeNode({
  node,
  depth,
  parentPath,
  expanded,
  childrenMap,
  loadingIds,
  activeId,
  filter,
  onToggle,
  onExpand,
  onActivate,
}: TreeNodeProps) {
  const t = useTranslations("Browse");
  const router = useRouter();
  const isExpanded = expanded.has(node.id);
  const isLoading = loadingIds.has(node.id);
  const isActive = activeId === node.id;
  const children = childrenMap.get(node.id);
  const childDirCount = node.child_directory_count ?? 0;
  const childMatCount = node.child_material_count ?? 0;
  const hasChildren = childDirCount > 0 || childMatCount > 0;
  const totalItems = childDirCount + childMatCount;
  const ownPath = node.full_path ?? (parentPath ? `${parentPath}/${node.slug}` : node.slug);

  const trimmedFilter = filter.trim().toLowerCase();
  const filtering = trimmedFilter.length > 0;
  const nameMatches = !filtering || node.name.toLowerCase().includes(trimmedFilter);
  const descendantMatches = useMemo(() => {
    if (!filtering) return false;
    // Only known descendants can be checked; unknown branches will be revealed
    // as the user types if they're already loaded.
    const visit = (id: string): boolean => {
      const payload = childrenMap.get(id);
      if (!payload) return false;
      for (const m of payload.materials) {
        if (m.title.toLowerCase().includes(trimmedFilter)) return true;
      }
      for (const k of payload.directories) {
        if (k.name.toLowerCase().includes(trimmedFilter)) return true;
        if (visit(k.id)) return true;
      }
      return false;
    };
    return visit(node.id);
  }, [filtering, trimmedFilter, node.id, childrenMap]);

  if (filtering && !nameMatches && !descendantMatches) {
    return null;
  }

  // Force expansion when filtering reveals a deeper match
  const shouldShowChildren = isExpanded || (filtering && descendantMatches);

  return (
    <li>
      <div
        className={cn(
          "group/node flex items-center gap-0.5 rounded-md pr-1 transition-colors",
          "text-[13px] leading-tight",
          isActive
            ? "bg-accent text-accent-foreground"
            : "hover:bg-accent/50 text-foreground/90",
        )}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) onToggle(node);
          }}
          className={cn(
            "flex h-7 w-5 shrink-0 items-center justify-center rounded-sm",
            "text-muted-foreground hover:text-foreground",
            !hasChildren && "pointer-events-none opacity-0",
          )}
          aria-label={isExpanded ? "Collapse" : "Expand"}
          tabIndex={hasChildren ? 0 : -1}
          style={{ marginLeft: `${depth * 10}px` }}
        >
          {isLoading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-150",
                shouldShowChildren && "rotate-90",
              )}
            />
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            if (hasChildren) onToggle(node);
          }}
          onDoubleClick={() => {
            onExpand(node);
            onActivate(node.id);
            router.push(buildDirHref(node.full_path));
          }}
          className="flex flex-1 items-center gap-1.5 min-w-0 py-1.5 outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm text-left"
          title={node.name}
        >
          {shouldShowChildren && hasChildren ? (
            <FolderOpen
              className={cn(
                "h-4 w-4 shrink-0",
                isActive ? "text-primary" : "text-muted-foreground/80",
              )}
            />
          ) : (
            <Folder
              className={cn(
                "h-4 w-4 shrink-0",
                isActive ? "text-primary" : "text-muted-foreground/80",
              )}
            />
          )}
          <span className={cn("truncate font-mono", isActive && "font-medium")}>
            {node.name}
          </span>
        </button>
        {totalItems > 0 && (
          <span className="text-[10px] tabular-nums text-muted-foreground opacity-0 group-hover/node:opacity-100 transition-opacity pl-1 shrink-0">
            {totalItems}
          </span>
        )}
        <Link
          href={buildDirHref(node.full_path)}
          onClick={() => onActivate(node.id)}
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-sm",
            "text-muted-foreground hover:text-foreground",
            "opacity-0 group-hover/node:opacity-100 transition-opacity",
          )}
          title={node.name}
          aria-label={t("navigateTo")}
        >
          <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
      {shouldShowChildren && (
        <ul className="space-y-px">
          {children?.directories.map((c) => (
            <TreeNode
              key={c.id}
              node={c}
              depth={depth + 1}
              parentPath={ownPath}
              expanded={expanded}
              childrenMap={childrenMap}
              loadingIds={loadingIds}
              activeId={activeId}
              filter={filter}
              onToggle={onToggle}
              onExpand={onExpand}
              onActivate={onActivate}
            />
          ))}
          {children?.materials
            .filter(
              (m) => !filtering || m.title.toLowerCase().includes(trimmedFilter),
            )
            .map((m) => (
              <MaterialLeaf
                key={m.id}
                material={m}
                depth={depth + 1}
                parentPath={ownPath}
                activeId={activeId}
                onActivate={onActivate}
              />
            ))}
          {children &&
            children.directories.length === 0 &&
            children.materials.length === 0 &&
            !isLoading && (
              <li
                className="text-[11px] italic text-muted-foreground py-1"
                style={{ paddingLeft: `${(depth + 1) * 10 + 26}px` }}
              >
                {t("emptyDirectory")}
              </li>
            )}
          {!children && isLoading && (
            <li
              className="py-1.5"
              style={{ paddingLeft: `${(depth + 1) * 10 + 26}px` }}
            >
              <Skeleton className="h-3 w-32" />
            </li>
          )}
        </ul>
      )}
    </li>
  );
});

export function DirectoryTreeSidebar() {
  const t = useTranslations("Browse");
  // Subscribe to individual fields, not the whole store: a bare useUIStore()
  // re-renders this entire tree on every unrelated UI change — notably the
  // setSidebarTarget() calls fired on each navigation.
  const treeSidebarOpen = useUIStore((s) => s.treeSidebarOpen);
  const setTreeSidebarOpen = useUIStore((s) => s.setTreeSidebarOpen);
  const toggleTreeSidebar = useUIStore((s) => s.toggleTreeSidebar);
  const refreshCount = useBrowseRefreshStore((s) => s.refreshCount);

  const pathname = usePathname();
  const isOnBrowse = pathname === "/browse" || pathname.startsWith("/browse/");
  const currentSlugs = useMemo(() => {
    const stripped = pathname.replace(/^\/browse\/?/, "").replace(/\/$/, "");
    return stripped ? stripped.split("/") : [];
  }, [pathname]);
  const pathKey = currentSlugs.join("/");

  const [roots, setRoots] = useState<DirNode[] | null>(rootsCache.dirs);
  const [rootMaterials, setRootMaterials] = useState<MaterialNode[]>(
    rootsCache.mats,
  );
  const [childrenMap, setChildrenMap] = useState<Map<string, ChildrenPayload>>(
    () => new Map(childrenCache),
  );
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(expandedCache));
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadingRoots, setLoadingRoots] = useState(!rootsCache.dirs);

  const refreshRef = useRef(refreshCount);

  const fetchRoots = useCallback(async () => {
    setLoadingRoots(true);
    setError(null);
    try {
      const res = await apiFetchRetry<{
        directories: DirNode[];
        materials: unknown[];
      }>("/browse", { timeoutMs: TREE_TIMEOUT_MS });
      const dirs = res.directories || [];
      const mats = normalizeMaterials(res.materials || []);
      rootsCache.dirs = dirs;
      rootsCache.mats = mats;
      setRoots(dirs);
      setRootMaterials(mats);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("loadError"));
    } finally {
      setLoadingRoots(false);
    }
  }, [t]);

  useEffect(() => {
    if (!rootsCache.dirs) {
      fetchRoots();
    }
  }, [fetchRoots]);

  const expandedRef = useRef(expanded);
  useEffect(() => {
    expandedRef.current = expanded;
    // Persist to the module-level cache so a remount during navigation restores
    // the same open folders. `expanded` is always replaced immutably, so sharing
    // the reference is safe.
    expandedCache = expanded;
  }, [expanded]);

  const fetchChildren = useCallback(
    async (id: string): Promise<ChildrenPayload | null> => {
      if (childrenCache.has(id)) {
        const cached = childrenCache.get(id)!;
        setChildrenMap((m) => {
          if (m.get(id) === cached) return m;
          const n = new Map(m);
          n.set(id, cached);
          return n;
        });
        return cached;
      }
      setLoadingIds((s) => {
        if (s.has(id)) return s;
        const n = new Set(s);
        n.add(id);
        return n;
      });
      try {
        const res = await apiFetchRetry<{
          directories: DirNode[];
          materials: unknown[];
        }>(`/directories/${id}/children`, { timeoutMs: TREE_TIMEOUT_MS });
        const data: ChildrenPayload = {
          directories: res.directories || [],
          materials: normalizeMaterials(res.materials || []),
        };
        childrenCache.set(id, data);
        setChildrenMap((m) => {
          const n = new Map(m);
          n.set(id, data);
          return n;
        });
        return data;
      } catch {
        return null;
      } finally {
        setLoadingIds((s) => {
          if (!s.has(id)) return s;
          const n = new Set(s);
          n.delete(id);
          return n;
        });
      }
    },
    [],
  );

  // Fetch into the module cache WITHOUT touching React state. Used by the
  // auto-expand walk so a deep, cold path doesn't fire a full-tree re-render per
  // level (each setLoadingIds/setChildrenMap during the staggered network walk
  // repaints the whole tree). Children are committed to the render map in one
  // batched update once the walk finishes.
  const fetchChildrenQuiet = useCallback(
    async (id: string): Promise<ChildrenPayload | null> => {
      const cached = childrenCache.get(id);
      if (cached) return cached;
      try {
        const res = await apiFetchRetry<{
          directories: DirNode[];
          materials: unknown[];
        }>(`/directories/${id}/children`, { timeoutMs: TREE_TIMEOUT_MS });
        const data: ChildrenPayload = {
          directories: res.directories || [],
          materials: normalizeMaterials(res.materials || []),
        };
        childrenCache.set(id, data);
        return data;
      } catch {
        return null;
      }
    },
    [],
  );

  // Silent refetches for SSE-triggered updates — no loading spinners.
  const refetchRootSilent = useCallback(async () => {
    try {
      const res = await apiFetch<{ directories: DirNode[]; materials: unknown[] }>("/browse", { timeoutMs: TREE_TIMEOUT_MS });
      const dirs = res.directories || [];
      const mats = normalizeMaterials(res.materials || []);
      rootsCache.dirs = dirs;
      rootsCache.mats = mats;
      setRoots(dirs);
      setRootMaterials(mats);
    } catch { }
  }, []);

  const refetchChildSilent = useCallback(async (id: string) => {
    childrenCache.delete(id);
    try {
      const res = await apiFetch<{ directories: DirNode[]; materials: unknown[] }>(
        `/directories/${id}/children`,
        { timeoutMs: TREE_TIMEOUT_MS },
      );
      const data: ChildrenPayload = {
        directories: res.directories || [],
        materials: normalizeMaterials(res.materials || []),
      };
      childrenCache.set(id, data);
      setChildrenMap((m) => {
        if (!m.has(id)) return m;
        const n = new Map(m);
        n.set(id, data);
        return n;
      });
    } catch { }
  }, []);

  // Maintain one SSE connection per expanded directory (plus root).
  // Each connection listens for child_added / child_removed and silently
  // refetches only the affected directory — no full-tree reload, no spinners.
  const treeConnectionsRef = useRef<Map<string, SSEConnection>>(new Map());

  useEffect(() => {
    const watched = new Set(["root", ...Array.from(expanded)]);
    const existing = treeConnectionsRef.current;

    // Close connections for directories no longer watched.
    for (const id of Array.from(existing.keys())) {
      if (!watched.has(id)) {
        existing.get(id)!.close();
        existing.delete(id);
      }
    }

    // Open connections for newly watched directories.
    for (const id of watched) {
      if (!existing.has(id)) {
        const handleChange = () => {
          if (id === "root") void refetchRootSilent();
          else void refetchChildSilent(id);
        };
        const conn = createSSEConnection({
          url: `/directories/${id}/sse`,
          listeners: {
            child_added: handleChange,
            child_updated: handleChange,
            child_removed: handleChange,
            pr_closed: handleChange,
          },
          startupDelay: 50,
        });
        existing.set(id, conn);
      }
    }
  }, [expanded, refetchRootSilent, refetchChildSilent]);

  // Close all tree SSE connections on unmount.
  useEffect(() => {
    const connections = treeConnectionsRef.current;
    return () => {
      for (const conn of connections.values()) conn.close();
      connections.clear();
    };
  }, []);

  // Background revalidation for cached-but-stale directories (no loading spinner).
  // Unlike refetchChildSilent, the old cache is kept until fresh data arrives.
  const revalidateChildSilent = useCallback(async (id: string) => {
    try {
      const res = await apiFetch<{ directories: DirNode[]; materials: unknown[] }>(
        `/directories/${id}/children`,
        { timeoutMs: TREE_TIMEOUT_MS },
      );
      const data: ChildrenPayload = {
        directories: res.directories || [],
        materials: normalizeMaterials(res.materials || []),
      };
      childrenCache.set(id, data);
      setChildrenMap((m) => {
        const n = new Map(m);
        n.set(id, data);
        return n;
      });
    } catch { }
  }, []);

  const handleToggle = useCallback(
    (node: DirNode) => {
      setExpanded((s) => {
        const n = new Set(s);
        if (n.has(node.id)) {
          n.delete(node.id);
        } else {
          n.add(node.id);
          const hasKids =
            (node.child_directory_count ?? 0) > 0 ||
            (node.child_material_count ?? 0) > 0;
          if (!childrenCache.has(node.id) && hasKids) {
            void fetchChildren(node.id);
          } else if (childrenCache.has(node.id) && hasKids) {
            void revalidateChildSilent(node.id);
          }
        }
        return n;
      });
    },
    [fetchChildren, revalidateChildSilent],
  );

  const handleExpand = useCallback(
    (node: DirNode) => {
      setExpanded((s) => {
        if (s.has(node.id)) return s;
        const n = new Set(s);
        n.add(node.id);
        const hasKids =
          (node.child_directory_count ?? 0) > 0 ||
          (node.child_material_count ?? 0) > 0;
        if (!childrenCache.has(node.id) && hasKids) {
          void fetchChildren(node.id);
        } else if (childrenCache.has(node.id) && hasKids) {
          void revalidateChildSilent(node.id);
        }
        return n;
      });
    },
    [fetchChildren, revalidateChildSilent],
  );

  // Invalidate caches whenever the browse data changes elsewhere (e.g. a PR
  // got approved and the directory layout shifted). Keep expanded ids so the
  // tree visually re-opens at the same nodes once refetched.
  useEffect(() => {
    if (refreshCount === refreshRef.current) return;
    refreshRef.current = refreshCount;

    const previouslyExpanded = expandedRef.current;
    clearTreeCaches();
    setChildrenMap(new Map());
    fetchRoots().then(() => {
      previouslyExpanded.forEach((id) => {
        void fetchChildren(id);
      });
    });
  }, [refreshCount, fetchRoots, fetchChildren]);

  // Auto-expand the path to the current directory whenever it changes
  // (or after fresh root data lands).
  useEffect(() => {
    if (!roots) {
      setActiveId(null);
      return;
    }
    if (currentSlugs.length === 0) {
      setActiveId(null);
      return;
    }
    let cancelled = false;

    (async () => {
      let currentDirs: DirNode[] = roots;
      let currentMats: MaterialNode[] = rootMaterials;
      const toExpand: string[] = [];
      let lastId: string | null = null;

      for (let i = 0; i < currentSlugs.length; i++) {
        const slug = currentSlugs[i];
        const dirMatch = currentDirs.find((d) => d.slug === slug);
        const isLast = i === currentSlugs.length - 1;

        // Materials only match at the final slug (they are leaves)
        if (isLast && !dirMatch) {
          const matMatch = currentMats.find((m) => m.slug === slug);
          if (matMatch) {
            lastId = matMatch.id;
            break;
          }
        }

        if (!dirMatch) break;
        lastId = dirMatch.id;

        if (!isLast) {
          toExpand.push(dirMatch.id);
          let kids = childrenCache.get(dirMatch.id) ?? null;
          if (!kids) {
            kids = await fetchChildrenQuiet(dirMatch.id);
            if (cancelled || !kids) return;
          }
          // If the next slug isn't in the cached data, the cache may be stale —
          // force a fresh fetch so a newly-created subdirectory is found.
          const nextSlug = currentSlugs[i + 1];
          if (
            nextSlug &&
            !kids.directories.some((d) => d.slug === nextSlug) &&
            !kids.materials.some((m) => m.slug === nextSlug)
          ) {
            childrenCache.delete(dirMatch.id);
            kids = await fetchChildrenQuiet(dirMatch.id);
            if (cancelled || !kids) return;
          }
          currentDirs = kids.directories;
          currentMats = kids.materials;
        }
      }

      if (cancelled) return;

      // Commit the children fetched during the walk into the render map in a
      // single update — batched with setExpanded/setActiveId below into one
      // re-render instead of one per traversed level.
      if (toExpand.length > 0) {
        setChildrenMap((m) => {
          let n = m;
          let changed = false;
          for (const id of toExpand) {
            const cached = childrenCache.get(id);
            if (cached && m.get(id) !== cached) {
              if (!changed) {
                n = new Map(m);
                changed = true;
              }
              n.set(id, cached);
            }
          }
          return changed ? n : m;
        });
      }

      if (toExpand.length > 0) {
        setExpanded((s) => {
          const n = new Set(s);
          let mutated = false;
          for (const id of toExpand) {
            if (!n.has(id)) {
              n.add(id);
              mutated = true;
            }
          }
          return mutated ? n : s;
        });
      }
      setActiveId(lastId);
    })();

    return () => {
      cancelled = true;
    };
  }, [roots, rootMaterials, pathKey, fetchChildrenQuiet, currentSlugs]);

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------

  // Closed state — show a compact "peeker" rail on the left edge of the page
  // with just the open icon and a vertical "Tree" label.
  if (!treeSidebarOpen) {
    return (
      <button
        type="button"
        onClick={() => setTreeSidebarOpen(true)}
        title={t("showTree")}
        aria-label={t("showTree")}
        className={cn(
          "group hidden md:flex h-full w-7 shrink-0 flex-col items-center",
          "border-r bg-muted/20 hover:bg-accent/40 transition-colors",
          "py-3 gap-3",
        )}
      >
        <PanelLeftOpen className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors" />
        <span
          className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/70 group-hover:text-foreground transition-colors"
          style={{ writingMode: "vertical-rl" }}
        >
          {t("tree")}
        </span>
      </button>
    );
  }

  const rootMatches =
    !filter.trim() ||
    "home".includes(filter.trim().toLowerCase()) ||
    t("home").toLowerCase().includes(filter.trim().toLowerCase());

  return (
    <aside
      className={cn(
        "relative h-full hidden md:flex flex-col shrink-0 overflow-hidden",
        "border-r border-border bg-background",
        "transition-[width] duration-150 ease-in-out",
        "w-72",
      )}
      aria-label={t("tree")}
    >
      <div className="flex items-center justify-between border-b px-3 py-2 shrink-0 bg-muted/10">
        <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/70 flex items-center gap-2">
          <Folder className="h-3 w-3" />
          {t("tree")}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 rounded-full hover:bg-accent"
          onClick={() => toggleTreeSidebar()}
          title={t("hideTree")}
          aria-label={t("hideTree")}
        >
          <PanelLeftClose className="h-4 w-4" />
        </Button>
      </div>

      <div className="px-2 pt-2 pb-1 shrink-0">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t("filterFolders")}
            className="h-8 pl-7 pr-7 text-xs"
            aria-label={t("filterFolders")}
          />
          {filter && (
            <button
              type="button"
              onClick={() => setFilter("")}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50"
              aria-label={t("clear")}
              title={t("clear")}
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      <nav className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-1.5 py-1">
        {rootMatches && (
          <Link
            href="/browse"
            className={cn(
              "flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px]",
              "transition-colors",
              activeId === null && currentSlugs.length === 0 && isOnBrowse
                ? "bg-accent text-accent-foreground font-medium"
                : "hover:bg-accent/50 text-foreground/90",
            )}
            title={t("home")}
          >
            <Home
              className={cn(
                "h-4 w-4",
                activeId === null && currentSlugs.length === 0 && isOnBrowse
                  ? "text-primary"
                  : "text-muted-foreground/80",
              )}
            />
            <span>{t("home")}</span>
          </Link>
        )}

        {loadingRoots && (
          <div className="space-y-2 px-2 py-2">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </div>
        )}

        {!loadingRoots && error && (
          <div className="flex flex-col items-start gap-2 px-3 py-4 text-xs text-destructive">
            <span>{error}</span>
            <button
              onClick={() => void fetchRoots()}
              className="rounded-md border border-current px-2 py-1 font-medium text-foreground transition-colors hover:bg-foreground/5"
            >
              {t("retry")}
            </button>
          </div>
        )}

        {!loadingRoots && !error && roots && (
          <ul className="space-y-px mt-0.5">
            {roots.map((r) => (
              <TreeNode
                key={r.id}
                node={r}
                depth={0}
                parentPath=""
                expanded={expanded}
                childrenMap={childrenMap}
                loadingIds={loadingIds}
                activeId={activeId}
                filter={filter}
                onToggle={handleToggle}
                onExpand={handleExpand}
                onActivate={setActiveId}
              />
            ))}
            {rootMaterials
              .filter(
                (m) =>
                  !filter.trim() ||
                  m.title.toLowerCase().includes(filter.trim().toLowerCase()),
              )
              .map((m) => (
                <MaterialLeaf
                  key={m.id}
                  material={m}
                  depth={0}
                  parentPath=""
                  activeId={activeId}
                  onActivate={setActiveId}
                />
              ))}
            {roots.length === 0 && rootMaterials.length === 0 && (
              <li className="text-xs text-muted-foreground italic px-3 py-2">
                {t("emptyDirectory")}
              </li>
            )}
          </ul>
        )}
      </nav>
    </aside>
  );
}
