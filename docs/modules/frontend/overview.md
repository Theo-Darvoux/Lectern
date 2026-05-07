# Frontend Overview (`web/`)

## Tech Stack

| Technology | Purpose |
|-----------|---------|
| Next.js 15 (App Router) | Framework, SSR, routing |
| React 19 | UI library |
| TypeScript | Type safety |
| Tailwind CSS | Styling |
| next-themes | Theme management (Light/Dark/System) |
| Zustand | Client-side state management |
| shadcn/ui | UI component library |

## Internationalization (i18n)

WikINT uses **`next-intl`** for internationalization. Locale messages are stored in `web/messages/{locale}.json` (currently `en.json` and `fr.json`).

### Dynamic Language Switching (no page reload)

Language changes are applied **instantly client-side** without a full-page refresh via a custom `LocaleProvider`:

| File | Role |
|------|------|
| `src/components/locale-provider.tsx` | Client component holding `locale` + `messages` in React state. Wraps `NextIntlClientProvider` with live state. Exposes `changeLocale(newLocale)` via `LocaleContext`. |
| `src/hooks/use-change-locale.ts` | Thin hook: `const { locale, changeLocale, isPending } = useChangeLocale()` |
| `src/app/intl/[locale]/route.ts` | API route serving the message JSON for a given locale. Used by `changeLocale()` to fetch the new bundle client-side. **Placed under `/intl/` (not `/api/`) to avoid the `/api/:path*` backend proxy rewrite.** Responses are cached (`Cache-Control: public, max-age=3600`). |
| `src/i18n/request.ts` | Server-side locale resolution from the `NEXT_LOCALE` cookie (used on SSR / first load). |

### Tooling & Maintenance

To maintain translation quality and coverage, several scripts are available in the `web/` directory:

| Script | Command | Purpose |
|--------|---------|---------|
| `scripts/check-i18n.ts` | `pnpm i18n:check` | Compares `en.json` vs `fr.json` for missing/extra keys and scans for unused keys in the codebase. Handles namespaces and dynamic usages. |
| `scripts/scan-i18n.ts` | `pnpm i18n:scan` | Scans all `.tsx` files for hardcoded strings that are not yet translated. Outputs a detailed report. |

#### Flow on language change

1. User picks a new locale in `Settings → Language`.
2. `changeLocale(newLocale)` (from `useChangeLocale`) is called.
3. Cookie `NEXT_LOCALE` is updated so the next SSR request also picks up the new locale.
4. `fetch('/api/messages/{newLocale}')` retrieves the message bundle (served from the static import map in the API route; Turbopack-friendly).
5. `startTransition` swaps `locale` + `messages` state inside `LocaleProvider`.
6. `NextIntlClientProvider` re-renders with the new messages. All `useTranslations()` calls across the entire tree update atomically.
7. `document.documentElement.lang` is updated to keep the `<html lang>` attribute in sync.

#### Adding a new locale

1. Add `web/messages/{locale}.json` with all translation keys.
2. Import it in `src/app/api/messages/[locale]/route.ts` and add it to the `MESSAGES` map.
3. Add a `<SelectItem value="{locale}">` in `src/app/settings/page.tsx`.

## Theme Handling

The application supports Light, Dark, and System themes using `next-themes`.

- **Default Behavior**: On first visit, the application defaults to the **System** theme, matching the user's operating system preference.
- **Persistence**: User theme selections (Light or Dark) are persisted in `localStorage` and will override the system setting on subsequent visits.
- **Implementation**:
    - The `ThemeProvider` in `web/src/app/layout.tsx` is configured with `defaultTheme="system"` and `enableSystem={true}`.
    - Components use the `useTheme` hook from `next-themes` to access the current `theme` or the `resolvedTheme` (which is always either `light` or `dark`).
    - The **Settings** page (`app/settings/page.tsx`) provides a three-way toggle for users to explicitly choose their preference or return to system settings.

## App Router Structure

