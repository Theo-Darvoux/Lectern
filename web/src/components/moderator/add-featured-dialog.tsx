"use client";

import { useEffect, useState } from "react";
import { Check, ChevronsUpDown, Loader2, Folder, FileText } from "lucide-react";
import { TYPE_ICONS, EXT_ICONS } from "@/lib/material-icons";
import { getFileExtension } from "@/lib/file-utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { useSearch } from "@/components/search/use-search";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useTranslations } from "next-intl";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import type { FeaturedItem } from "@/components/home/types";

function toLocalDateInput(isoString?: string | Date): string {
  const d = isoString ? new Date(isoString) : new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function ItemSearch({
  onSelect,
  selectedId,
  selectedTitle,
}: {
  onSelect: (id: string, title: string, type: "material" | "directory", path: string) => void;
  selectedId: string;
  selectedTitle: string;
}) {
  const t = useTranslations("Moderator.featured");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { results, loading } = useSearch(query);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal h-10 px-3"
        >
          <span className="truncate">
            {selectedTitle || <span className="text-muted-foreground">{t("dialog.searchPlaceholder")}</span>}
          </span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0 overflow-hidden flex flex-col" align="start">
        <Command shouldFilter={false} className="flex flex-col">
          <CommandInput
            placeholder={t("dialog.searchPlaceholder")}
            value={query}
            onValueChange={setQuery}
          />
          <CommandList className="max-h-[240px] overflow-y-auto" onWheel={(e) => e.stopPropagation()}>
            {loading && (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                <span className="ml-2 text-sm text-muted-foreground">{t("dialog.searchPlaceholder")}</span>
              </div>
            )}
            {!loading && results.length === 0 && query.length > 0 && <CommandEmpty>{t("dialog.noResults")}</CommandEmpty>}
            {!loading && results.length === 0 && query.length === 0 && <CommandEmpty className="py-6 text-muted-foreground">{t("dialog.startTyping")}</CommandEmpty>}
            <CommandGroup>
              {results.map((result) => {
                const title = result.title || result.name || result.file_name || "Untitled";
                const isDir = result.search_type === "directory";
                const extension = getFileExtension(title) || (result.browse_path ? getFileExtension(result.browse_path) : "");

                let Icon: React.ElementType = isDir ? Folder : FileText;
                if (!isDir) {
                  if (result.type && TYPE_ICONS[result.type]) {
                    Icon = TYPE_ICONS[result.type];
                  } else if (extension && EXT_ICONS[extension]) {
                    Icon = EXT_ICONS[extension];
                  }
                }

                const pathDisplay = result.browse_path ? result.browse_path.replace(/^\/browse/, "") || "/" : "/";

                return (
                  <CommandItem
                    key={result.id}
                    value={result.id}
                    onSelect={() => {
                      onSelect(result.id, title, result.search_type, pathDisplay);
                      setOpen(false);
                    }}
                    className="flex items-center gap-2 cursor-pointer"
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4 shrink-0",
                        selectedId === result.id ? "opacity-100" : "opacity-0"
                      )}
                    />
                    <Icon className={cn("h-4 w-4 shrink-0", isDir ? "text-primary" : "text-blue-500")} />
                    <div className="flex flex-col overflow-hidden flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium truncate">{title}</span>
                        <Badge variant="outline" className="text-[10px] px-1 py-0 h-4">
                          {isDir ? "Folder" : "File"}
                        </Badge>
                      </div>
                      <span className="text-[10px] text-muted-foreground font-mono truncate">{pathDisplay}</span>
                    </div>
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export interface AddFeaturedDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
  editItem?: FeaturedItem | null;
}

export function AddFeaturedDialog({ open, onOpenChange, onSuccess, editItem }: AddFeaturedDialogProps) {
  const t = useTranslations("Moderator.featured");
  const [itemId, setItemId] = useState("");
  const [itemType, setItemType] = useState<"material" | "directory">("material");
  const [selectedTitle, setSelectedTitle] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [titleOverride, setTitleOverride] = useState("");
  const [descOverride, setDescOverride] = useState("");
  const [startAt, setStartAt] = useState(toLocalDateInput());
  const [endAt, setEndAt] = useState("");
  const [priority, setPriority] = useState<number>(0);
  const [submitting, setSubmitting] = useState(false);

  const resetForm = () => {
    setItemId("");
    setItemType("material");
    setSelectedTitle("");
    setSelectedPath("");
    setTitleOverride("");
    setDescOverride("");
    setStartAt(toLocalDateInput());
    setEndAt("");
    setPriority(0);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) resetForm();
    onOpenChange(next);
  };

  useEffect(() => {
    if (open) {
      if (editItem) {
        if (editItem.material) {
          setItemId(editItem.material.id);
          setItemType("material");
          setSelectedTitle(editItem.material.title);
          setSelectedPath(
            editItem.material.directory_path
              ? `/${editItem.material.directory_path}/${editItem.material.slug}`
              : `/${editItem.material.slug}`
          );
        } else if (editItem.directory) {
          setItemId(editItem.directory.id);
          setItemType("directory");
          setSelectedTitle(editItem.directory.name);
          setSelectedPath(
            editItem.directory.full_path ? `/${editItem.directory.full_path}` : "/"
          );
        }
        setTitleOverride(editItem.title || "");
        setDescOverride(editItem.description || "");
        setStartAt(toLocalDateInput(editItem.start_at));
        setEndAt(toLocalDateInput(editItem.end_at));
        setPriority(editItem.priority);
      } else {
        resetForm();
      }
    }
  }, [open, editItem]);

  const handleSubmit = async () => {
    if (!itemId.trim()) {
      toast.error("Please select a file or folder to boost");
      return;
    }
    if (!startAt) { toast.error(t("errors.startRequired")); return; }
    if (!endAt) { toast.error(t("errors.endRequired")); return; }
    if (new Date(endAt) <= new Date(startAt)) { toast.error(t("errors.endAfterStart")); return; }

    setSubmitting(true);
    try {
      const startIso = new Date(`${startAt}T00:00:00`).toISOString();
      const endIso = new Date(`${endAt}T23:59:59`).toISOString();

      const payload: Record<string, unknown> = {
        start_at: startIso,
        end_at: endIso,
        priority,
      };

      if (itemType === "material") {
        payload.material_id = itemId.trim();
        payload.directory_id = null;
      } else {
        payload.directory_id = itemId.trim();
        payload.material_id = null;
      }

      payload.title = titleOverride.trim() ? titleOverride.trim() : null;
      payload.description = descOverride.trim() ? descOverride.trim() : null;

      if (editItem) {
        await apiFetch(`/moderator/featured/${editItem.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast.success("Successfully updated featured item");
      } else {
        await apiFetch("/moderator/featured", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast.success(t("success.added"));
      }

      handleOpenChange(false);
      onSuccess();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t("errors.addFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg flex flex-col max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>{editItem ? "Edit Featured Boost" : t("dialog.addTitle")}</DialogTitle>
          <DialogDescription>
            {editItem ? "Update the details for this featured boost" : t("dialog.addDesc")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 flex-1 overflow-y-auto min-h-0 pr-1 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="item-search">Search file or folder <span className="text-destructive" aria-hidden>*</span></Label>
            <ItemSearch
              selectedId={itemId}
              selectedTitle={selectedTitle}
              onSelect={(id, title, type, path) => {
                setItemId(id);
                setSelectedTitle(title);
                setItemType(type);
                setSelectedPath(path);
              }}
            />
            {selectedPath && (
              <p className="text-[10px] text-muted-foreground font-mono mt-1">
                Selected Path: {selectedPath} ({itemType === "material" ? "File" : "Folder"})
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="title-override">{t("dialog.titleOverride")}</Label>
            <Input
              id="title-override"
              placeholder={t("dialog.titleOverridePlaceholder")}
              value={titleOverride}
              onChange={(e) => setTitleOverride(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="desc-override">{t("dialog.descOverride")}</Label>
            <Textarea
              id="desc-override"
              placeholder={t("dialog.descOverridePlaceholder")}
              value={descOverride}
              onChange={(e) => setDescOverride(e.target.value)}
              rows={3}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="start-at">{t("dialog.startDate")} <span className="text-destructive" aria-hidden>*</span></Label>
              <Input id="start-at" type="date" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
              <p className="text-[10px] text-muted-foreground italic">{t("dialog.startsAt")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="end-at">{t("dialog.endDate")} <span className="text-destructive" aria-hidden>*</span></Label>
              <Input id="end-at" type="date" value={endAt} onChange={(e) => setEndAt(e.target.value)} />
              <p className="text-[10px] text-muted-foreground italic">{t("dialog.endsAt")}</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="priority">{t("dialog.priority")}</Label>
            <Input
              id="priority"
              type="number"
              min={0}
              placeholder="0"
              value={priority}
              onChange={(e) => setPriority(Math.max(0, parseInt(e.target.value) || 0))}
              className="w-32"
            />
            <p className="text-xs text-muted-foreground">{t("dialog.priorityDesc")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>{t("dialog.cancel")}</Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? (editItem ? "Updating..." : t("dialog.adding")) : (editItem ? "Save Changes" : t("addFeatured"))}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
