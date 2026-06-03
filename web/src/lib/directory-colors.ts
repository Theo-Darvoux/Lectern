export interface DirectoryColorDef {
  id: string;
  label: string;
  gradient: string;
  iconClass: string;
  swatchClass: string;
}

export const DIRECTORY_COLORS: DirectoryColorDef[] = [
  {
    id: "blue",
    label: "Blue",
    gradient: "from-blue-50 to-indigo-100 dark:from-blue-950/30 dark:to-indigo-900/20",
    iconClass: "text-blue-400",
    swatchClass: "bg-blue-400",
  },
  {
    id: "purple",
    label: "Purple",
    gradient: "from-purple-50 to-violet-100 dark:from-purple-950/30 dark:to-violet-900/20",
    iconClass: "text-purple-400",
    swatchClass: "bg-purple-400",
  },
  {
    id: "rose",
    label: "Rose",
    gradient: "from-rose-50 to-pink-100 dark:from-rose-950/30 dark:to-pink-900/20",
    iconClass: "text-rose-400",
    swatchClass: "bg-rose-400",
  },
  {
    id: "amber",
    label: "Amber",
    gradient: "from-amber-50 to-yellow-100 dark:from-amber-950/30 dark:to-yellow-900/20",
    iconClass: "text-amber-400",
    swatchClass: "bg-amber-400",
  },
  {
    id: "green",
    label: "Green",
    gradient: "from-green-50 to-emerald-100 dark:from-green-950/30 dark:to-emerald-900/20",
    iconClass: "text-green-400",
    swatchClass: "bg-green-400",
  },
  {
    id: "teal",
    label: "Teal",
    gradient: "from-teal-50 to-cyan-100 dark:from-teal-950/30 dark:to-cyan-900/20",
    iconClass: "text-teal-400",
    swatchClass: "bg-teal-400",
  },
  {
    id: "orange",
    label: "Orange",
    gradient: "from-orange-50 to-amber-100 dark:from-orange-950/30 dark:to-amber-900/20",
    iconClass: "text-orange-400",
    swatchClass: "bg-orange-400",
  },
  {
    id: "slate",
    label: "Slate",
    gradient: "from-slate-100 to-zinc-200 dark:from-slate-900/50 dark:to-zinc-900/40",
    iconClass: "text-slate-400",
    swatchClass: "bg-slate-400",
  },
];

const DEFAULT_COLOR = DIRECTORY_COLORS[0];

export function getDirectoryColor(id: string | null | undefined): DirectoryColorDef {
  if (!id) return DEFAULT_COLOR;
  return DIRECTORY_COLORS.find((c) => c.id === id) ?? DEFAULT_COLOR;
}