```
web/src/app/
├── layout.tsx                      # Root layout (providers, navbar, footer)
├── page.tsx                        # Home page (featured, popular, PRs, favourites)
├── popular/
│   └── page.tsx                    # "See all popular" with period tabs + pagination
├── login/
│   ├── page.tsx                    # Email input for OTP
│   └── verify/page.tsx             # OTP code entry + magic link handler
├── onboarding/page.tsx             # First-time user setup
├── browse/[[...path]]/page.tsx     # Main content browser (catch-all route)
├── pull-requests/
│   ├── page.tsx                    # PR list
│   ├── new/page.tsx                # PR creation wizard
│   └── [id]/page.tsx               # PR detail/review
├── profile/
│   ├── page.tsx                    # Current user profile
│   └── [id]/page.tsx               # Other user profile
├── notifications/page.tsx          # Notification center
├── settings/page.tsx               # User settings
├── privacy/page.tsx                # Privacy policy
├── moderator/
│   ├── layout.tsx                  # Moderator layout (role guard: moderator|bureau|vieux)
│   ├── page.tsx                    # Moderator dashboard (stats)
│   ├── flags/page.tsx              # Content moderation flags
│   ├── directories/page.tsx        # Directory view (read-only)
│   ├── pull-requests/page.tsx      # PR moderation queue
│   └── featured/page.tsx           # Featured items management
└── admin/
    ├── layout.tsx                  # Admin layout (role guard: bureau|vieux only)
    ├── page.tsx                    # Admin dashboard (links to sub-sections)
    ├── users/page.tsx              # User management (role changes, deletion)
    ├── dlq/page.tsx                # Dead letter queue (retry/dismiss failed jobs)
    ├── config/page.tsx             # Platform configuration (Phase 2 placeholder)
    ├── flags/page.tsx              # Redirects → /moderator/flags
    ├── pull-requests/page.tsx      # Redirects → /moderator/pull-requests
    ├── directories/page.tsx        # Redirects → /moderator/directories
    └── featured/page.tsx           # Redirects → /moderator/featured
```

### Key Route: Browse (`/browse/[[...path]]`)

The `[[...path]]` catch-all segment captures the full slug path (e.g., `/browse/math/linear-algebra/chapter-1`). The page:
1. Calls `GET /api/browse/{path}` with the joined slug segments
2. Renders either a `DirectoryListing` or `MaterialViewer` based on the response type
3. Shows breadcrumbs for navigation

## Component Architecture

### Layout Components
- `LayoutShell` — Main app shell with responsive sidebar
- `Navbar` — Top navigation with search, notifications, user menu. On mobile (`< md`), the Bell and Send/Contributions icons are hidden (`hidden md:flex` / `hidden md:block`) since those routes are covered by the bottom bar. On mobile the right side shows only a search icon button + the avatar (for logout/settings access). On `md+` (desktop/tablet) the full icon set is visible: Browse link, centred search bar, Send, Bell popover, and avatar with display name.
- `MobileBottomBar` — Bottom navigation for mobile with 5 tabs: **Home / Browse (Folder icon) / PRs / Notifications / Profile**. The PRs tab (Send icon, `/pull-requests`) was added to ensure the contributions flow is reachable on mobile without the top navbar. Features a top-edge pill indicator (`w-6 h-0.5 rounded-full bg-foreground`) on the active tab. Respects iOS safe-area insets via `style={{ paddingBottom: "env(safe-area-inset-bottom)" }}`, enabled by `viewportFit: "cover"` in `layout.tsx`.
- `Footer` — Site footer
- `AuthGuard` — Wraps authenticated pages, redirects to login if not authenticated
- `CookieBanner` — GDPR cookie consent

### Home Components (`components/home/`)

These components power the home page, the `/popular` page, and the admin featured management page.

#### Shared foundations
- `types.ts` — TypeScript interfaces shared across home components: `MaterialDetail`, `MaterialVersionInfo`, `FeaturedItem`, `PullRequestOut`, `HomeData`
- `file-type-display.tsx` — Pure utilities:
  - `getFileTypeStyle(fileName, mimeType)` — Returns `{ gradient, iconColorClass, Icon }` for Tailwind gradient backgrounds and Lucide icons keyed by file extension / MIME type. Covers PDF, images, video, audio, code, documents, spreadsheets, presentations, markdown, and e-books.
  - `getMaterialBrowsePath(material)` — Constructs `/browse/{directory_path}/{slug}` or `/browse/{slug}`.

