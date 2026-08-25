/**
 * Builds the offline icon-pack data used by `<Icon>`.
 *
 * The theming engine speaks Lucide names as its canonical icon vocabulary
 * (see src/lib/theme/icon-registry.ts). This script resolves each of those
 * names onto a real icon in the Fluent and Material Symbols collections, then
 * writes out:
 *
 *   src/lib/theme/icon-data/<pack>.json  — a PRUNED Iconify collection holding
 *                                          only the icons we actually map
 *
 * Pruning matters: the full Fluent collection is 20k icons and the full
 * Material Symbols one 16k. We ship the ~150 we use, so the packs cost a few
 * tens of KB instead of several megabytes, and render with no network calls.
 *
 * Run with: npm run build:icons
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { getIcons } from "@iconify/utils";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const OUT_DIR = resolve(ROOT, "src/lib/theme/icon-data");

/**
 * Candidate base names per pack, in preference order. The first candidate that
 * exists in the collection wins, which keeps this file readable while still
 * failing loudly when a concept has no equivalent in a pack.
 *
 * Keys are Lucide component names — the vocabulary every call site already
 * uses.
 */
const MAP = {
  // ── Arrived with the CRM and Projects apps (merged 2026-08-07) ───────────
  // Without a mapping these render Lucide on every theme — graceful, but they
  // would be the only Lucide glyphs in a Fluent or Material screen.
  Kanban: { fluent: ["board"], material: ["view-kanban"] },
  IndianRupee: { fluent: ["currency-rupee-indian", "money"], material: ["currency-rupee"] },
  UserCheck: { fluent: ["person-available", "person-accounts"], material: ["how-to-reg", "person-check"] },
  CircleAlert: { fluent: ["error-circle"], material: ["error"] },
  Ban: { fluent: ["prohibited"], material: ["block"] },
  Car: { fluent: ["vehicle-car"], material: ["directions-car"] },
  Flame: { fluent: ["fire"], material: ["local-fire-department"] },
  Siren: { fluent: ["alert-urgent"], material: ["emergency", "siren"] },
  // ── Status / feedback ────────────────────────────────────────────────────
  Loader2: { fluent: ["spinner-ios"], material: ["progress-activity"] },
  Loader: { fluent: ["spinner-ios"], material: ["progress-activity"] },
  Check: { fluent: ["checkmark"], material: ["check"] },
  CheckCircle2: { fluent: ["checkmark-circle"], material: ["check-circle"] },
  CheckCircle: { fluent: ["checkmark-circle"], material: ["check-circle"] },
  X: { fluent: ["dismiss"], material: ["close"] },
  XCircle: { fluent: ["dismiss-circle"], material: ["cancel"] },
  AlertTriangle: { fluent: ["warning"], material: ["warning"] },
  TriangleAlert: { fluent: ["warning"], material: ["warning"] },
  AlertCircle: { fluent: ["error-circle"], material: ["error"] },
  Info: { fluent: ["info"], material: ["info"] },
  HelpCircle: { fluent: ["question-circle"], material: ["help"] },
  Bell: { fluent: ["alert"], material: ["notifications"] },
  Activity: { fluent: ["pulse"], material: ["monitoring", "timeline"] },

  // ── Editing / actions ────────────────────────────────────────────────────
  Plus: { fluent: ["add"], material: ["add"] },
  Minus: { fluent: ["subtract"], material: ["remove"] },
  Trash2: { fluent: ["delete"], material: ["delete"] },
  Trash: { fluent: ["delete"], material: ["delete"] },
  Pencil: { fluent: ["edit"], material: ["edit"] },
  PenLine: { fluent: ["edit-line-horizontal-3", "edit"], material: ["edit"] },
  Edit: { fluent: ["edit"], material: ["edit"] },
  Save: { fluent: ["save"], material: ["save"] },
  Copy: { fluent: ["copy"], material: ["content-copy"] },
  Download: { fluent: ["arrow-download"], material: ["download"] },
  Upload: { fluent: ["arrow-upload"], material: ["upload"] },
  Undo2: { fluent: ["arrow-undo"], material: ["undo"] },
  RotateCcw: { fluent: ["arrow-counterclockwise", "arrow-undo"], material: ["undo", "refresh"] },
  RefreshCw: { fluent: ["arrow-sync"], material: ["refresh"] },
  RefreshCcw: { fluent: ["arrow-sync"], material: ["refresh"] },
  Search: { fluent: ["search"], material: ["search"] },
  Filter: { fluent: ["filter"], material: ["filter-alt"] },
  SlidersHorizontal: { fluent: ["options"], material: ["tune"] },
  Settings: { fluent: ["settings"], material: ["settings"] },
  Settings2: { fluent: ["settings"], material: ["tune"] },
  Share2: { fluent: ["share"], material: ["share"] },
  Send: { fluent: ["send"], material: ["send"] },
  Eye: { fluent: ["eye"], material: ["visibility"] },
  EyeOff: { fluent: ["eye-off"], material: ["visibility-off"] },

  // ── Navigation / arrows ──────────────────────────────────────────────────
  ChevronDown: { fluent: ["chevron-down"], material: ["keyboard-arrow-down"] },
  ChevronUp: { fluent: ["chevron-up"], material: ["keyboard-arrow-up"] },
  ChevronLeft: { fluent: ["chevron-left"], material: ["keyboard-arrow-left"] },
  ChevronRight: { fluent: ["chevron-right"], material: ["keyboard-arrow-right"] },
  ArrowLeft: { fluent: ["arrow-left"], material: ["arrow-back"] },
  ArrowRight: { fluent: ["arrow-right"], material: ["arrow-forward"] },
  ArrowUp: { fluent: ["arrow-up"], material: ["arrow-upward"] },
  ArrowDown: { fluent: ["arrow-down"], material: ["arrow-downward"] },
  CornerDownLeft: { fluent: ["arrow-enter-left", "arrow-enter"], material: ["subdirectory-arrow-left"] },
  ExternalLink: { fluent: ["open"], material: ["open-in-new"] },
  MoreHorizontal: { fluent: ["more-horizontal"], material: ["more-horiz"] },
  MoreVertical: { fluent: ["more-vertical"], material: ["more-vert"] },
  Menu: { fluent: ["navigation"], material: ["menu"] },
  PanelLeft: { fluent: ["panel-left"], material: ["view-sidebar"] },
  Maximize2: { fluent: ["arrow-expand"], material: ["open-in-full"] },
  Minimize2: { fluent: ["arrow-minimize"], material: ["close-fullscreen"] },

  // ── Communication ────────────────────────────────────────────────────────
  Mail: { fluent: ["mail"], material: ["mail"] },
  MailOpen: { fluent: ["mail-read"], material: ["drafts"] },
  MailMinus: { fluent: ["mail-dismiss"], material: ["unsubscribe"] },
  Inbox: { fluent: ["mail-inbox"], material: ["inbox"] },
  Reply: { fluent: ["arrow-reply"], material: ["reply"] },
  ReplyAll: { fluent: ["arrow-reply-all"], material: ["reply-all"] },
  Forward: { fluent: ["arrow-forward"], material: ["forward"] },
  MessageCircle: { fluent: ["chat"], material: ["chat"] },
  MessageSquare: { fluent: ["chat"], material: ["chat-bubble"] },
  MessagesSquare: { fluent: ["chat-multiple"], material: ["forum"] },
  Phone: { fluent: ["call"], material: ["call"] },
  Paperclip: { fluent: ["attach"], material: ["attach-file"] },
  AtSign: { fluent: ["mention"], material: ["alternate-email"] },

  // ── People ───────────────────────────────────────────────────────────────
  User: { fluent: ["person"], material: ["person"] },
  Users: { fluent: ["people"], material: ["group"] },
  UserPlus: { fluent: ["person-add"], material: ["person-add"] },
  UserMinus: { fluent: ["person-subtract"], material: ["person-remove"] },
  Building2: { fluent: ["building"], material: ["apartment"] },

  // ── Files / content ──────────────────────────────────────────────────────
  File: { fluent: ["document"], material: ["draft"] },
  FileText: { fluent: ["document-text"], material: ["description"] },
  FileCode: { fluent: ["document-javascript", "code"], material: ["code"] },
  FileSpreadsheet: { fluent: ["document-table"], material: ["table-chart"] },
  FileImage: { fluent: ["image"], material: ["image"] },
  Folder: { fluent: ["folder"], material: ["folder"] },
  FolderOpen: { fluent: ["folder-open"], material: ["folder-open"] },
  FolderInput: { fluent: ["folder-arrow-right"], material: ["drive-file-move"] },
  FolderKanban: { fluent: ["board"], material: ["view-kanban"] },
  Archive: { fluent: ["archive"], material: ["archive"] },
  ArchiveRestore: { fluent: ["archive-arrow-back"], material: ["unarchive"] },
  Image: { fluent: ["image"], material: ["image"] },
  BookOpen: { fluent: ["book-open"], material: ["menu-book"] },
  StickyNote: { fluent: ["note"], material: ["sticky-note-2"] },
  ClipboardCheck: { fluent: ["clipboard-checkmark"], material: ["assignment-turned-in"] },

  // ── Layout ───────────────────────────────────────────────────────────────
  LayoutGrid: { fluent: ["grid"], material: ["grid-view"] },
  LayoutList: { fluent: ["apps-list"], material: ["view-list"] },
  LayoutDashboard: { fluent: ["board"], material: ["dashboard"] },
  Columns3: { fluent: ["column-triple"], material: ["view-column"] },
  ListChecks: { fluent: ["task-list-square-ltr"], material: ["checklist"] },
  ListTree: { fluent: ["text-bullet-list-tree"], material: ["account-tree"] },
  CheckSquare: { fluent: ["checkbox-checked"], material: ["check-box"] },
  Square: { fluent: ["square"], material: ["square"] },
  Circle: { fluent: ["circle"], material: ["circle"] },

  // ── Time ─────────────────────────────────────────────────────────────────
  Clock: { fluent: ["clock"], material: ["schedule"] },
  Timer: { fluent: ["timer"], material: ["timer"] },
  History: { fluent: ["history"], material: ["history"] },
  Calendar: { fluent: ["calendar"], material: ["calendar-today"] },
  CalendarDays: { fluent: ["calendar-ltr"], material: ["calendar-month"] },
  CalendarClock: { fluent: ["calendar-clock"], material: ["event-upcoming", "schedule"] },
  CalendarPlus: { fluent: ["calendar-add"], material: ["calendar-add-on"] },

  // ── Media ────────────────────────────────────────────────────────────────
  Play: { fluent: ["play"], material: ["play-arrow"] },
  Pause: { fluent: ["pause"], material: ["pause"] },
  Mic: { fluent: ["mic"], material: ["mic"] },
  MicOff: { fluent: ["mic-off"], material: ["mic-off"] },
  Video: { fluent: ["video"], material: ["videocam"] },
  Camera: { fluent: ["camera"], material: ["photo-camera"] },
  Volume2: { fluent: ["speaker-2"], material: ["volume-up"] },
  Radio: { fluent: ["radio-button"], material: ["radio-button-checked"] },

  // ── Systems / infra ──────────────────────────────────────────────────────
  Zap: { fluent: ["flash"], material: ["bolt"] },
  Cloud: { fluent: ["cloud"], material: ["cloud"] },
  HardDrive: { fluent: ["hard-drive"], material: ["hard-drive", "storage"] },
  Server: { fluent: ["server"], material: ["dns"] },
  Database: { fluent: ["database"], material: ["database"] },
  Cpu: { fluent: ["developer-board"], material: ["memory"] },
  Terminal: { fluent: ["window-console", "code"], material: ["terminal"] },
  Code: { fluent: ["code"], material: ["code"] },
  GitBranch: { fluent: ["branch"], material: ["fork-right"] },
  Workflow: { fluent: ["flowchart"], material: ["account-tree"] },
  Plug: { fluent: ["plug-connected"], material: ["power"] },
  Power: { fluent: ["power"], material: ["power-settings-new"] },
  Puzzle: { fluent: ["puzzle-piece"], material: ["extension"] },
  Boxes: { fluent: ["cube-multiple", "cube"], material: ["widgets"] },
  Package: { fluent: ["box"], material: ["inventory-2"] },
  Layers: { fluent: ["layer"], material: ["layers"] },
  Globe: { fluent: ["globe"], material: ["public"] },
  Link2: { fluent: ["link"], material: ["link"] },
  Link: { fluent: ["link"], material: ["link"] },
  Wrench: { fluent: ["wrench"], material: ["build"] },
  FlaskConical: { fluent: ["beaker"], material: ["science"] },
  Rocket: { fluent: ["rocket"], material: ["rocket-launch"] },
  Bot: { fluent: ["bot"], material: ["smart-toy"] },
  Brain: { fluent: ["brain-circuit"], material: ["psychology"] },
  Sparkles: { fluent: ["sparkle"], material: ["auto-awesome"] },
  Wand2: { fluent: ["wand"], material: ["auto-fix-high"] },
  Lightbulb: { fluent: ["lightbulb"], material: ["lightbulb"] },
  Target: { fluent: ["target"], material: ["target", "my-location"] },
  TrendingUp: { fluent: ["arrow-trending-lines", "arrow-trending"], material: ["trending-up"] },
  BarChart3: { fluent: ["data-bar-vertical"], material: ["bar-chart"] },
  Waves: { fluent: ["wifi-1", "pulse"], material: ["waves"] },
  Wind: { fluent: ["weather-squalls"], material: ["air"] },

  // ── Security ─────────────────────────────────────────────────────────────
  Lock: { fluent: ["lock-closed"], material: ["lock"] },
  Unlock: { fluent: ["lock-open"], material: ["lock-open"] },
  KeyRound: { fluent: ["key"], material: ["key"] },
  Shield: { fluent: ["shield"], material: ["shield"] },
  ShieldCheck: { fluent: ["shield-checkmark"], material: ["verified-user"] },
  ShieldAlert: { fluent: ["shield-error"], material: ["gpp-maybe"] },
  ShieldOff: { fluent: ["shield-dismiss"], material: ["gpp-bad"] },
  LogOut: { fluent: ["sign-out"], material: ["logout"] },
  LogIn: { fluent: ["arrow-enter"], material: ["login"] },

  // ── Misc chrome ──────────────────────────────────────────────────────────
  Star: { fluent: ["star"], material: ["star"] },
  Flag: { fluent: ["flag"], material: ["flag"] },
  Tag: { fluent: ["tag"], material: ["label"] },
  Tags: { fluent: ["tag-multiple"], material: ["label"] },
  Sun: { fluent: ["weather-sunny"], material: ["light-mode"] },
  Moon: { fluent: ["weather-moon"], material: ["dark-mode"] },
  Monitor: { fluent: ["desktop"], material: ["desktop-windows"] },
  Smartphone: { fluent: ["phone"], material: ["smartphone"] },
  Home: { fluent: ["home"], material: ["home"] },
  Command: { fluent: ["keyboard-shift"], material: ["keyboard-command-key"] },
  MapPin: { fluent: ["location"], material: ["location-on"] },
  Briefcase: { fluent: ["briefcase"], material: ["work"] },
  CreditCard: { fluent: ["payment"], material: ["credit-card"] },
  DollarSign: { fluent: ["currency-dollar-euro", "currency"], material: ["attach-money"] },
  ThumbsUp: { fluent: ["thumb-like"], material: ["thumb-up"] },
  Stethoscope: { fluent: ["stethoscope"], material: ["stethoscope"] },

  // ── Navigation icons (src/lib/nav.ts, src/lib/centers.ts) ────────────────
  // The sidebar is the most visible surface in the app, so every icon it can
  // render needs a mapping or the theme switch looks half-applied.
  Palette: { fluent: ["color"], material: ["palette"] },
  UserCog: { fluent: ["person-settings"], material: ["manage-accounts"] },
  UserSearch: { fluent: ["person-search"], material: ["person-search"] },
  PlusSquare: { fluent: ["add-square"], material: ["add-box"] },
  KanbanSquare: { fluent: ["board"], material: ["view-kanban"] },
  ClipboardList: { fluent: ["clipboard-task-list-ltr"], material: ["assignment"] },
  Cog: { fluent: ["settings"], material: ["settings"] },
  Network: { fluent: ["organization"], material: ["hub"] },
  BellRing: { fluent: ["alert-badge", "alert-on"], material: ["notifications-active"] },
  Megaphone: { fluent: ["megaphone"], material: ["campaign"] },
  Newspaper: { fluent: ["news"], material: ["newspaper"] },
  Handshake: { fluent: ["handshake"], material: ["handshake"] },
  Landmark: { fluent: ["building-bank"], material: ["account-balance"] },
  Receipt: { fluent: ["receipt"], material: ["receipt"] },
  Wallet: { fluent: ["wallet"], material: ["wallet"] },
  ShoppingCart: { fluent: ["cart"], material: ["shopping-cart"] },
  Truck: { fluent: ["vehicle-truck"], material: ["local-shipping"] },
  Factory: { fluent: ["building-factory"], material: ["factory"] },
  LifeBuoy: { fluent: ["person-support"], material: ["support"] },

  // ── Rich-text editor toolbar (SignatureEditor, notes) ────────────────────
  Bold: { fluent: ["text-bold"], material: ["format-bold"] },
  Italic: { fluent: ["text-italic"], material: ["format-italic"] },
  Underline: { fluent: ["text-underline"], material: ["format-underlined"] },
  Strikethrough: { fluent: ["text-strikethrough"], material: ["format-strikethrough"] },
  RemoveFormatting: { fluent: ["text-clear-formatting"], material: ["format-clear"] },
  AlignLeft: { fluent: ["text-align-left"], material: ["format-align-left"] },
  AlignCenter: { fluent: ["text-align-center"], material: ["format-align-center"] },
  AlignRight: { fluent: ["text-align-right"], material: ["format-align-right"] },
  List: { fluent: ["text-bullet-list-ltr"], material: ["format-list-bulleted"] },
  ListOrdered: { fluent: ["text-number-list-ltr"], material: ["format-list-numbered"] },
  Table: { fluent: ["table"], material: ["table"] },
  Redo2: { fluent: ["arrow-redo"], material: ["redo"] },
  Link2Off: { fluent: ["link-dismiss"], material: ["link-off"] },
  ImageOff: { fluent: ["image-off", "image-prohibited"], material: ["hide-image"] },
  Eraser: { fluent: ["eraser"], material: ["ink-eraser"] },
  PenTool: { fluent: ["pen"], material: ["draw"] },
  PencilLine: { fluent: ["edit"], material: ["edit"] },
  SquarePen: { fluent: ["edit"], material: ["edit-square"] },
  Code2: { fluent: ["code"], material: ["code"] },

  // ── Panels / layout affordances ──────────────────────────────────────────
  PanelLeftClose: { fluent: ["panel-left-contract"], material: ["left-panel-close"] },
  PanelLeftOpen: { fluent: ["panel-left-expand"], material: ["left-panel-open"] },
  PanelRight: { fluent: ["panel-right"], material: ["view-sidebar"] },
  Columns2: { fluent: ["column-double-compare", "column"], material: ["view-column-2", "view-column"] },
  Rows3: { fluent: ["row-triple"], material: ["table-rows"] },
  LayoutTemplate: { fluent: ["slide-layout"], material: ["dashboard"] },
  AppWindow: { fluent: ["window"], material: ["web-asset"] },
  GripVertical: { fluent: ["re-order-dots-vertical"], material: ["drag-indicator"] },
  StretchHorizontal: { fluent: ["auto-fit-width", "arrow-expand"], material: ["width-full", "height"] },
  ChevronsUpDown: { fluent: ["chevron-up-down"], material: ["unfold-more"] },

  // ── Sorting / filtering ──────────────────────────────────────────────────
  ListFilter: { fluent: ["filter"], material: ["filter-list"] },
  ArrowDownUp: { fluent: ["arrow-sort"], material: ["swap-vert"] },
  ArrowUpNarrowWide: { fluent: ["arrow-sort-up"], material: ["sort"] },
  ArrowDownWideNarrow: { fluent: ["arrow-sort-down"], material: ["sort"] },
  SearchX: { fluent: ["search-info", "dismiss-circle"], material: ["search-off"] },

  // ── Arrows / movement ────────────────────────────────────────────────────
  ArrowUpRight: { fluent: ["arrow-up-right"], material: ["north-east"] },
  ArrowDownRight: { fluent: ["arrow-down-right"], material: ["south-east"] },
  ArrowUpToLine: { fluent: ["arrow-export-up", "arrow-up"], material: ["vertical-align-top"] },
  CornerDownRight: { fluent: ["arrow-turn-right-down", "arrow-enter"], material: ["subdirectory-arrow-right"] },
  SkipForward: { fluent: ["next"], material: ["skip-next"] },
  RotateCw: { fluent: ["arrow-clockwise"], material: ["refresh"] },

  // ── Status / progress ────────────────────────────────────────────────────
  LoaderCircle: { fluent: ["spinner-ios"], material: ["progress-activity"] },
  Hourglass: { fluent: ["hourglass"], material: ["hourglass-empty"] },
  CheckCheck: { fluent: ["checkmark-starburst", "checkmark"], material: ["done-all"] },
  BadgeCheck: { fluent: ["checkmark-starburst"], material: ["verified"] },
  CircleDot: { fluent: ["radio-button"], material: ["radio-button-checked"] },
  MinusCircle: { fluent: ["subtract-circle"], material: ["do-not-disturb-on"] },
  PauseCircle: { fluent: ["pause-circle"], material: ["pause-circle"] },
  Gauge: { fluent: ["gauge"], material: ["speed"] },
  Milestone: { fluent: ["flag"], material: ["flag"] },
  ShieldX: { fluent: ["shield-dismiss"], material: ["gpp-bad"] },

  // ── Lists / tasks ────────────────────────────────────────────────────────
  ListTodo: { fluent: ["task-list-square-ltr"], material: ["checklist"] },
  ListPlus: { fluent: ["text-bullet-list-add"], material: ["playlist-add"] },
  CopyX: { fluent: ["clipboard-error", "copy"], material: ["content-paste-off"] },
  FolderPlus: { fluent: ["folder-add"], material: ["create-new-folder"] },
  FolderClosed: { fluent: ["folder"], material: ["folder"] },
  BookMarked: { fluent: ["book-star", "bookmark"], material: ["bookmark"] },
  Pin: { fluent: ["pin"], material: ["push-pin"] },
  PinOff: { fluent: ["pin-off"], material: ["keep-off"] },

  // ── Comms / calls ────────────────────────────────────────────────────────
  Mails: { fluent: ["mail-multiple"], material: ["mail"] },
  MessageSquareText: { fluent: ["comment-text", "chat"], material: ["chat"] },
  MessageCircleQuestion: { fluent: ["chat-help"], material: ["contact-support"] },
  PhoneIncoming: { fluent: ["call-inbound"], material: ["phone-callback"] },
  PhoneOff: { fluent: ["call-dismiss"], material: ["phone-disabled"] },
  Headphones: { fluent: ["headphones"], material: ["headphones"] },
  AudioLines: { fluent: ["sound-wave-circle", "speaker-2"], material: ["graphic-eq"] },

  // ── Misc ─────────────────────────────────────────────────────────────────
  UserRound: { fluent: ["person"], material: ["person"] },
  Keyboard: { fluent: ["keyboard"], material: ["keyboard"] },
  Printer: { fluent: ["print"], material: ["print"] },
  QrCode: { fluent: ["qr-code"], material: ["qr-code"] },
  Webhook: { fluent: ["link-square", "plug-connected"], material: ["webhook"] },
  GitFork: { fluent: ["branch-fork"], material: ["fork-right"] },
  Hammer: { fluent: ["hammer", "wrench"], material: ["hardware", "build"] },
  Box: { fluent: ["box"], material: ["inventory-2"] },
  Sliders: { fluent: ["options"], material: ["tune"] },
  BarChart2: { fluent: ["data-bar-vertical"], material: ["bar-chart"] },
  UploadCloud: { fluent: ["cloud-arrow-up"], material: ["cloud-upload"] },
  Coins: { fluent: ["money"], material: ["payments"] },
  Gem: { fluent: ["diamond"], material: ["diamond"] },
  Coffee: { fluent: ["drink-coffee"], material: ["coffee"] },
  Footprints: { fluent: ["shoe-print", "run"], material: ["footprint", "directions-walk"] },
  Mountain: { fluent: ["mountain-location-top", "mountain-trail"], material: ["landscape"] },
  DoorOpen: { fluent: ["door"], material: ["door-open"] },
  MoonStar: { fluent: ["weather-moon"], material: ["bedtime"] },
  CalendarX: { fluent: ["calendar-cancel"], material: ["event-busy"] },
  AlarmClockOff: { fluent: ["alert-off"], material: ["alarm-off"] },
  Battery: { fluent: ["battery-10"], material: ["battery-full"] },
  BatteryLow: { fluent: ["battery-2"], material: ["battery-2-bar"] },
  BatteryMedium: { fluent: ["battery-5"], material: ["battery-5-bar"] },
};

