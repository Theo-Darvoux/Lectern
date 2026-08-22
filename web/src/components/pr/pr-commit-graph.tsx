"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { fr, enUS } from "date-fns/locale";
import { useTranslations, useLocale } from "next-intl";
import {
  Copy,
  Check,
  ChevronRight,
  GitPullRequest,
  Undo2,
  FilePlus,
  FilePenLine,
  FileX,
  FolderPlus,
  FolderPen,
  FolderX,
  ArrowRightLeft,
  Layers,
  Sparkles,
  Cloud,
  CircleDot,
} from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { PullRequestOut } from "@/components/home/types";
import { cn } from "@/lib/utils";

const LANE_WIDTH = 16;
const ROW_HEIGHT = 36;
const PADDING_LEFT = 16;

const BRANCH_COLORS = [
  "#a855f7", // Purple (main trunk)
  "#f59e0b", // Amber (branch 1)
  "#3b82f6", // Sky / Blue (branch 2)
  "#ec4899", // Pink / Rose (branch 3)
  "#10b981", // Emerald (branch 4)
  "#06b6d4", // Cyan (branch 5)
  "#f97316", // Orange (branch 6)
];

const OP_ICONS: Record<string, React.ElementType> = {
  create_material: FilePlus,
  new: FilePlus,
  edit_material: FilePenLine,
  update: FilePenLine,
  delete_material: FileX,
  delete: FileX,
  create_directory: FolderPlus,
  edit_directory: FolderPen,
  delete_directory: FolderX,
  move_item: ArrowRightLeft,
  batch: Layers,
  revert: Undo2,
};

interface PRCommitGraphProps {
  prs: PullRequestOut[];
  loading?: boolean;
}

type NodeType = "mainHead" | "merge" | "commit" | "open" | "rejected" | "revert";

interface ComputedNode {
  pr: PullRequestOut;
  lane: number;
  x: number;
  y: number;
  color: string;
  nodeType: NodeType;
  isMain: boolean;
  isOpen: boolean;
  isApproved: boolean;
  isRejected: boolean;
  isRevert: boolean;
  showMainBadge: boolean;
  showOriginBadge: boolean;
}

interface GraphPath {
  d: string;
  color: string;
  dashed?: boolean;
  strokeWidth?: number;
}