#### Display components
- `SectionHeader` — Reusable section heading with optional subtitle and "See all →" link. Props: `title`, `subtitle?`, `seeAllHref?`, `seeAllLabel?`.
- `MaterialCard` — Card component for a single `MaterialDetail`. Width is `w-55` (220 px) with `flex-none` for horizontal-scroll contexts on mobile; at `sm+` the built-in `sm:w-full` class fills the grid column automatically in desktop/tablet grid layouts. Shows a 4:3 gradient preview area with file-type icon, file-type badge, title, filename, file size, directory path, view count, and like count. Hover lifts the card.
- `FeaturedSection` — Renders admin-curated featured materials. Empty array → renders nothing. Single item → full-width split hero card with gradient panel + content area. Multiple items → horizontally-scrollable row of `FeaturedScrollCard` components (300 px wide each). Each card shows gradient banner, "Featured" pill, title, description, tags, and a "View material" CTA.
- `PopularSection` — Responsive card display of up to 8 popular materials. **Mobile** (`< sm`): horizontally-scrollable flex row with a negative-margin viewport bleed trick (`-mx-4 px-4`). **Tablet/Desktop** (`sm+`): CSS grid — 3 columns at `sm`, 4 at `lg`, 5 at `xl` (implemented via `sm:grid sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5` on the same container). A "See all" dashed card is appended when `materials.length >= 8`. Supports `isLoading` skeleton state (4 placeholder cards).
- `FavouritesSection` — Same responsive layout as `PopularSection` (horizontal scroll on mobile, CSS grid on `sm+`) for the user's recently favourited materials. "See all" links to `/profile`. Hidden when empty.
- `RecentPRsSection` — Compact list card of open pull requests. Each row shows: status icon, title, author, time-ago, and "Open" badge. Hidden when empty. Supports `isLoading` skeleton rows.

#### Data flow — Home page (`app/page.tsx`)
1. Fetches `GET /api/home` on mount → populates `HomeData`.
2. Renders in order: welcome greeting (`Good morning/afternoon/evening, {name}!`), `FeaturedSection`, `PopularSection` (today), `PopularSection` (14 d), `RecentPRsSection`, `FavouritesSection`.
3. Loading state: skeleton placeholders for each section.
4. Error state: inline destructive banner.

#### Data flow — Popular page (`app/popular/page.tsx`)
- Uses `useSearchParams` (wrapped in `<Suspense>`) to read the `period` query param (`"today"` | `"14d"`).
- Tab switcher updates the URL via `router.replace` without a full navigation.
- Fetches `GET /api/home/popular?period={period}&limit=20&offset={offset}`.
- Renders a responsive grid: 2 cols (mobile) → 3 cols (sm) → 4 cols (md) → 5 cols (lg).
- "Load more" button appends the next page; a summary line is shown once all results are loaded.

#### Admin — Featured management (`app/admin/featured/page.tsx`)
- Lists all featured items from `GET /api/admin/featured`.
- Sorts by status (active → scheduled → expired) then by priority descending.
- **Add Featured dialog**: collects Material UUID, optional title/description overrides, `start_at`/`end_at` datetime inputs, and priority. Validates date ordering before submitting `POST /api/admin/featured`.
- **Delete**: confirmation dialog → `DELETE /api/admin/featured/{id}`.
- Status badges: animated green "Active" pill, blue "Scheduled", gray "Expired".
- Summary line at the bottom shows counts per status.
- Accessible via the **Featured** tab in the admin layout nav (Star icon).