/**
 * Suffixes tried against each candidate base name.
 *
 * Fluent's UI standard is the 20px regular grid, with 24px as the common
 * fallback — `-regular` is Fluent's unfilled face.
 *
 * Material Symbols encodes TWO independent axes in one name: `outline` means
 * FILL 0 (unfilled) and its absence means FILL 1 (solid); `-rounded`/`-sharp`
 * pick the face, and a bare name is the default "Outlined" face. So
 * `inbox-rounded` is the SOLID rounded glyph and `inbox-outline-rounded` is
 * the unfilled one — a distinction easy to misread as face-only.
 *
 * Fill is ordered ahead of face here on purpose. A solid glyph paints its
 * interior in `currentColor` instead of letting the surface show through, so
 * on a light theme every sidebar icon reads as a black blob and no longer
 * matches Fluent, Lucide or Material 3's own FILL 0 default. A face mismatch
 * (rounded vs the default Outlined face) is invisible at 16–20px; a fill
 * mismatch is the first thing you see. Only two glyphs — `smartphone` and
 * `push-pin` — actually take the second rung; `checklist` and friends have no
 * outline variant at all and fall through to `-rounded` unchanged.
 */
const SUFFIXES = {
  fluent: ["-20-regular", "-24-regular", "-16-regular", "-28-regular", "-32-regular", "-48-regular"],
  material: ["-outline-rounded", "-outline", "-rounded", ""],
};

