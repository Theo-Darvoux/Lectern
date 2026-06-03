import {
  Folder,
  BookOpen,
  GraduationCap,
  FlaskConical,
  Code2,
  Calculator,
  Globe,
  Music,
  Film,
  Palette,
  Microscope,
  Trophy,
  Dumbbell,
  Scale,
  Stethoscope,
  Landmark,
  Lightbulb,
  Cpu,
  Database,
  Network,
  Shield,
  PenLine,
  BarChart2,
  Atom,
  Rocket,
  Wrench,
  Camera,
  Leaf,
  Cloud,
  type LucideIcon,
} from "lucide-react";

export interface DirectoryIconDef {
  id: string;
  label: string;
  Icon: LucideIcon;
}

export const DIRECTORY_ICONS: DirectoryIconDef[] = [
  { id: "book-open", label: "Book", Icon: BookOpen },
  { id: "graduation-cap", label: "Graduation", Icon: GraduationCap },
  { id: "flask-conical", label: "Lab", Icon: FlaskConical },
  { id: "code-2", label: "Code", Icon: Code2 },
  { id: "calculator", label: "Math", Icon: Calculator },
  { id: "globe", label: "Geography", Icon: Globe },
  { id: "music", label: "Music", Icon: Music },
  { id: "film", label: "Film", Icon: Film },
  { id: "palette", label: "Art", Icon: Palette },
  { id: "microscope", label: "Science", Icon: Microscope },
  { id: "trophy", label: "Trophy", Icon: Trophy },
  { id: "dumbbell", label: "Sport", Icon: Dumbbell },
  { id: "scale", label: "Law", Icon: Scale },
  { id: "stethoscope", label: "Medicine", Icon: Stethoscope },
  { id: "landmark", label: "History", Icon: Landmark },
  { id: "lightbulb", label: "Ideas", Icon: Lightbulb },
  { id: "cpu", label: "Computer Science", Icon: Cpu },
  { id: "database", label: "Data", Icon: Database },
  { id: "network", label: "Network", Icon: Network },
  { id: "shield", label: "Security", Icon: Shield },
  { id: "pen-line", label: "Writing", Icon: PenLine },
  { id: "bar-chart-2", label: "Statistics", Icon: BarChart2 },
  { id: "atom", label: "Physics", Icon: Atom },
  { id: "rocket", label: "Research", Icon: Rocket },
  { id: "wrench", label: "Engineering", Icon: Wrench },
  { id: "camera", label: "Photography", Icon: Camera },
  { id: "leaf", label: "Biology", Icon: Leaf },
  { id: "cloud", label: "Cloud", Icon: Cloud },
];

const FOLDER_DEFAULT: DirectoryIconDef = { id: "folder", label: "Folder", Icon: Folder };

export function getDirectoryIcon(id: string | null | undefined): DirectoryIconDef {
  if (!id) return FOLDER_DEFAULT;
  return DIRECTORY_ICONS.find((d) => d.id === id) ?? FOLDER_DEFAULT;
}