### Browse Components
- `DirectoryListing` — Renders a directory's children (folders + materials)
- `DirectoryLineItem` — Single directory row
- `MaterialLineItem` — Single material row with type icon and metadata
- `MaterialViewer` — Container that selects the appropriate viewer component. It hides the main page scrollbar and footer while active to provide a focused viewing experience. Viewer height is responsive: `h-[calc(100vh-7rem)]` on mobile (subtracting 3.5 rem navbar + 3.5 rem bottom bar) and `h-[calc(100vh-3.5rem)]` on `md+`. On mobile, header actions (download, print, attachments, share) are surfaced via `ViewerFab` instead of the inline toolbar shown on desktop.
- `ViewerFab` — Mobile-only floating action button stack for viewer actions (Download, Print, Upload Attachment, Attachments link, Flag, Share). Positioned at `bottom-[5.5rem]` on mobile (safely above the bottom nav bar) and `bottom-6` on `md+`.
- `Breadcrumbs` — Slug-path based breadcrumb navigation
- `DirectoryOpenPRs` — Shows pending PRs affecting this directory. Supports discrete Previous/Next pagination (10 items per page) for folders with many open contributions.
- `EmptyDirectory` — Empty state with upload CTA

### Viewer Components (`components/viewers/`)
Each viewer handles a specific file type. All viewers that support zoom include `ZoomControls` in their `ViewerToolbar` and support pinch-to-zoom on touch screens and Ctrl+wheel on pointer devices via the `usePinchZoom` hook.
- `PDFViewer` — PDF rendering (pdf.js based). Zoom adjusts rendered page width. Supports Ctrl+= / Ctrl+- / Ctrl+0 keyboard shortcuts, Ctrl+wheel, and pinch-to-zoom.
- `ImageViewer` — Image display with zoom via CSS `transform: scale(zoom/100)`. Supports ZoomControls, Ctrl+wheel, and pinch-to-zoom (25%–500%).
- `VideoPlayer` — HTML5 video player
- `AudioPlayer` — HTML5 audio player
- `CodeViewer` — Syntax-highlighted code (highlight.js). Zoom adjusts `fontSize` on the `<pre>` element (50%–200%). Supports a large set of languages via the common bundle plus individually imported grammars: C/C++, Python, Java/Kotlin/Scala/Groovy, JavaScript/TypeScript, Rust, Go, Ruby, PHP, C#/F#/VB.NET, Swift, Dart, Haskell, OCaml, Elixir, Erlang, Clojure, Elm, Julia, Lua, Perl, Tcl, PowerShell, Bash/Shell, SQL, GraphQL, Protobuf, Nix, Nim, D, x86 ASM, CMake, TeX/LaTeX, Diff, and more. Languages without a dedicated HLJS grammar (Zig, V, HCL/Terraform, TOML) get best-effort highlighting via a close analogue.
- `MarkdownViewer` — Rendered markdown (with GFM, WikiLinks, Mermaid diagrams, and Obsidian callouts). Zoom adjusts `fontSize` on the prose container (50%–200%).
- `CsvViewer` — Paginated CSV table viewer. Zoom adjusts `fontSize` on the table container (50%–200%).
- `EpubViewer` — EPUB reader
- `DjvuViewer` — DjVu document viewer
- `OfficeViewer` — OnlyOffice integration for docx/xlsx/pptx
- `GenericViewer` — Fallback download-only view
- `FullscreenToggle` — Fullscreen mode control
- `ZoomControls` — Shared zoom toolbar cluster (ZoomOut | `N%` | ZoomIn) used across all zoomable viewers. Accepts `min`, `max`, `onZoomIn`, `onZoomOut`, `onReset`, and `disabled` props.

### PR Components (`components/pr/`)
- `PRList` — Paginated PR list with status filtering (Pending / Approved / Rejected / All). The tab bar uses `overflow-x-auto` with hidden scrollbar so tabs remain scrollable on narrow screens without wrapping.
- `PRCard` — Individual PR summary card
- `PRCreateWizard` — Multi-step PR creation flow
- `PRFileUpload` — File upload within PR context
- `PRDiffView` — Visual diff of PR operations
- `PRVoteButtons` — [REMOVED] Upvote/downvote controls
- `PRComments` — Threaded comment display
- `ReviewDrawer` — Side panel showing staged operations
- `UploadDrawer` — File upload progress panel
- `StagingFAB` — Floating action button showing staged operation count. Responsive position: `bottom-[4.5rem] right-4` on mobile (clears the 56 px bottom navigation bar by 16 px) and `bottom-6 right-6` on `md+`.
- `EditItemDialog` — Edit material/directory metadata
- `NewFolderDialog` — Create directory inline
- `FilePreview` — Preview of uploaded file before submission
- `GlobalDropZone` — Drag-and-drop file upload anywhere on the page
- `OperationRow` — Individual change row in the PR detail view, showing metadata changes and file diffs. Uses `ExpandableText` for descriptions.

