import {
  ArrowRight,
  ArrowUp,
  BadgeCheck,
  BookOpen,
  Braces,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  CircleHelp,
  Code2,
  Database,
  Download,
  EllipsisVertical,
  ExternalLink,
  FilePenLine,
  FileText,
  Gavel,
  HardDrive,
  Headset,
  Heart,
  HeartHandshake,
  Image,
  Languages,
  LockKeyhole,
  Menu,
  MessageCircle,
  Palette,
  Pencil,
  Phone,
  Plus,
  Reply,
  Settings,
  Shield,
  ShieldCheck,
  ShieldUser,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  UserRound,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";

// Keep the existing call-site names while bundling only the SVGs this app uses.
// This includes source badges, admin tabs and conditional action/navigation icons.
const icons = {
  account_tree: Workflow,
  add: Plus,
  admin_panel_settings: ShieldUser,
  arrow_forward: ArrowRight,
  arrow_upward: ArrowUp,
  auto_awesome: Sparkles,
  call: Phone,
  chat_bubble: MessageCircle,
  chevron_left: ChevronLeft,
  chevron_right: ChevronRight,
  close: X,
  code: Code2,
  data_object: Braces,
  database: Database,
  delete: Trash2,
  delete_forever: Trash2,
  description: FileText,
  download: Download,
  edit: Pencil,
  edit_note: FilePenLine,
  error: CircleAlert,
  expand_less: ChevronUp,
  expand_more: ChevronDown,
  favorite: Heart,
  gavel: Gavel,
  image: Image,
  local_police: BadgeCheck,
  lock: LockKeyhole,
  menu: Menu,
  menu_book: BookOpen,
  more_vert: EllipsisVertical,
  open_in_new: ExternalLink,
  palette: Palette,
  person: UserRound,
  reply: Reply,
  settings: Settings,
  shield: Shield,
  shield_with_heart: HeartHandshake,
  stop: Square,
  storage: HardDrive,
  support_agent: Headset,
  translate: Languages,
  tune: SlidersHorizontal,
  verified_user: ShieldCheck,
} satisfies Record<string, LucideIcon>;

interface MaterialIconProps {
  icon: string;
  size?: number;
  filled?: boolean;
  className?: string;
}

/** Local SVG adapter; decorative icons never add ligature text to button names. */
export default function MaterialIcon({
  icon,
  size = 24,
  filled = false,
  className = "",
}: MaterialIconProps) {
  const Icon = Object.hasOwn(icons, icon)
    ? icons[icon as keyof typeof icons]
    : CircleHelp;
  const dimension = Number.isFinite(size) && size > 0 ? size : 24;
  // Filling detailed icons would obscure inner strokes, such as a shield's check.
  const silhouette = icon === "favorite" || icon === "shield" || icon === "stop";

  return (
    <Icon
      size={dimension}
      className={`inline-block shrink-0 align-middle ${className}`.trim()}
      style={{ width: dimension, height: dimension }}
      strokeWidth={filled && !silhouette ? 2.25 : 2}
      fill={filled && silhouette ? "currentColor" : "none"}
      aria-hidden="true"
      focusable="false"
    />
  );
}
