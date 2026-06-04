export interface DirectoryColorDef {
  id: string;
  label: string;
  gradient: string;
  iconClass: string;
  swatchClass: string;
  /** Solid saturated color for the folder body (collage mode). */
  folderBodyClass: string;
  /** Slightly darker shade for the folder tab (collage mode). */
  folderTabClass: string;
}

export const DIRECTORY_COLORS: DirectoryColorDef[] = [
  {
    id: "blue",
    label: "Blue",
    gradient: "from-blue-50 to-indigo-100 dark:from-blue-950/30 dark:to-indigo-900/20",
    iconClass: "text-blue-400",
    swatchClass: "bg-blue-400",
    folderBodyClass: "bg-blue-300 dark:bg-blue-600",
    folderTabClass: "bg-blue-500 dark:bg-blue-800",
  },
  {
    id: "purple",
    label: "Purple",
    gradient: "from-purple-50 to-violet-100 dark:from-purple-950/30 dark:to-violet-900/20",
    iconClass: "text-purple-400",
    swatchClass: "bg-purple-400",
    folderBodyClass: "bg-violet-300 dark:bg-violet-600",
    folderTabClass: "bg-violet-500 dark:bg-violet-800",
  },
  {
    id: "rose",
    label: "Rose",
    gradient: "from-rose-50 to-pink-100 dark:from-rose-950/30 dark:to-pink-900/20",
    iconClass: "text-rose-400",
    swatchClass: "bg-rose-400",
    folderBodyClass: "bg-rose-300 dark:bg-rose-600",
    folderTabClass: "bg-rose-500 dark:bg-rose-800",
  },
  {
    id: "amber",
    label: "Amber",
    gradient: "from-amber-50 to-yellow-100 dark:from-amber-950/30 dark:to-yellow-900/20",
    iconClass: "text-amber-400",
    swatchClass: "bg-amber-400",
    folderBodyClass: "bg-amber-300 dark:bg-amber-600",
    folderTabClass: "bg-amber-500 dark:bg-amber-800",
  },
  {
    id: "green",
    label: "Green",
    gradient: "from-green-50 to-emerald-100 dark:from-green-950/30 dark:to-emerald-900/20",
    iconClass: "text-green-400",
    swatchClass: "bg-green-400",
    folderBodyClass: "bg-green-300 dark:bg-green-600",
    folderTabClass: "bg-green-500 dark:bg-green-800",
  },
  {
    id: "teal",
    label: "Teal",
    gradient: "from-teal-50 to-cyan-100 dark:from-teal-950/30 dark:to-cyan-900/20",
    iconClass: "text-teal-400",
    swatchClass: "bg-teal-400",
    folderBodyClass: "bg-teal-300 dark:bg-teal-600",
    folderTabClass: "bg-teal-500 dark:bg-teal-800",
  },
  {
    id: "orange",
    label: "Orange",
    gradient: "from-orange-50 to-amber-100 dark:from-orange-950/30 dark:to-amber-900/20",
    iconClass: "text-orange-400",
    swatchClass: "bg-orange-400",
    folderBodyClass: "bg-orange-300 dark:bg-orange-600",
    folderTabClass: "bg-orange-500 dark:bg-orange-800",
  },
  {
    id: "slate",
    label: "Slate",
    gradient: "from-slate-100 to-zinc-200 dark:from-slate-900/50 dark:to-zinc-900/40",
    iconClass: "text-slate-400",
    swatchClass: "bg-slate-400",
    folderBodyClass: "bg-slate-300 dark:bg-slate-600",
    folderTabClass: "bg-slate-400 dark:bg-slate-700",
  },
];

const DEFAULT_COLOR = DIRECTORY_COLORS[0];

export function getDirectoryColor(id: string | null | undefined): DirectoryColorDef {
  if (!id) return DEFAULT_COLOR;
  return DIRECTORY_COLORS.find((c) => c.id === id) ?? DEFAULT_COLOR;
}