### Sidebar Components (`components/sidebar/`)
- `SharedSidebar` — Sidebar framework with tab navigation. On desktop it renders `SidebarContent` directly inside the page's bounded container. On mobile it uses a custom Sheet built from Radix `Dialog` primitives (`SheetPortal` + `SheetOverlay` + `SheetPrimitive.Content`) with slide-in-from-right animation — no separate `FloatingPanel` or `GlobalFloatingSidebar` needed.
- `DetailsTab` — Material metadata display
- `AnnotationsTab` — Document annotations
- `EditsTab` — Version history
- `ActionsTab` — Download, flag, share actions
- `ChatTab` — Comments/discussion

#### Sidebar state model (`useUIStore`)
The sidebar target/tab live in a single Zustand store and are shared across desktop aside, mobile drawer, the FAB, and listing item buttons.

- `openSidebar(tab, target)` — opens the sidebar AND sets both tab and target. Use this when the user explicitly picks an item/tab (clicking "Details" on a list row, picking "Annotations" from the FAB, etc.).
- `setSidebarTarget(target)` — updates only the target. **Does not change the active tab** unless the new target is restricted (id starting with `$` for drafts), in which case a disabled tab (`chat`/`annotations`/`edits`) auto-falls back to `details`. This is the correct primitive for synchronisation effects (e.g. browse page seeding the current directory).
- `updateSidebarData(partial)` — shallow-merges into the current target's `data`. Used by interaction primitives (likes, favourites) so all surfaces (FAB, Details tab) read from a single source.
- `closeSidebar()` — closes the panel but preserves the target so the closing transition doesn't flicker into an empty state.

The browse page (`app/browse/[[...path]]/page.tsx`) seeds the directory target only on **path change** via a `seededPathRef`. Background refetches triggered by `triggerBrowseRefresh` (e.g. after a like/favourite) refresh the directory's metadata in place when the sidebar is still showing that directory, but never overwrite a child material/directory the user explicitly opened. This prevents the "sidebar jumps back to the parent directory after liking a child" regression.

The viewer FAB (`viewer-fab.tsx`) reads `is_liked`/`like_count` from `sidebarTarget.data` when the target points at the same material, so optimistic updates stay synchronised with the Details tab.

#### Layout fix: `absolute inset-0` on `TabsContent`
Radix `ScrollAreaViewport` only activates `overflow-y: scroll` once a `ResizeObserver` fires and detects `offsetHeight < scrollHeight`. When the viewport has no concrete CSS height (only flex/percentage chains), `offsetHeight` always equals `scrollHeight` and scrolling never activates. The fix is to give each `TabsContent` panel `position: absolute; inset: 0` inside a `relative flex-1 min-h-0` container — this provides a concrete, positioned height without relying on CSS percentage resolution through flex chains. Scroll-heavy tabs (`details`, `edits`) use `overflow-y-auto` directly on the `TabsContent`; flex-column tabs (`chat`, `annotations`, `actions`) use `flex flex-col`.

### Profile Components
- `ProfileView` — User profile display with activity tabs (Contributions, Materials, Annotations, Recently Viewed). The `TabsList` uses `overflow-x-auto` with a hidden scrollbar and `shrink-0` on each trigger to allow horizontal scrolling on narrow viewports. Tab panels have a minimum height of `min-h-[400px] sm:min-h-[600px]`.
- `ContributionList` — User's PRs and contributions
- `RecentlyViewed` — View history
- `ReputationBadge` — Visual reputation indicator

### Generic UI Components (`components/ui/`)
- `ExpandableText` — Text component that clamps to a specific number of lines with a "Show more" toggle. Uses `overflow-wrap: anywhere` to handle long strings without spaces.
- `TagInput` — Interactive tag selection and management component.
- `ConfirmDeleteDialog` — Standardized confirmation dialog for destructive actions.
- `Badge`, `Button`, `Dialog`, `Accordion`, etc. — Standardized shadcn/ui primitives.

