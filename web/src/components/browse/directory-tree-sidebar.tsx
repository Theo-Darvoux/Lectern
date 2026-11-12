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
import { usePathname, useRouter } from "next/navigation";
import { BrowseLink, navigateBrowse } from "@/components/browse/browse-link";
import {
  ArrowRight,
  ChevronRight,
  ExternalLink,
  File,
  Folder,
  FolderOpen,
  Home,
  Link2,
  ListChecks,
  Loader2,
  Clock3,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from "lucide-react";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/lib/stores";
import { directoryTreeQuery, type TreeChildren } from "@/lib/browse-prefetch";
import { subscribeToSSE } from "@/lib/sse-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslations } from "next-intl";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { isExternalUrl } from "@/lib/url-utils";
import { pendingCreatesForParent } from "@/lib/pending-contributions";
import { EXT_ICONS, TYPE_ICONS } from "@/lib/material-icons";
import {
  getFileExtension,
  getFileIconColor,
  MATERIAL_TYPE_ICON_COLORS,
  MIME_QCM,
} from "@/lib/file-utils";
import { useSubmittedOperations } from "@/hooks/use-submitted-operations";

interface DirNode {
  id: string;
  name: string;
  slug: string;
  full_path?: string;
  child_directory_count?: number;
  child_material_count?: number;
  parent_id?: string | null;
  pending?: boolean;
}

interface MaterialNode {
  id: string;
  title: string;
  slug: string;
  type: string;
  file_name?: string | null;
  file_mime_type?: string | null;
  /** Target of a link material. */
  url?: string | null;
  pending?: boolean;
}

interface ChildrenPayload {
  directories: DirNode[];
  materials: MaterialNode[];
}

const ROOT_ID = "root";

function pickMaterialIcon(mat: MaterialNode): React.ElementType {
  const targetUrl = (mat.url ?? "").trim();
  const isLink = mat.type === "link" || !!targetUrl;
  if (isLink) {
    return isExternalUrl(targetUrl) ? ExternalLink : Link2;
  }
  if (mat.type === "document") {
    const ext = mat.file_name ? getFileExtension(mat.file_name) : "";
    if (ext && EXT_ICONS[ext]) return EXT_ICONS[ext];
    if (mat.file_mime_type === MIME_QCM) return ListChecks;
  }
  return TYPE_ICONS[mat.type] ?? File;
}

function pickMaterialIconColor(mat: MaterialNode): string {
  const targetUrl = (mat.url ?? "").trim();
  const isLink = mat.type === "link" || !!targetUrl;
  if (isLink) {
    return isExternalUrl(targetUrl)
      ? (MATERIAL_TYPE_ICON_COLORS["link"] ?? "text-sky-600 dark:text-sky-400")
      : (MATERIAL_TYPE_ICON_COLORS["internal_link"] ?? "text-indigo-600 dark:text-indigo-400");
  }
  if (mat.type === "document") {
    return getFileIconColor(mat.file_name ?? "", mat.file_mime_type ?? undefined);
  }
  return MATERIAL_TYPE_ICON_COLORS[mat.type] ?? "text-muted-foreground";
}

// Open folders survive remounts, so navigating between directories keeps
// previously-opened folders open instead of collapsing everything except the
// path that was just navigated to. The branch data itself lives in the query
// cache (cleared when the account changes).
let expandedCache = new Set<string>();

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
  const t = useTranslations("Browse");
  const router = useRouter();
  const openLink = useExternalLinkStore((s) => s.openLink);
  const isActive = activeId === material.id;
  const title = material.title || "Untitled";

  if (material.pending) {
    return (
      <li>
        <div
          className="flex items-center gap-1.5 rounded-md py-1.5 pr-1 text-[13px] text-blue-700 dark:text-blue-300"
          style={{ paddingLeft: `${depth * 10 + 20}px` }}
          title={t("contribution")}
        >
          <Clock3 className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate font-mono">{title}</span>
          <span className="sr-only">({t("contribution")})</span>
        </div>
      </li>
    );
  }

  const targetUrl = (material.url ?? "").trim();
  const isLink = material.type === "link" || !!targetUrl;

  const handleClick = (e: React.MouseEvent) => {
    onActivate(material.id);
    if (isLink && targetUrl) {
      e.preventDefault();
      openLink(targetUrl, (path) => router.push(path));
    }
  };

  return (
    <li>
      <BrowseLink
        href={buildMaterialHref(parentPath, material.slug)}
        onClick={handleClick}
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
      </BrowseLink>
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
  const router = useRouter();
  const pathname = usePathname();
  const isOnBrowse = pathname === "/browse" || pathname?.startsWith("/browse/");
  const t = useTranslations("Browse");
  const isExpanded = expanded.has(node.id);
  const isLoading = loadingIds.has(node.id);
  const isActive = activeId === node.id;
  const children = childrenMap.get(node.id);
  const childDirCount = node.child_directory_count ?? 0;
  const childMatCount = node.child_material_count ?? 0;
  const projectedChildCount = (children?.directories.length ?? 0) + (children?.materials.length ?? 0);
  const hasChildren = childDirCount > 0 || childMatCount > 0 || projectedChildCount > 0;
  const totalItems = Math.max(childDirCount + childMatCount, projectedChildCount);
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

  if (node.pending) {
    const shouldShowPendingChildren = isExpanded || (filtering && descendantMatches);
    return (
      <li>
        <div className="flex items-center gap-0.5 rounded-md pr-1 text-[13px] text-blue-700 dark:text-blue-300">
          <button
            type="button"
            onClick={() => hasChildren && onToggle(node)}
            className={cn(
              "flex h-7 w-5 shrink-0 items-center justify-center rounded-sm",
              !hasChildren && "pointer-events-none opacity-0",
            )}
            aria-label={isExpanded ? t("collapseFolder") : t("expandFolder")}
            aria-expanded={hasChildren ? isExpanded : undefined}
            tabIndex={hasChildren ? 0 : -1}
            style={{ marginLeft: `${depth * 10}px` }}
          >
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-150",
                shouldShowPendingChildren && "rotate-90",
              )}
            />
          </button>
          <Clock3 className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate font-mono">{node.name}</span>
          <span className="sr-only">({t("contribution")})</span>
        </div>
        {shouldShowPendingChildren && children && (
          <ul>
            {children.directories.map((child) => (
              <TreeNode
                key={child.id}
                node={child}
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
            {children.materials.map((material) => (
              <MaterialLeaf
                key={material.id}
                material={material}
                depth={depth + 1}
                parentPath={ownPath}
                activeId={activeId}
                onActivate={onActivate}
              />
            ))}
          </ul>
        )}
      </li>
    );
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
          aria-label={isExpanded ? t("collapseFolder") : t("expandFolder")}
          aria-expanded={hasChildren ? isExpanded : undefined}
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
            const targetHref = buildDirHref(node.full_path);
            if (isOnBrowse) {
              navigateBrowse(targetHref);
            } else {
              router.push(targetHref);
            }
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
        <BrowseLink
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
        </BrowseLink>
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
  const queryClient = useQueryClient();

  const pathname = usePathname();
  const isOnBrowse = pathname === "/browse" || pathname.startsWith("/browse/");
  const currentSlugs = useMemo(() => {
    const stripped = pathname.replace(/^\/browse\/?/, "").replace(/\/$/, "");
    return stripped ? stripped.split("/") : [];
  }, [pathname]);
  const pathKey = currentSlugs.join("/");

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(expandedCache));
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeDirectoryId, setActiveDirectoryId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const submittedOperations = useSubmittedOperations();

  useEffect(() => {
    expandedCache = expanded;
  }, [expanded]);

  // ── Data: one query per visible branch, shared with every other tree ──
  const rootQuery = useQuery({ ...directoryTreeQuery(ROOT_ID), enabled: treeSidebarOpen });
  const roots = rootQuery.data ?? null;

  const branchIds = useMemo(() => {
    const ids = new Set(expanded);
    if (activeDirectoryId) ids.add(activeDirectoryId);
    return [...ids];
  }, [expanded, activeDirectoryId]);

  const combineBranches = useCallback(
    (results: { data?: TreeChildren; isPending: boolean }[]) => {
      const childrenMap = new Map<string, ChildrenPayload>();
      const loadingIds = new Set<string>();
      results.forEach((result, i) => {
        if (result.data) childrenMap.set(branchIds[i], result.data);
        else if (result.isPending) loadingIds.add(branchIds[i]);
      });
      return { childrenMap, loadingIds };
    },
    [branchIds],
  );
  const { childrenMap, loadingIds } = useQueries({
    queries: branchIds.map((id) => ({ ...directoryTreeQuery(id), enabled: treeSidebarOpen })),
    combine: combineBranches,
  });

  const pendingNodesFor = useCallback((parentId: string | null) => {
    const projected = pendingCreatesForParent(submittedOperations, parentId);
    return {
      directories: projected.directories.map((operation) => ({
        id: operation.temp_id || `pending-dir-${operation.name}`,
        name: operation.name,
        slug: "",
        pending: true,
      } satisfies DirNode)),
      materials: projected.materials.map((operation) => ({
        id: operation.temp_id || `pending-material-${operation.title}`,
        title: operation.title,
        slug: "",
        type: operation.type,
        file_name: operation.file_name ?? undefined,
        file_mime_type: operation.file_mime_type ?? undefined,
        pending: true,
      } satisfies MaterialNode)),
    };
  }, [submittedOperations]);

  const projectedRoots = useMemo(() => {
    const pending = pendingNodesFor(null);
    return {
      directories: [...(roots?.directories ?? []), ...pending.directories],
      materials: [...(roots?.materials ?? []), ...pending.materials],
    };
  }, [roots, pendingNodesFor]);

  const projectedChildrenMap = useMemo(() => {
    const next = new Map(childrenMap);
    const parentIds = new Set(childrenMap.keys());
    for (const operation of submittedOperations) {
      if (operation.op === "create_directory" && operation.parent_id) {
        parentIds.add(operation.parent_id);
      } else if (operation.op === "create_material" && operation.directory_id) {
        parentIds.add(operation.directory_id);
      }
    }
    for (const id of parentIds) {
      const children = childrenMap.get(id) ?? { directories: [], materials: [] };
      const pending = pendingNodesFor(id);
      if (pending.directories.length === 0 && pending.materials.length === 0) continue;
      next.set(id, {
        directories: [...children.directories, ...pending.directories],
        materials: [...children.materials, ...pending.materials],
      });
    }
    return next;
  }, [childrenMap, pendingNodesFor, submittedOperations]);

  // Reconcile anything missed while the tree was closed (no live updates then).
  const wasSidebarOpenRef = useRef(treeSidebarOpen);
  useEffect(() => {
    const wasOpen = wasSidebarOpenRef.current;
    wasSidebarOpenRef.current = treeSidebarOpen;
    if (treeSidebarOpen && !wasOpen) {
      void queryClient.invalidateQueries({ queryKey: ["directory-tree"] });
    }
  }, [treeSidebarOpen, queryClient]);

  // Subscribe to each visible logical branch. These all share the single
  // per-user transport, so expanding the tree does not create more sockets.
  useEffect(() => {
    if (!treeSidebarOpen) return;
    const subscribe = (id: string) => {
      const refresh = () =>
        void queryClient.invalidateQueries({ queryKey: ["directory-tree", id], exact: true });
      return subscribeToSSE({
        channel: `directory:${id}`,
        listeners: {
          child_added: refresh,
          child_updated: refresh,
          child_removed: refresh,
          pr_closed: refresh,
        },
        onResync: refresh,
      });
    };
    const connections = [ROOT_ID, ...branchIds].map(subscribe);
    return () => connections.forEach((connection) => connection.close());
  }, [treeSidebarOpen, branchIds, queryClient]);

  const toggleNode = useCallback((node: DirNode, mode: "toggle" | "expand") => {
    setExpanded((s) => {
      if (s.has(node.id) && mode === "expand") return s;
      const n = new Set(s);
      if (n.has(node.id)) n.delete(node.id);
      else n.add(node.id);
      return n;
    });
  }, []);
  const handleToggle = useCallback((node: DirNode) => toggleNode(node, "toggle"), [toggleNode]);
  const handleExpand = useCallback((node: DirNode) => toggleNode(node, "expand"), [toggleNode]);

  const lastAutoExpandedPathRef = useRef<string | null>(null);

  // Auto-expand the path to the current directory whenever it changes
  // (or after fresh root data lands).
  useEffect(() => {
    if (!roots) {
      setActiveId(null);
      setActiveDirectoryId(null);
      return;
    }
    if (currentSlugs.length === 0) {
      lastAutoExpandedPathRef.current = "";
      setActiveId(null);
      setActiveDirectoryId(null);
      return;
    }
    let cancelled = false;

    (async () => {
      let current: TreeChildren = roots;
      const toExpand: string[] = [];
      let lastId: string | null = null;
      let currentDirectoryId: string | null = null;

      for (let i = 0; i < currentSlugs.length; i++) {
        const slug = currentSlugs[i];
        const dirMatch = current.directories.find((d) => d.slug === slug);
        const isLast = i === currentSlugs.length - 1;

        // Materials only match at the final slug (they are leaves)
        if (isLast && !dirMatch) {
          const matMatch = current.materials.find((m) => m.slug === slug);
          if (matMatch) {
            lastId = matMatch.id;
            break;
          }
        }

        if (!dirMatch) break;
        lastId = dirMatch.id;
        currentDirectoryId = dirMatch.id;
        if (isLast) break;

        toExpand.push(dirMatch.id);
        try {
          let kids = await queryClient.fetchQuery(directoryTreeQuery(dirMatch.id));
          // If the next slug isn't in the cached branch, it may be stale: a
          // newly created subfolder needs a fresh fetch to be found.
          const nextSlug = currentSlugs[i + 1];
          if (
            !kids.directories.some((d) => d.slug === nextSlug) &&
            !kids.materials.some((m) => m.slug === nextSlug)
          ) {
            kids = await queryClient.fetchQuery({ ...directoryTreeQuery(dirMatch.id), staleTime: 0 });
          }
          if (cancelled) return;
          current = kids;
        } catch {
          break;
        }
      }

      if (cancelled) return;

      const shouldAutoExpand = lastAutoExpandedPathRef.current !== pathKey;
      if (shouldAutoExpand && toExpand.length > 0) {
        setExpanded((s) => {
          if (toExpand.every((id) => s.has(id))) return s;
          return new Set([...s, ...toExpand]);
        });
      }
      lastAutoExpandedPathRef.current = pathKey;
      setActiveId(lastId);
      setActiveDirectoryId(currentDirectoryId);
    })();

    return () => {
      cancelled = true;
    };
  }, [roots, pathKey, currentSlugs, queryClient]);

  const loadingRoots = rootQuery.isPending;
  const error = rootQuery.isError && !roots ? t("loadError") : null;

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
      data-tutorial="sidebar-tree"
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
          <BrowseLink
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
          </BrowseLink>
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
              onClick={() => void rootQuery.refetch()}
              className="rounded-md border border-current px-2 py-1 font-medium text-foreground transition-colors hover:bg-foreground/5"
            >
              {t("retry")}
            </button>
          </div>
        )}

        {!loadingRoots && !error && roots && (
          <ul className="space-y-px mt-0.5">
            {projectedRoots.directories.map((r) => (
              <TreeNode
                key={r.id}
                node={r}
                depth={0}
                parentPath=""
                expanded={expanded}
                childrenMap={projectedChildrenMap}
                loadingIds={loadingIds}
                activeId={activeId}
                filter={filter}
                onToggle={handleToggle}
                onExpand={handleExpand}
                onActivate={setActiveId}
              />
            ))}
            {projectedRoots.materials
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
            {projectedRoots.directories.length === 0 && projectedRoots.materials.length === 0 && (
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
