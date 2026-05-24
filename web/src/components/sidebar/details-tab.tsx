"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  Folder,
  FileText,
  Download,
  Calendar,
  User,
  ExternalLink,
  Paperclip,
  Tag,
  HardDrive,
  Eye,
  ThumbsUp,
  Star,
  Loader2,
  Info,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  formatFileSize,
  getFileBadgeColor,
  getFileBadgeLabel,
} from "@/lib/file-utils";
import { apiFetch } from "@/lib/api-client";
import { ExpandableText } from "@/components/ui/expandable-text";
import { useUIStore, useBrowseRefreshStore, isGuest } from "@/lib/stores";
import { useAuth } from "@/hooks/use-auth";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { isRestrictedTarget } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*  Reusable primitives for a card-sectioned sidebar                          */
/* -------------------------------------------------------------------------- */

function SidebarSection({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border bg-muted/30 px-3.5 py-3 dark:bg-muted/10 ${className}`}
    >
      {children}
    </div>
  );
}

function MetaRow({
  icon: Icon,
  label,
  value,
  href,
}: {
  icon: React.ElementType;
  label: string;
  value: React.ReactNode;
  href?: string;
}) {
  const content = (
    <div className="flex items-center gap-2 text-sm min-w-0">
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="ml-auto text-right font-medium truncate min-w-0 flex-1 pl-2">
        {value}
      </span>
    </div>
  );

  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="group block transition-colors hover:text-primary"
      >
        {content}
      </a>
    );
  }

  return content;
}

/* -------------------------------------------------------------------------- */
/*  Difficulty indicator                                                       */
/* -------------------------------------------------------------------------- */

function DifficultyDots({ level }: { level: number }) {
  const clamped = Math.max(1, Math.min(5, level));
  const colors = [
    "bg-green-500",
    "bg-lime-500",
    "bg-yellow-500",
    "bg-orange-500",
    "bg-red-500",
  ];
  return (
    <div className="flex items-center gap-1">
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          className={`inline-block h-2 w-2 rounded-full transition-colors ${
            i < clamped ? colors[clamped - 1] : "bg-muted-foreground/20"
          }`}
        />
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  InteractionBar                                                             */
/* -------------------------------------------------------------------------- */

interface InteractionBarProps {
  targetId: string;
  targetType: "directory" | "material";
  initialIsLiked: boolean;
  initialIsFavourited: boolean;
  initialLikeCount: number;
  disabled?: boolean;
}

function InteractionBar({
  targetId,
  targetType,
  initialIsLiked,
  initialIsFavourited,
  initialLikeCount,
  disabled = false,
}: InteractionBarProps) {
  const [isLiked, setIsLiked] = useState(initialIsLiked);
  const [isFavourited, setIsFavourited] = useState(initialIsFavourited);
  const [likeCount, setLikeCount] = useState(initialLikeCount);
  const [isLiking, setIsLiking] = useState(false);
  const [isFavouriting, setIsFavouriting] = useState(false);
  const { updateSidebarData } = useUIStore();
  const { user } = useAuth();
  const t = useTranslations("Sidebar");
  const triggerBrowseRefresh = useBrowseRefreshStore(
    (s) => s.triggerBrowseRefresh,
  );

  useEffect(() => {
    setIsLiked(initialIsLiked);
    setIsFavourited(initialIsFavourited);
    setLikeCount(initialLikeCount);
  }, [targetId, initialIsLiked, initialIsFavourited, initialLikeCount]);

  // Guests are read-only: liking and favouriting are unavailable.
  if (isGuest(user)) return null;

  const handleLike = async () => {
    if (isLiking || disabled) return;
    const next = !isLiked;
    const nextCount = likeCount + (next ? 1 : -1);
    setIsLiked(next);
    setLikeCount(nextCount);
    setIsLiking(true);
    try {
      const endpoint =
        targetType === "material"
          ? `/materials/${targetId}/like`
          : `/directories/${targetId}/like`;
      await apiFetch(endpoint, { method: "POST" });
      updateSidebarData({ is_liked: next, like_count: nextCount });
      triggerBrowseRefresh();
    } catch {
      setIsLiked(!next);
      setLikeCount(likeCount);
      toast.error(t("failedToUpdateLike"));
    } finally {
      setIsLiking(false);
    }
  };

  const handleFavourite = async () => {
    if (isFavouriting || disabled) return;
    const next = !isFavourited;
    setIsFavourited(next);
    setIsFavouriting(true);
    try {
      const endpoint =
        targetType === "material"
          ? `/materials/${targetId}/favourite`
          : `/directories/${targetId}/favourite`;
      await apiFetch(endpoint, { method: "POST" });
      updateSidebarData({ is_favourited: next });
      triggerBrowseRefresh();
    } catch {
      setIsFavourited(!next);
      toast.error(t("failedToUpdateFavourite"));
    } finally {
      setIsFavouriting(false);
    }
  };

  return (
    <div className="grid grid-cols-2 gap-2">
      <button
        onClick={handleLike}
        disabled={isLiking || disabled}
        className={cn(
          "flex items-center justify-center gap-1.5 rounded-xl border px-3 py-2.5 text-sm font-medium transition-all active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60",
          isLiked
            ? "border-blue-300 bg-blue-50 text-blue-600 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400"
            : "border-border bg-muted/30 text-muted-foreground hover:border-blue-200 hover:bg-blue-50/50 hover:text-blue-500 dark:hover:border-blue-900 dark:hover:bg-blue-950/20 dark:hover:text-blue-400",
        )}
      >
        {isLiking ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <ThumbsUp
            className={cn(
              "h-4 w-4",
              isLiked && "fill-blue-500 dark:fill-blue-400",
            )}
          />
        )}
        <span>{isLiked ? t("liked") : t("like")}</span>
        <span className="text-xs font-normal opacity-70">· {likeCount}</span>
      </button>

      <button
        onClick={handleFavourite}
        disabled={isFavouriting || disabled}
        className={cn(
          "flex items-center justify-center gap-1.5 rounded-xl border px-3 py-2.5 text-sm font-medium transition-all active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60",
          isFavourited
            ? "border-amber-300 bg-amber-50 text-amber-600 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400"
            : "border-border bg-muted/30 text-muted-foreground hover:border-amber-200 hover:bg-amber-50/50 hover:text-amber-500 dark:hover:border-amber-900 dark:hover:bg-amber-950/20 dark:hover:text-amber-400",
        )}
      >
        {isFavouriting ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Star
            className={cn(
              "h-4 w-4",
              isFavourited && "fill-amber-400 dark:fill-amber-300",
            )}
          />
        )}
        <span>{isFavourited ? t("saved") : t("save")}</span>
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Directory details                                                          */
/* -------------------------------------------------------------------------- */

function DirectoryDetails({ data }: { data: Record<string, unknown> }) {
  const t = useTranslations("Sidebar");
  const name = String(data.name ?? "");
  const description = data.description ? String(data.description) : null;
  const dirType = String(data.type ?? "folder");
  const metadata = (data.metadata ?? {}) as Record<string, unknown>;
  const childDirCount = Number(data.child_directory_count ?? 0);
  const childMatCount = Number(data.child_material_count ?? 0);
  const totalCount = childDirCount + childMatCount;

  const isModule = dirType === "module";
  const code = metadata.code ? String(metadata.code) : null;
  const syllabusUrl = metadata.syllabus_url
    ? String(metadata.syllabus_url)
    : null;
  const difficulty = metadata.difficulty ? Number(metadata.difficulty) : null;
  const examFormatFileKey = metadata.exam_format_file_key
    ? String(metadata.exam_format_file_key)
    : null;
  const rawTags = (data.tags ?? []) as unknown[];
  const tags = Array.isArray(rawTags)
    ? rawTags.map(String).filter(Boolean)
    : [];

  const isLiked = Boolean(data.is_liked);
  const likeCount = Number(data.like_count ?? 0);
  const isFavourited = Boolean(data.is_favourited);
  const searchParams = useSearchParams();
  const isRestricted = isRestrictedTarget(String(data.id ?? ""), searchParams.get("preview_pr"));

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-start gap-3 min-w-0">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-blue-100 dark:bg-blue-900/40">
          <Folder className="h-6 w-6 text-blue-600 dark:text-blue-400" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold leading-tight break-all">{name}</h3>
          {code && (
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {code}
            </p>
          )}
          <Badge variant="outline" className="mt-1.5 text-xs capitalize">
            {isModule ? t("module") : t("folder")}
          </Badge>
        </div>
      </div>

      <InteractionBar
        targetId={String(data.id ?? "")}
        targetType="directory"
        initialIsLiked={isLiked}
        initialIsFavourited={isFavourited}
        initialLikeCount={likeCount}
        disabled={isRestricted}
      />

      {/* Description */}
      {description && (
        <ExpandableText
          text={description}
          threshold={180}
          clampedLines={4}
          className="text-sm text-muted-foreground leading-relaxed px-0.5"
        />
      )}

      {/* Metadata card */}
      <SidebarSection className="space-y-2">
        <MetaRow
          icon={FileText}
          label={t("items")}
          value={t("itemsCount", { count: totalCount })}
        />
        {isModule && difficulty !== null && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">{t("difficulty")}</span>
            <span className="ml-auto">
              <DifficultyDots level={difficulty} />
            </span>
          </div>
        )}
      </SidebarSection>

      {/* Links */}
      {isModule && (syllabusUrl || examFormatFileKey) && (
        <SidebarSection className="space-y-2">
          {syllabusUrl && (
            <a
              href={syllabusUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-primary hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {t("syllabus")}
            </a>
          )}
          {examFormatFileKey && (
            <a
              href={`/api/materials/download-by-key?key=${encodeURIComponent(examFormatFileKey)}`}
              className="flex items-center gap-2 text-sm text-primary hover:underline"
            >
              <FileText className="h-3.5 w-3.5" />
              {t("examFormat")}
            </a>
          )}
        </SidebarSection>
      )}

      {/* Tags */}
      {tags.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <Tag className="h-3 w-3" />
            {t("tags")}
          </div>
          <div className="flex flex-wrap gap-1">
            {tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">
                {tag}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Material details                                                           */
/* -------------------------------------------------------------------------- */

// Module-level cache keyed by authorId so repeated sidebar opens don't
// re-fetch the same user. In-flight requests are deduplicated.
const authorNameCache = new Map<string, string>();
const authorNameInflight = new Map<string, Promise<string>>();

function AuthorName({ authorId }: { authorId: string | null }) {
  const t = useTranslations("Sidebar");
  const [name, setName] = useState<string | null>(
    authorId ? (authorNameCache.get(authorId) ?? null) : null,
  );

  useEffect(() => {
    if (!authorId) return;
    const cached = authorNameCache.get(authorId);
    if (cached !== undefined) {
      if (name !== cached) {
        setName(cached);
      }
      return;
    }
    let active = true;
    let promise = authorNameInflight.get(authorId);
    if (!promise) {
      promise = apiFetch<{ display_name: string | null }>(`/users/${authorId}`)
        .then((u) => u.display_name ?? t("unknown"))
        .catch(() => t("unknown"))
        .then((resolved) => {
          authorNameCache.set(authorId, resolved);
          authorNameInflight.delete(authorId);
          return resolved;
        });
      authorNameInflight.set(authorId, promise);
    }
    promise.then((resolved) => {
      if (active) setName(resolved);
    });
    return () => {
      active = false;
    };
  }, [authorId, t]);

  if (!authorId) return <span>{t("deletedUser")}</span>;
  if (!name)
    return <span className="animate-pulse text-muted-foreground">…</span>;
  return (
    <Link
      href={`/profile/${authorId}`}
      className="text-primary hover:underline"
    >
      {name}
    </Link>
  );
}

function MaterialDetails({ data }: { data: Record<string, unknown> }) {
  const t = useTranslations("Sidebar");
  const id = String(data.id ?? "");
  const title = String(data.title ?? "");
  const description = data.description ? String(data.description) : null;
  const type = String(data.type ?? "other");
  const authorId = data.author_id ? String(data.author_id) : null;
  const downloadCount = Number(data.download_count ?? 0);
  const createdAt = data.created_at ? new Date(String(data.created_at)) : null;
  const parentMaterialId = data.parent_material_id
    ? String(data.parent_material_id)
    : null;
  const rawTags = (data.tags ?? []) as unknown[];
  const tags = Array.isArray(rawTags)
    ? rawTags.map(String).filter(Boolean)
    : [];
  const attachmentCount = Number(data.attachment_count ?? 0);

  const versionInfo = data.current_version_info as Record<
    string,
    unknown
  > | null;
  const fileSize = versionInfo ? Number(versionInfo.file_size ?? 0) : 0;
  const fileName = versionInfo ? String(versionInfo.file_name ?? "") : "";
  const fileMimeType = versionInfo
    ? String(versionInfo.file_mime_type ?? "")
    : "";

  const isLiked = Boolean(data.is_liked);
  const likeCount = Number(data.like_count ?? 0);
  const isFavourited = Boolean(data.is_favourited);
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const isRestricted = isRestrictedTarget(String(data.id ?? ""), searchParams.get("preview_pr"));

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-start gap-3 min-w-0">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted">
          <FileText className="h-6 w-6 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold leading-tight break-all">{title}</h3>
          <span
            className={`mt-1.5 inline-block rounded px-1.5 py-0.5 text-xs font-medium ${fileName || fileMimeType ? getFileBadgeColor(fileName, fileMimeType) : "bg-gray-100 text-gray-800 dark:bg-gray-800/40 dark:text-gray-300"}`}
          >
            {fileName || fileMimeType
              ? getFileBadgeLabel(fileName, fileMimeType)
              : type}
          </span>
        </div>
      </div>

      <InteractionBar
        targetId={id}
        targetType="material"
        initialIsLiked={isLiked}
        initialIsFavourited={isFavourited}
        initialLikeCount={likeCount}
        disabled={isRestricted}
      />

      {/* Description */}
      {description && (
        <ExpandableText
          text={description}
          threshold={180}
          clampedLines={4}
          className="text-sm text-muted-foreground leading-relaxed px-0.5"
        />
      )}

      {/* Metadata card */}
      <SidebarSection className="space-y-2.5">
        <MetaRow
          icon={User}
          label={t("author")}
          value={<AuthorName authorId={authorId} />}
        />
        {fileSize > 0 && (
          <MetaRow
            icon={HardDrive}
            label={t("size")}
            value={formatFileSize(fileSize)}
          />
        )}
        <MetaRow icon={Download} label={t("downloads")} value={downloadCount} />
        <MetaRow
          icon={Eye}
          label={t("totalViews")}
          value={
            <div className="flex items-center gap-1.5 justify-end">
              {Number(data.total_views ?? 0)}
              {Number(data.views_today ?? 0) > 0 && (
                <Badge
                  variant="outline"
                  className="h-4 px-1 text-[9px] font-bold border-orange-200 bg-orange-50 text-orange-600 dark:border-orange-900/50 dark:bg-orange-950/30 dark:text-orange-400"
                >
                  +{Number(data.views_today)} {t("today")}
                </Badge>
              )}
            </div>
          }
        />
        {createdAt && (
          <MetaRow
            icon={Calendar}
            label={t("created")}
            value={createdAt.toLocaleDateString()}
          />
        )}
      </SidebarSection>

      {/* Tags */}
      {tags.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <Tag className="h-3 w-3" />
            {t("tags")}
          </div>
          <div className="flex flex-wrap gap-1">
            {tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">
                {tag}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Attachments card */}
      {!parentMaterialId && (
        <Link
          href={`${(data.__path as string) || pathname}/attachments`}
          className="group flex items-center gap-3 rounded-lg border border-violet-200 bg-violet-50/50 px-3 py-3 transition-colors hover:bg-violet-100/70 dark:border-violet-800/50 dark:bg-violet-950/20 dark:hover:bg-violet-950/40"
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-violet-100 text-violet-600 dark:bg-violet-900/50 dark:text-violet-400">
            <Paperclip className="h-4.5 w-4.5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-violet-900 dark:text-violet-200">
                {t("attachments")}
              </span>
              {attachmentCount > 0 && (
                <Badge
                  variant="secondary"
                  className="h-5 px-1.5 text-[10px] font-semibold bg-violet-200 text-violet-700 dark:bg-violet-800 dark:text-violet-200"
                >
                  {attachmentCount}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {attachmentCount > 0
                ? t("supplementaryFilesCount", { count: attachmentCount })
                : t("addSupplementaryFiles")}
            </p>
          </div>
          <ExternalLink className="h-3.5 w-3.5 text-violet-400 opacity-0 transition-opacity group-hover:opacity-100" />
        </Link>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Export                                                                      */
/* -------------------------------------------------------------------------- */

interface DetailsTabProps {
  target: {
    type: "directory" | "material";
    id: string;
    data: Record<string, unknown>;
  } | null;
}

export function DetailsTab({ target }: DetailsTabProps) {
  const t = useTranslations("Sidebar");
  if (!target || !target.data) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
        <Info className="mb-3 h-8 w-8 opacity-20" />
        <p className="text-sm font-medium">{t("noItemSelected")}</p>
        <p className="text-xs">{t("selectItemToViewDetails")}</p>
      </div>
    );
  }

  if (target.type === "directory") {
    return <DirectoryDetails data={target.data} />;
  }

  return <MaterialDetails data={target.data} />;
}