## Responsive Design

WikINT follows a **mobile-first** responsive strategy using Tailwind CSS breakpoints. The key breakpoints in use are:

| Breakpoint | Value | Usage |
|-----------|-------|-------|
| _(none)_ | 0 px+ | Mobile base styles |
| `sm` | 640 px+ | Card grid layout, scroll → grid transitions |
| `md` | 768 px+ | Desktop nav (no bottom bar), viewer height, FAB positions |
| `lg` | 1024 px+ | Wider grid columns (4 cols), desktop sidebar |
| `xl` | 1280 px+ | Maximum grid columns (5 cols) |

### Viewport & Safe Areas
`layout.tsx` exports `viewport: Viewport = { viewportFit: "cover" }` which enables `env(safe-area-inset-*)` on iOS notched devices. `MobileBottomBar` uses `padding-bottom: env(safe-area-inset-bottom)` so its content clears the home indicator.

### Navbar Simplification on Mobile
On mobile (`< md = 768 px`) the top navbar is stripped to its essentials:
- **Removed**: Bell (Notifications) icon and Send (Contributions) icon — both are now primary tabs in the bottom bar.
- **Kept**: WikINT logo, search icon button (right-aligned), and the avatar dropdown (provides Logout and Settings which have no bottom-bar equivalent).

This prevents the duplication between the top navbar and the bottom navigation and keeps the mobile header uncluttered.

### Bottom Navigation Clearance
All fixed/floating elements that appear on mobile account for the 56 px (`h-14`) bottom navigation bar:
- `StagingFab`: `bottom-[4.5rem]` on mobile, `bottom-6` on `md+`
- `ViewerFab`: `bottom-[5.5rem]` on mobile, `bottom-6` on `md+`
- `CookieBanner`: `bottom-16` on mobile, `bottom-4` on `sm+`
- All page containers: `pb-20 sm:pb-6` or `pb-24 sm:pb-10` to prevent content from being obscured.

### Card Sections (Home / Popular / Favourites)
`PopularSection` and `FavouritesSection` switch layout based on screen width:
- **Mobile**: `display: flex` with `overflow-x: auto` — horizontal scroll row, cards at fixed `w-55` width
- **`sm+`**: `display: grid` (`sm:grid sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5`) — cards fill their columns via `sm:w-full`

`MaterialCard` encodes this dual behaviour directly: `w-55 flex-none sm:w-full`.

### Toolbar Compactness
Action toolbars in `DirectoryListing` (batch select + add-item) and tab bars in `PRList` hide text labels on mobile (using `hidden sm:inline` spans) while keeping icons visible, preventing overflow on narrow screens.

## State Management

### Interaction Feedback & State Synchronization

To ensure a "real-time" feel and avoid race conditions associated with backend persistence delays, interactive components (like `ChatTab` and `PRComments`) use immediate local state synchronization:
- Upon successful `POST`, `PATCH`, or `DELETE`, the local state is updated immediately using the API response.
- This avoids unnecessary or premature full-list re-fetches that might result in stale data flicker if the backend transaction is still committing.

### Zustand Stores

**`staging-store.ts`** — The most important client-side store. Persists staged PR operations to `localStorage`:
- `operations: StagedOperation[]` — Pending PR operations with timestamps
- `uploads: StagedUpload[]` — In-progress file uploads
- `reviewOpen: boolean` — Whether the review drawer is open
- Methods: `addOperation`, `removeOperation`, `updateOperation`, `clearOperations`, `purgeExpired`

Operations have a 24-hour expiry (matching the server-side upload cleanup). The store tracks `stagedAt` timestamps and provides helpers (`isExpired`, `isExpiringSoon`, `msUntilExpiry`) for the UI to show warnings.

**`stores.ts`** — Additional Zustand stores for UI state (sidebar visibility, viewer preferences)

**`selection-store.ts`** — Text selection state for the annotation system