const COLLECTIONS = {
  fluent: JSON.parse(
    readFileSync(resolve(ROOT, "node_modules/@iconify-json/fluent/icons.json"), "utf8"),
  ),
  material: JSON.parse(
    readFileSync(
      resolve(ROOT, "node_modules/@iconify-json/material-symbols/icons.json"),
      "utf8",
    ),
  ),
};

/** Iconify prefix each pack publishes under. */
const PREFIX = { fluent: "fluent", material: "material-symbols" };

function exists(collection, name) {
  return Boolean(collection.icons?.[name] || collection.aliases?.[name]);
}

/** First `base + suffix` combination that exists in the collection. */
function resolveName(pack, bases) {
  const collection = COLLECTIONS[pack];
  for (const base of bases) {
    for (const suffix of SUFFIXES[pack]) {
      const candidate = `${base}${suffix}`;
      if (exists(collection, candidate)) return candidate;
    }
  }
  return null;
}

const registry = {};
const missing = [];

for (const [lucideName, candidates] of Object.entries(MAP)) {
  const entry = {};
  for (const pack of ["fluent", "material"]) {
    const resolved = resolveName(pack, candidates[pack] ?? []);
    if (resolved) entry[pack] = resolved;
    else missing.push(`${lucideName} → ${pack} (tried: ${(candidates[pack] ?? []).join(", ")})`);
  }
  if (Object.keys(entry).length > 0) registry[lucideName] = entry;
}