export function PRCommitGraph({ prs, loading }: PRCommitGraphProps) {
  const t = useTranslations("PRs");
  const locale = useLocale();
  const dateLocale = locale === "fr" ? fr : enUS;

  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copyHash = async (id: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(id);
      setCopiedId(id);
      toast.success(t("hashCopied"));
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      toast.error("Failed to copy ID");
    }
  };

  // Compute authentic Git commit graph lanes and paths
  const { nodes, paths, totalHeight, svgWidth } = useMemo(() => {
    if (!prs || prs.length === 0) {
      return { nodes: [], paths: [], totalHeight: 0, svgWidth: 48 };
    }

    let maxLane = 1;
    let firstApprovedIndex = -1;
    let lastApprovedIndex = -1;

    // Find boundary indices of approved PRs
    prs.forEach((pr, i) => {
      if (pr.status === "approved") {
        if (firstApprovedIndex === -1) firstApprovedIndex = i;
        lastApprovedIndex = i;
      }
    });

    // 1. Determine origin and merge points for each PR
    // A PR is a branch if it was developed in parallel (originIndex >= index + 2) or is open/rejected/batch.
    // Sequential PRs on main (originIndex === index + 1) are linear trunk commits.
    const prIntervals = prs.map((pr, index) => {
      const isOpen = pr.status === "open";
      const isRejected = pr.status === "rejected";
      const isApproved = pr.status === "approved";
      const isRevert = pr.type === "revert" || Boolean(pr.reverts_pr_id);
      const isExplicitMerge =
        pr.title.toLowerCase().startsWith("merge") ||
        pr.type === "batch" ||
        (pr.summary_types && pr.summary_types.length > 1);

      const prCreatedTime = new Date(pr.created_at).getTime();

      // Find origin on main: latest approved PR at or before creation time
      let originIndex = -1;
      for (let i = index + 1; i < prs.length; i++) {
        if (prs[i].status === "approved") {
          const baseMergedTime = new Date(prs[i].updated_at || prs[i].created_at).getTime();
          if (baseMergedTime <= prCreatedTime || originIndex === -1) {
            originIndex = i;
            if (baseMergedTime <= prCreatedTime) break;
          }
        }
      }

      if (originIndex === -1 && lastApprovedIndex >= 0 && lastApprovedIndex > index) {
        originIndex = lastApprovedIndex;
      }

      // Is this a parallel branch (spans 2+ commits, or open/rejected)?
      const isParallelBranch =
        isOpen ||
        isRejected ||
        (isApproved && originIndex >= index + 2) ||
        (isApproved && index > 0 && index % 4 === 1 && originIndex >= index + 1);

      const mergeIndex = isApproved ? index : -1;
      const topRow = index;
      const bottomRow = originIndex >= 0 ? Math.max(originIndex, index) : index;

      return {
        pr,
        index,
        originIndex,
        mergeIndex,
        topRow,
        bottomRow,
        isParallelBranch,
        isExplicitMerge,
        isRevert,
      };
    });

    // 2. Allocate lanes using interval coloring
    // Lane 0 is the main trunk. Branches get Lane 1, 2, 3, 4.
    const laneOccupancy: number[][] = [];
    const assignedLanes = new Map<string, number>();

    prIntervals.forEach(({ pr, topRow, bottomRow, isParallelBranch }) => {
      if (!isParallelBranch) {
        // Direct commit on main trunk (Lane 0)
        assignedLanes.set(pr.id, 0);
        return;
      }

      let lane = 1;
      while (lane <= 4) {
        if (!laneOccupancy[lane]) {
          laneOccupancy[lane] = [];
        }

        const hasCollision = laneOccupancy[lane].some(
          (r) => r >= topRow && r <= bottomRow,
        );

        if (!hasCollision) {
          for (let r = topRow; r <= bottomRow; r++) {
            laneOccupancy[lane].push(r);
          }
          assignedLanes.set(pr.id, lane);
          maxLane = Math.max(maxLane, lane);
          break;
        }

        lane++;
      }

      if (!assignedLanes.has(pr.id)) {
        assignedLanes.set(pr.id, 1);
        maxLane = Math.max(maxLane, 1);
      }
    });

    // 3. Construct computed nodes
    const computedNodes: ComputedNode[] = prs.map((pr, index) => {
      const isOpen = pr.status === "open";
      const isApproved = pr.status === "approved";
      const isRejected = pr.status === "rejected";
      const isRevert = pr.type === "revert" || Boolean(pr.reverts_pr_id);
      const isExplicitMerge =
        pr.title.toLowerCase().startsWith("merge") ||
        pr.type === "batch" ||
        (pr.summary_types && pr.summary_types.length > 1);

      const lane = assignedLanes.get(pr.id) ?? 0;
      const color = isRevert
        ? "#06b6d4"
        : isRejected
          ? "#f43f5e"
          : lane === 0
            ? isExplicitMerge
              ? "#3b82f6"
              : "#a855f7"
            : BRANCH_COLORS[lane % BRANCH_COLORS.length];

      let showMainBadge = false;
      let showOriginBadge = false;
      let nodeType: NodeType = "commit";

      if (isApproved) {
        if (index === firstApprovedIndex) {
          showMainBadge = true;
          showOriginBadge = true;
          nodeType = "mainHead";
        } else if (isRevert) {
          nodeType = "revert";
        } else if (isExplicitMerge || lane > 0) {
          nodeType = "merge";
        } else {
          nodeType = "commit";
        }
      } else if (isOpen) {
        nodeType = "open";
      } else if (isRejected) {
        nodeType = "rejected";
      }

      const x = PADDING_LEFT + lane * LANE_WIDTH;
      const y = index * ROW_HEIGHT + ROW_HEIGHT / 2;

      return {
        pr,
        lane,
        x,
        y,
        color,
        nodeType,
        isMain: isApproved && lane === 0,
        isOpen,
        isApproved,
        isRejected,
        isRevert,
        showMainBadge,
        showOriginBadge,
      };
    });

    const computedPaths: GraphPath[] = [];
    const mainX = PADDING_LEFT;

    // 4. Main Trunk: Continuous vertical line down lane 0 connecting all merged/origin points
    if (firstApprovedIndex >= 0 && lastApprovedIndex >= firstApprovedIndex) {
      const topY = computedNodes[firstApprovedIndex].y;
      const bottomY = computedNodes[lastApprovedIndex].y;
      if (topY !== bottomY) {
        computedPaths.push({
          d: `M ${mainX} ${topY} L ${mainX} ${bottomY}`,
          color: "#a855f7",
          strokeWidth: 2,
        });
      }
    }

    // 5. Generate branch curves for parallel feature branches
    prIntervals.forEach(({ pr, index, originIndex, mergeIndex, isParallelBranch }) => {
      if (!isParallelBranch) return;

      const node = computedNodes[index];
      const branchX = PADDING_LEFT + (assignedLanes.get(pr.id) ?? 1) * LANE_WIDTH;
      const branchColor = node.color;
      const nodeY = node.y;

      if (originIndex >= 0 && originIndex !== index) {
        const originY = computedNodes[originIndex].y;
        const dy = Math.abs(originY - nodeY);

        if (pr.status === "approved" && mergeIndex >= 0) {
          const mergeY = computedNodes[mergeIndex].y;

          if (dy <= ROW_HEIGHT) {
            // 1-step clean curve
            const midY = (originY + mergeY) / 2;
            const curve = `M ${mainX} ${originY} C ${mainX} ${originY - 6}, ${branchX} ${midY + 6}, ${branchX} ${midY} C ${branchX} ${midY - 6}, ${mainX} ${mergeY + 6}, ${mainX} ${mergeY}`;
            computedPaths.push({
              d: curve,
              color: branchColor,
              strokeWidth: 2,
            });
          } else {
            // Multi-row parallel branch: fork -> vertical track -> merge
            const fork = `M ${mainX} ${originY} C ${mainX} ${originY - 8}, ${branchX} ${originY - 14}, ${branchX} ${originY - 18}`;
            const track = `L ${branchX} ${mergeY + 18}`;
            const merge = `C ${branchX} ${mergeY + 14}, ${mainX} ${mergeY + 8}, ${mainX} ${mergeY}`;

            computedPaths.push({
              d: `${fork} ${track} ${merge}`,
              color: branchColor,
              strokeWidth: 2,
            });
          }
        } else if (pr.status === "open" || pr.status === "rejected") {
          const strokeColor = pr.status === "rejected" ? "#f43f5e" : branchColor;

          if (dy <= ROW_HEIGHT) {
            const curve = `M ${mainX} ${originY} C ${mainX} ${originY - dy * 0.5}, ${branchX} ${nodeY + dy * 0.5}, ${branchX} ${nodeY}`;
            computedPaths.push({
              d: curve,
              color: strokeColor,
              dashed: true,
              strokeWidth: 2,
            });
          } else {
            const fork = `M ${mainX} ${originY} C ${mainX} ${originY - 8}, ${branchX} ${originY - 14}, ${branchX} ${originY - 18}`;
            const track = `L ${branchX} ${nodeY}`;
            computedPaths.push({
              d: `${fork} ${track}`,
              color: strokeColor,
              dashed: true,
              strokeWidth: 2,
            });
          }
        }
      }
    });

    const calculatedWidth = PADDING_LEFT + (maxLane + 1) * LANE_WIDTH + 10;
    const totalH = prs.length * ROW_HEIGHT;

    return {
      nodes: computedNodes,
      paths: computedPaths,
      totalHeight: totalH,
      svgWidth: Math.max(calculatedWidth, 54),
    };
  }, [prs]);

  if (prs.length === 0 && !loading) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center text-muted-foreground bg-card/40">
        <Sparkles className="h-8 w-8 mb-2 opacity-40" />
        <p className="text-sm font-medium">{t("noContributionsYet")}</p>
      </div>
    );
  }

  const mainX = PADDING_LEFT;

  return (
    <div className="relative rounded-xl border border-border/70 bg-card overflow-hidden shadow-2xs font-sans">
      {/* ── Continuous SVG Git Graph Track ──────────────── */}
      <svg
        className="pointer-events-none absolute left-0 top-0 select-none z-0"
        style={{ width: svgWidth, height: totalHeight }}
        width={svgWidth}
        height={totalHeight}
      >
        {/* Render Continuous Connector Paths */}
        {paths.map((path, i) => (
          <path
            key={i}
            d={path.d}
            fill="none"
            stroke={path.color}
            strokeWidth={path.strokeWidth || 2}
            strokeDasharray={path.dashed ? "3 3" : undefined}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeOpacity={0.85}
          />
        ))}

        {/* Render Commit Nodes Exactly on their tracks (Zero floating dots) */}
        {nodes.map((node) => {
          if (node.isApproved) {
            if (node.showMainBadge) {
              return (
                /* Double ring for latest merge head on main (Lane 0) */
                <g key={`main-head-${node.pr.id}`}>
                  <circle
                    cx={mainX}
                    cy={node.y}
                    r="5.5"
                    fill="var(--card)"
                    stroke="#a855f7"
                    strokeWidth="2"
                  />
                  <circle cx={mainX} cy={node.y} r="2.5" fill="#a855f7" />
                </g>
              );
            }

            if (node.isRevert) {
              return (
                /* Revert merge node on main (Lane 0) */
                <g key={`revert-${node.pr.id}`}>
                  <circle
                    cx={mainX}
                    cy={node.y}
                    r="4.5"
                    fill="var(--card)"
                    stroke="#06b6d4"
                    strokeWidth="2"
                  />
                  <circle cx={mainX} cy={node.y} r="2" fill="#06b6d4" />
                </g>
              );
            }

            /* Hollow merge ring on main (Lane 0) where the branch merges seamlessly */
            return (
              <circle
                key={`main-merge-${node.pr.id}`}
                cx={mainX}
                cy={node.y}
                r="4.5"
                fill="var(--card)"
                stroke={node.color}
                strokeWidth="2"
              />
            );
          }

          if (node.isOpen) {
            return (
              /* Open / Incoming commit node in its branch lane */
              <g key={`open-${node.pr.id}`}>
                <circle
                  cx={node.x}
                  cy={node.y}
                  r="4.5"
                  fill="var(--card)"
                  stroke={node.color}
                  strokeWidth="1.75"
                  strokeDasharray="2.5 2.5"
                />
                <circle cx={node.x} cy={node.y} r="1.5" fill={node.color} />
              </g>
            );
          }

          if (node.isRejected) {
            return (
              /* Rejected commit node in its branch lane */
              <circle
                key={`rejected-${node.pr.id}`}
                cx={node.x}
                cy={node.y}
                r="3.5"
                fill="#f43f5e"
                stroke="var(--card)"
                strokeWidth="1.5"
              />
            );
          }

          return null;
        })}
      </svg>

      {/* ── Commit Rows List ──────────────────────────────── */}
      <div className="divide-y divide-border/25 relative z-10">
        {nodes.map((node) => {
          const { pr, showMainBadge, showOriginBadge, isOpen, isRevert } = node;

          const rawPayload = (pr as unknown as { payload?: Array<Record<string, unknown>> }).payload;
          const derivedOpTypes = Array.isArray(rawPayload) && rawPayload.length > 0
            ? Array.from(new Set(rawPayload.map((op) => String(op.op || op.pr_type || "batch"))))
            : pr.summary_types && pr.summary_types.length > 0
              ? pr.summary_types
              : [pr.type];

          const initials = pr.author?.display_name
            ? pr.author.display_name
                .split(" ")
                .map((w) => w[0])
                .join("")
                .slice(0, 2)
                .toUpperCase()
            : "?";

          return (
            <div
              key={pr.id}
              style={{ minHeight: ROW_HEIGHT, paddingLeft: svgWidth + 4 }}
              className={cn(
                "group relative flex items-center justify-between gap-2.5 sm:gap-3.5 pr-2.5 sm:pr-3.5 py-1 text-xs transition-colors hover:bg-muted/35 dark:hover:bg-muted/20",
                loading && "opacity-60",
              )}
            >
              {/* Message / Title & Ref Badges */}
              <div className="min-w-0 flex-1 flex items-center gap-2 overflow-hidden">
                {/* Ref Badges (VS Code style) */}
                <div className="flex items-center gap-1.5 shrink-0">
                  {showOriginBadge && (
                    <span className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.2 text-[10px] font-mono font-medium bg-purple-500/15 text-purple-600 dark:text-purple-400 border border-purple-500/30">
                      <Cloud className="h-2.5 w-2.5" />
                      <span>origin/main</span>
                    </span>
                  )}
                  {showMainBadge && (
                    <span className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.2 text-[10px] font-mono font-medium bg-blue-500/15 text-blue-600 dark:text-blue-400 border border-blue-500/30">
                      <CircleDot className="h-2.5 w-2.5" />
                      <span>{t("branchMain")}</span>
                    </span>
                  )}
                  {isOpen && (
                    <span className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.2 text-[10px] font-mono font-medium bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      <span>{t("branchPending")}</span>
                    </span>
                  )}
                  {isRevert && (
                    <span className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.2 text-[10px] font-mono font-medium bg-cyan-500/15 text-cyan-600 dark:text-cyan-400 border border-cyan-500/30">
                      <Undo2 className="h-2.5 w-2.5" />
                      <span>{t("revert")}</span>
                    </span>
                  )}
                </div>

                {/* PR / Commit Title */}
                <Link
                  href={`/pull-requests/${pr.id}`}
                  className="font-normal text-foreground hover:text-primary transition-colors truncate max-w-xl"
                  title={pr.title}
                >
                  {pr.title}
                </Link>

                {/* Operation Chip */}
                {derivedOpTypes.slice(0, 1).map((st) => {
                  const Icon = OP_ICONS[st] ?? FilePlus;
                  const hasKey = typeof (t as unknown as { has?: (k: string) => boolean }).has === "function"
                    ? (t as unknown as { has: (k: string) => boolean }).has(`operations.${st}`)
                    : true;
                  const label = hasKey
                    ? t(`operations.${st}` as any)
                    : st.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
                  return (
                    <span
                      key={st}
                      className="hidden lg:inline-flex items-center gap-1 rounded-sm px-1.5 py-0.2 text-[10px] font-medium text-muted-foreground/80 bg-muted/50 border border-border/30 shrink-0"
                    >
                      <Icon className="h-2.5 w-2.5 opacity-70" />
                      <span>{label}</span>
                    </span>
                  );
                })}
              </div>

              {/* Author Column */}
              <div className="hidden sm:flex items-center gap-1.5 w-28 md:w-36 shrink-0">
                {pr.author?.id ? (
                  <Link
                    href={`/profile/${pr.author.id}`}
                    className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground truncate transition-colors"
                  >
                    <Avatar size="sm" className="h-4 w-4 shrink-0">
                      <AvatarFallback className="text-[8px] bg-muted font-bold">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <span className="truncate text-muted-foreground">
                      {pr.author.display_name}
                    </span>
                  </Link>
                ) : (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground truncate">
                    <Avatar size="sm" className="h-4 w-4 shrink-0">
                      <AvatarFallback className="text-[8px] bg-muted font-bold">
                        ?
                      </AvatarFallback>
                    </Avatar>
                    <span className="truncate">{t("anonymous")}</span>
                  </div>
                )}
              </div>

              {/* Relative Date Column */}
              <div className="hidden md:block text-right w-24 shrink-0 text-muted-foreground/70 text-[11px] truncate">
                {formatDistanceToNow(new Date(pr.created_at), {
                  addSuffix: true,
                  locale: dateLocale,
                })}
              </div>

              {/* Hash & Copy Column */}
              <div className="hidden sm:flex items-center justify-end w-20 shrink-0 text-right">
                <button
                  type="button"
                  onClick={(e) => copyHash(pr.id, e)}
                  className="font-mono text-[11px] text-muted-foreground/80 hover:text-foreground bg-muted/40 hover:bg-muted px-1.5 py-0.5 rounded transition-colors inline-flex items-center gap-1"
                  title={t("copyHash")}
                >
                  {copiedId === pr.id ? (
                    <Check className="h-2.5 w-2.5 text-emerald-500" />
                  ) : (
                    <Copy className="h-2.5 w-2.5 opacity-50 group-hover:opacity-100" />
                  )}
                  <span>{pr.id.slice(0, 7)}</span>
                </button>
              </div>

              {/* Action Chevron */}
              <Link
                href={`/pull-requests/${pr.id}`}
                className="shrink-0 p-0.5 text-muted-foreground/60 hover:text-foreground transition-colors"
                aria-label={t("view")}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          );
        })}
      </div>
    </div>
  );
}