### Auth State (`lib/auth-tokens.ts`, `hooks/use-auth.ts`)
- JWT access token stored in memory (not localStorage for security)
- Token refresh handled transparently by the API client
- `useAuth()` hook provides `user`, `login`, `logout`, `isAuthenticated`

## API Client (`lib/api-client.ts`)

Centralized fetch wrapper:
- Base URL configuration
- Automatic `Authorization: Bearer <token>` header injection
- Automatic token refresh on 401 responses
- Typed error handling (`ApiError` class)
- Request/response JSON serialization

## Upload Client (`lib/upload-client.ts`)

Handles the three-phase upload flow:
1. `POST /api/upload` with the file (streaming)
2. Open SSE connection to track processing progress
3. Return the final `file_key` and metadata

Supports progress callbacks (`onProgress`, `onStatusUpdate`) and `AbortController` for cancellation.

## Download System (`hooks/use-download.ts`)

The download system manages triggering file downloads from the browser without navigating away from the current page.

### Architecture

- **`useDownload()`** — Hook providing `downloadMaterial(id, version?)` and `isDownloading` state.
- **In-place Triggering** — To ensure a seamless UX, downloads are triggered using a hidden anchor element. This prevents the browser from opening a new tab, as the backend provides `Content-Disposition: attachment` headers in the presigned URL.

### Flow
1. Fetch a short-lived presigned download URL from the backend.
2. Create a temporary `<a>` element in the DOM.
3. Set `href` to the presigned URL.
4. Programmatically click the element to trigger the browser's download manager.
5. Cleanup the temporary element.

## Crypto Utils (`lib/crypto-utils.ts`)

Client-side SHA-256 hashing using the Web Crypto API. Used for:
- CAS deduplication checks (hash file before uploading to check if it already exists)
- Upload integrity verification

## File Utils (`lib/file-utils.ts`)

Utilities for file type detection, size formatting, icon selection, and extension validation. Mirrors the server-side MIME type registry so the UI can validate files before attempting upload.

## Upload Queue (`lib/upload-queue.ts`)

Manages multiple concurrent file uploads with:
- Queue size limits
- Retry logic
- Progress aggregation across multiple files
- Error isolation (one failed upload doesn't affect others)

## Print System (`lib/print-utils.ts`, `hooks/use-print.ts`)

The print system lets viewers send a formatted document to the browser's print dialog via a hidden iframe.

### Architecture

- **`isPrintable(viewerType)`** — returns true for `pdf`, `image`, `code`, `markdown`, `office`
- **`printInIframe(content, options)`** — creates a hidden `<iframe>`, writes HTML (or loads a blob URL for PDFs), calls `window.print()`, then removes the iframe
- **`usePrint({ viewerType, materialId, fileName, mimeType })`** — React hook that assembles the printable content per viewer type and drives the flow

### Per-viewer print strategies

| Viewer | Strategy |
|--------|----------|
| `pdf` | Fetch blob → create blob URL → load directly in iframe |
| `image` | Fetch blob → `<img>` centered in iframe |
| `code` | Fetch raw text → HTML-escape → `<pre><code>` in iframe |
| `markdown` | Capture rendered DOM HTML via registry → inject into iframe with prose + hljs CSS |
| `office` | Delegate to OnlyOffice's built-in print via `office-print-registry` |

### Markdown print registry (`lib/markdown-print-registry.ts`)

`MarkdownViewer` registers a getter on mount (keyed by `materialId`) that returns the `innerHTML` of its rendered prose container. `usePrint` calls this registry to obtain already-rendered HTML instead of re-processing the raw markdown source. This ensures the printed output exactly matches what the user sees, including syntax highlighting, callouts, tables, and Mermaid diagrams.

```
registerMarkdownPrint(materialId, () => proseRef.current?.innerHTML)  // in MarkdownViewer
getMarkdownContent(materialId)                                         // in usePrint
```

## SSE Client (`lib/sse-client.ts`)

Wrapper around `EventSource` for upload progress streaming:
- Automatic reconnection with backoff
- Token-based authentication via query parameter
- Event parsing and type-safe callback dispatch
- Timeout handling