mkdirSync(OUT_DIR, { recursive: true });

// Pruned collections — only the icons the registry actually references.
// getIcons() follows aliases and parent chains, so the output is self-contained.
const summary = [];
for (const pack of ["fluent", "material"]) {
  const names = Object.values(registry)
    .map((e) => e[pack])
    .filter(Boolean);
  const pruned = getIcons(COLLECTIONS[pack], names);
  if (!pruned) throw new Error(`Failed to prune the ${pack} collection`);
  pruned.prefix = PREFIX[pack];
  const file = resolve(OUT_DIR, `${pack}.json`);
  const json = JSON.stringify(pruned);
  writeFileSync(file, `${json}\n`);
  summary.push(
    `  ${pack.padEnd(9)} ${String(names.length).padStart(3)} icons  ${(json.length / 1024).toFixed(1)} KB`,
  );
}

writeFileSync(
  resolve(OUT_DIR, "registry.json"),
  `${JSON.stringify(registry, null, 2)}\n`,
);

console.log(`Wrote ${Object.keys(registry).length} icon mappings to ${OUT_DIR}`);
console.log(summary.join("\n"));

if (missing.length > 0) {
  console.error(`\n${missing.length} unresolved mapping(s):`);
  for (const m of missing) console.error(`  ${m}`);
  process.exitCode = 1;
}
