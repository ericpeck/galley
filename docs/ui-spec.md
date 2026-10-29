# Galley — UI spec

Status: draft for review. Built from the 18 approved mock PNGs (screens 3a, 4a, 4b, 5a, 5b, 6a–6m). Text only; no images are committed. Reference placeholders like `(ref: 6k)` mark where a mock would help, pending Eric's call on whether to commit reference PNGs.

Stack: Python + PySide6 (Qt 6). Galley targets macOS and Windows. It is a cross-platform Qt app with a print-inspired visual style. It is **not** a macOS-native app and must not be described as one. Everything platform-specific (menu bar, shortcuts, window chrome) sits behind a swappable layer (section 12).

Brand owner: Eric. Do not invent logos, taglines, icons, colors, or names beyond what is written here.

---

## 1. Purpose and layout overview

Galley formats, validates, and converts JSON and YAML. Users paste text or open a file in an **Input** pane, press **Format**, and get output in an **Output** pane, with exact error locations, path-aware search on output, Repair for near-valid input, and Convert from Python (never runs code).

Main window, top to bottom (ref: 6c):

1. **Title bar** — centered window title `Galley`. The mocks show `Formatter`; menus and window titles use Galley.
2. **Toolbar** (one row, ~56 px at 1x): left group of actions, `Find` and the **Format** button at the right end. Under Format, a small caption: `Editing <b>Input</b>` (names the pane that has focus / is the Format target).
3. **Pane area**: a horizontal `QSplitter` with two panes, Input (left) and Output (right), separated by a narrow **splitter rail** carrying a reset button and a `50 / 50` ratio readout. Layout is View › Stack Panes.
4. Each pane holds one document: **tab strip** (the pane title, `Input` / `Output`) with a format picker (`Input as` / `Output as`) on the right; the **editor** (gutter + text); a **pane footer** row.
5. **Status bar** (one row, full width): message on the left, conversion direction on the right (e.g. `JSON → JSON`, `JSON → YAML`, `YAML → JSON`).

Default split is 50/50 (Input / Output). The rail readout shows `50` over `50`. The mocks printed `40` over `60` while the panes looked near equal; the default ratio is 50/50.

---

## 2. Color roles (Broadsheet)

Measured from the PNGs (1x pixel sampling of the 2x mocks). "Confirmed" = given by Eric, re-measured identical in 6c. Two roles are adjusted from the sampled hex, same hue, so the pairs in the contrast notes meet 4.5:1: `accent` (`#0088B0` → `#0080A6`) and `text.muted` (`#605D5D` → `#5E5B5B`).

| Role | Hex | Where | Confidence |
|---|---|---|---|
| `window.bg` | `#F3F2F2` | window, toolbar, status bar, pane footers, menus, popovers, find bar | confirmed |
| `editor.bg` | `#FFFFFF` | light-mode sample of the editor text area. The editor uses palette Base, not this hex | confirmed |
| `gutter.bg` | `#EAE7E7` | light-mode sample of the line-number gutter (one step darker than the white page). The gutter uses palette AlternateBase; also the stale-output banner, `Stale` tag fill, and progress-bar track in the light samples | confirmed |
| `pane.edge` | `#7D7979` | 1 px border around panes; ~3.9:1 vs window | confirmed |
| `line.current` | `#DEDBDB` | current-line band in the editor | measured |
| `dialog.bg` | `#EAE9E9` | modal dialog surface | measured |
| `card.bg` | `#F3F2F2` | rows/cards inside dialogs, diff preview box | measured |
| `scrim` | result `#B5B2B2` over window | dims the window behind modals (approx. black at ~25–28%) | estimated |
| `text` | `#201E1D` | primary UI and editor text, JSON punctuation emphasis, headings | measured |
| `text.code.value` | `#2D2B2B` | numbers, `true`/`false`/`null` (shown bold) | measured |
| `text.muted` | `#5E5B5B` | captions, line numbers, `Ready`, counts, JSON punctuation (`{ } [ ] , :`) | adjusted |
| `accent` | `#0080A6` | Format button, primary buttons, focus ring (2 px), active segment, selected scope toggle, progress fill, links like `Show in input →` icons, find-match outline | adjusted |
| `accent.strong` | `#006786` | menu highlight row, info icon, link text on light bg (5.7:1) | measured |
| `accent.ink` | `#004961` | JSON/YAML **keys** in code, text on `accent.tint` | measured |
| `accent.tint` | `#E9F8FF` | active toolbar toggle (e.g. Find on), screen-label chip, diff "changed" chip | measured |
| `accent.disabled` | `#86BFD0` | Format button with no input. `round(0.45 × accent + 0.55 × window.bg)` per channel. Measured `#86C2D4` was that blend of `#0088B0` over `#F3F2F2`; the same blend of `#0080A6` over `#F3F2F2` is `#86BFD0` | adjusted |
| `selection` | `#99E0FF` | text selection and current find match fill | measured |
| `info.tint` | `#C2D6DC` | info note icon backing (6e inline note) | low |
| `string` (magenta) | `#AA0B56` | string values in code; also the **error** hue | measured |
| `error` | `#AA0B56` | error text, error gutter icon, wavy underline, removed-text strike | measured |
| `error.tint` | `#FFF1F4` | background of failing lines and "removed" chips | measured |
| `error.icon.ring` | `#EEC3D4` | soft ring behind the gutter error icon | low |
| `amber.tint` | `#F1E3BE` | 6k "source text kept as a string" highlight and `to string` margin tag | measured |
| `amber.ink` | `#6E4604` | text on `amber.tint` (6.5:1) | measured |
| window controls | `#FF5F57`, `#FEBC2E`, `#28C840` | macOS traffic lights only; come from the platform chrome layer, not the palette | n/a |

**Amber**: per the confirmed decision, amber is mixed from the palette's yellow. The mocks only show the mixed results (`#F1E3BE` fill, `#6E4604` ink). The base yellow itself does not appear as a flat swatch in any of the 18 mocks, so its hex is an open question. Do not use amber for errors; magenta means error everywhere else (6k).

### Contrast notes (WCAG 2.x)

- `text` on `editor.bg`: 16.6:1. `text.muted` `#5E5B5B` on `editor.bg`: 6.72:1; on `window.bg`: 6.02:1; on `gutter.bg` (line numbers): 5.47:1. Line numbers were `#605D5D` (4.39:1 on the old gutter `#D7D3D3`); the color is darkened along the same hue (0°, saturation about 1.6%) to `#5E5B5B`.
- `string`/`error` `#AA0B56` on `editor.bg`: 5.9:1; on `error.tint`: 6.6:1.
- `accent.ink` keys on `editor.bg`: 8.0:1.
- `amber.ink` on `amber.tint`: 6.5:1.
- `accent` `#0080A6` on `window.bg`: 4.05:1 — still under 4.5:1, so use it for non-text UI (icons, borders, focus ring) or large/bold text. White on `accent` (Format, primary buttons): 4.53:1 against `#FFFFFF`. The button-face accent was `#0088B0` (white contrast 4.08:1); it is darkened along the same hue (HSL 193.6°, saturation 1) to `#0080A6`.
- `accent.disabled` `#86BFD0` is the disabled Format face: `round(0.45 × #0080A6 + 0.55 × #F3F2F2)` per channel. White text on `#86BFD0` is 2.02:1. Disabled controls are exempt from the 4.5:1 text contrast target.
- `pane.edge` vs `window.bg`: 3.85:1 (meets 3:1 for UI component boundaries).

### Second cue rule (color never alone)

Every place color carries meaning also has an icon or words:

- Error lines: magenta tint **plus** a gutter error icon (`!` in a circle) **plus** wavy underline **plus** a plain-words message. Status bar error text is prefixed with the same icon and underlined as a link.
- Info notes: blue `i` icon plus text (6e, 6l, 6j banner).
- Diff preview: changed = blue chip, **and** the margin names the change (`quotes`, `True → true`, `None → null`, `tuple → list · comma`, `trailing comma`). Removed = a small marked glyph with strike, plus a legend row (`"a" changed`, `"a" removed`).
- 6k amber items: amber fill **plus** a margin tag with a quote icon and the words `to string`, plus a legend entry `"a" source text kept as a string`.
- Stale output: banner text plus a `Stale` tag with an icon on the tab.
- Edited output: `Edited` in the output footer (6f).
- Valid: check icon plus `Valid JSON` text in the status bar.
- Selected segments/scope toggles: filled accent **plus** checked state exposed to accessibility.

---

## 3. Type

Observed in the mocks (exact families not labeled; see Open questions):

- **UI font: the platform system font**, from `QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)`, set once on `QApplication`. Used for toolbar labels, tabs, menus, dialogs, status bar, empty-state text. Do not name the family and do not bundle a font. Weights seen: regular and bold (semibold/bold for tab titles, buttons, dialog titles, headings like `Convert from Python`).
- **Code font: monospace** for editor text, gutter numbers, find field, code snippets inside dialogs, inline code like `\u00e9`, `a.b[2]`. Has a slashed or dotted zero and distinct `l`/`1`.
- Approximate sizes at 1x (measured from 2x mocks, ±1 px):
  - Dialog title: ~17–18 px bold.
  - Empty-state heading (`Paste JSON or YAML, or drop a file here`, `Formatted output will appear here.`): ~16 px bold.
  - Format button label: ~14 px bold.
  - Body UI (toolbar, tabs, menus, dialog body, status bar): ~12–13 px; status bar / footer captions ~11 px.
  - Editor mono: ~12–13 px; line height ~15–16 px (31 lines fit in ~470 px).
  - Margin annotations in diff preview, `3 keys` / `3 items` fold hints: ~10–11 px, muted, fold hints italic.
- Code coloring: keys `accent.ink`; strings `string` magenta; numbers and literals `text.code.value` bold; punctuation `text.muted`. YAML uses the same colors as JSON (6e).
- View menu controls font, Bigger (⌘+), Smaller (⌘−), Actual Size (⌘0) for the editor.

---

## 4. Spacing and density

Measured at 1x, approximate:

- Base unit 4 px; most gaps are 8 / 12 / 16 px.
- Window outer padding around pane area: ~16 px left/right.
- Toolbar: ~56 px tall; buttons are icon + label, ~28 px tall, 8 px icon–label gap, ~24 px between groups. Undo/Redo sit in a shared rounded tray (slightly darker fill).
- Tab strip: ~30 px; one row with the pane title and the format picker.
- Gutter: ~50 px wide; numbers right-aligned with fold arrows (`▾`/`▸`) immediately right of the number.
- Editor: ~10 px left text inset.
- Pane footer: ~24 px; status bar: ~24 px.
- Dialogs: ~16 px padding; rows inside dialogs ~8 px padding on `card.bg`, 6–8 px gap between rows; buttons bottom-right, ~32 px tall.
- Splitter rail: 32 px wide, centered controls. The reset button is 24 px. Layout is View › Stack Panes.
- Corner radii: small (2–4 px) on buttons, fields, tabs; dialogs ~4–6 px. Print style: minimal shadows; dialogs have a soft shadow.

---

## 5. Components

### 5.1 Toolbar (ref: 3a)
Order, left to right: `Paste`, `Copy` | `Undo`, `Redo` (paired tray) | `Outdent`, `Indent` (icon only) | indent size dropdown (`2 spaces`) | `Sort keys` | `Minify` | `Expand all`, `Collapse all` (icon only) | `Validate` … spacer … `Find` | **Format**.
- Icon-only buttons need tooltips and accessible names.
- `Find` shows `accent.tint` background when the find bar is open (4a, 4b).
- Toolbar acts on the focused pane (named by `Editing <pane>` under Format).

### 5.2 Format button (ref: 6d bottom strip)
Three states:
- **No input**: label `{·} Format ⌘↵`, disabled face `#86BFD0` (45% of `accent` over `window.bg`). Only when there is nothing to format.
- **Ready**: full `accent`, white bold label, shortcut hint `⌘↵` (platform layer supplies `Ctrl+Return` on Windows; §12).
- **Working**: full color, small spinner, label `Formatting…`. Never looks disabled. Cancel lives in the status bar, not on the button.
Caption below: `Editing Input` / `Editing Output` (bold pane name).

### 5.3 Tab strip
- Galley v1 has no tabs. Each pane holds one document, and the strip has no close `×`, no new tab `+`, and no right-click menu.
- Left side of strip: the bold pane title (`Input`, `Output`).
- Right side of strip: `Input as` / `Output as` label + format dropdown (`JSON`, `YAML`).

### 5.4 Editor (QPlainTextEdit subclass)
- Background `editor.bg`; current line `line.current`.
- Syntax colors as in §3. Fold arrows in gutter; folded object shows `{ … }` then italic muted `3 keys`; arrays `3 items`.
- Breadcrumb bar on Output (when parsed): path icon, `root › order › customer › address › zip` (last segment bold), `Copy path` link at right. Shows `root` when cursor is at the top.
- Word wrap toggle (⌥Z on macOS, Alt+Z on Windows), Show Whitespace (⇧⌘8). Both bindings are swappable per platform (§6, §12).
- Right-click menu: `Undo`, `Redo` | `Cut`, `Copy`, `Paste`, `Select All`. Output's menu adds `Copy As ▸` right after `Copy`, and the fold commands at the end (#66).
- Inline notes: a note box inserted between lines (not over text), with icon + message (6a error, 6e info, 6l info).
- Horizontal scrollbar when wrap is off.

### 5.5 Gutter
`gutter.bg`, muted right-aligned numbers, fold arrows. Error lines: number turns magenta, error icon after the number, the whole line row tinted `error.tint`. Info lines: blue `i` icon.

### 5.6 Pane footer
- Input: `Paste`, `Clear` (accent links with icons; `Clear` disabled when empty) … right: count (`Empty`, `1 line · 318 characters`, `34 lines · 612 characters`, `6 lines · 3 problems`, `1 line · 4.8 MB`).
- Output: `Options` (opens popover 6h) … right: status (`34 lines`, `22 lines · 1 note`, `Edited`, `Last good result`, `1,284 lines so far`) then `Copy` (disabled when empty).

### 5.7 Status bar
Left: state message with icon. Examples: `Ready`; `✓ Valid JSON · 34 lines`; `✓ Valid JSON · formatted in 3 ms · 34 lines`; `Converted · 1 note`; `Cancelled. Showing the previous result.`; error link `! Expected a comma after "price": 4.5 (line 3, column 38)` + `‹ 1 of 3 ›` + `Repair…` button + `2 of 3 fixable`; Python hint: error link + `</> Convert from Python…` button; working: spinner + `Formatting… 4.8 MB` (+ `· writing output`, progress bar and `62%` only during write) + `✕ Cancel`.
Right: direction `JSON → JSON` etc.

### 5.8 Splitter and rail
`QSplitter(Qt.Horizontal)` holding Input pane widget and Output pane widget. Custom handle (32 px) shows: reset button (`↔`, tooltip and accessible name `Reset split`, calls `PaneSplitter.apply_even_split()`, disabled while the readout is already 50/50) and ratio readout `50` / `50` at 11 px. The readout's accessible name stays `50 / 50`. Dragging updates the readout. Double-click handle → reset to 50/50. Layout is View › Stack Panes, not a rail button.

### 5.9 Pane pop-out
Removed. Galley v1 has no pop-out windows; both panes stay in the main window.

### 5.10 Search (find bar)
- **Output find** (4a): field with search icon, `2 of 3`, prev/next arrows, scope segmented toggle `Keys` / `Values` / `Path`, close `×`. Search only, no Replace. Path scope uses dotted form with zero-based indexes (`order.items[2].price`), jumps to the match, and opens folded parents. Current match: `selection` fill with accent outline.
- **Input find and replace** (4b): plain text search; toggles inside the field: Regex `.*`, Match case `Aa`, Whole word `ab` (underlined); count, prev/next, close. Second row: replace field, `Replace`, `Replace All`. No Keys/Values/Path scope (raw input may not parse).
- During a large write (6d): find works as plain text; count reads `1 so far`; `Path` toggle greyed out with tooltip `Available when formatting finishes`.

### 5.11 Dialogs (modal, window-modal sheet-style centered over the window with scrim)
- Repair input (6b), Convert from Python (6i, 6j, 6k, 6l), Replace existing output? (6f). Details per screen in §7.
- Common: title bold; summary line with icon; rows on `card.bg` with `Line N, column M` left, snippet (mono) + explanation middle, `Show in input →` link right; legend row; footer buttons right-aligned, primary rightmost with check icon and focus ring.

### 5.12 Output options popover (6h)
Anchored to the `Options` footer button. Header `Output options`, right `Output · YAML`. Rows: Indent `2 | 4 | Tab`; Sort keys `☐ A–Z at every level` + `Comments may move.`; Non-ASCII `☐ Escape as \u00e9`; Line endings `LF | CRLF` + `CRLF is the Windows default`; section `YAML` (only when output is YAML): Quote style `Plain | Single | Double` + `Quotes are added only where needed`; Collections `Block | Flow`. Footer: `↺ Reset to defaults` link, `Applies on the next Format`.

### 5.13 Menus
App menu `Galley` (mocks labeled it `Formatter`), `File`, `Edit`, `View`, `Help`. There is no Window menu. Menu-bar placement is in §12.
- Edit (3a): Undo ⌘Z, Redo ⇧⌘Z | Cut ⌘X, Copy ⌘C, Copy As ▸, Paste ⌘V, Select All ⌘A | Find… ⌘F, Find and Replace… ⌥⌘F | Escape as String ⇧⌘E, Unescape String ⌘K ⌘E.
- Copy As ▸ (#66): `Original`, `Masked`, `Random fake`, with no shortcuts. `Masked` and `Random fake` show the masking help text as their tooltip and status tip. Each copies Output, or its selection, to the clipboard and leaves Output unchanged. Original copies the unmasked text, even when Output shows masked values. Masked replaces values under matched keys, and values that are an email or phone number, with `•••`. Random fake replaces the same values with new ones of the same type on every copy. The status bar names what was copied (`Copied original (unmasked)`, `Copied masked (N values hidden)`, `Copied random fake (N values replaced)`), or, on any failure, `Nothing copied. Galley couldn't start the copy. Try again.` (never the error itself). Disabled while Output is empty or a job is running. Masked and Random fake run on the worker unless the text is tiny, and show the status-bar spinner and then Cancel on Format's timings. Cancel or Esc stops the copy and shows `Copy canceled. Nothing copied.`
- View (4a): Font ▸, Bigger ⌘+, Smaller ⌘−, Actual Size ⌘0 | Word Wrap ⌥Z (checked), Show Whitespace ⇧⌘8 | Enter Full Screen ^⌘F.
- `File` includes `Open file…` (6c) and `Open Recent ▸` (#85: up to 10 files, newest first, file name only, with the parent folder when names match; a missing file stays listed, disabled, as `name.json (not found)` (after the folder when one shows) with the tooltip "File not found"; `Clear Menu` at the bottom). `Help` contents are not shown in the mocks (Open questions). Do not invent further File or Help items.

---

## 6. Global keyboard paths (macOS and Windows; see §12)

| Action | macOS | Windows |
|---|---|---|
| Format | ⌘↵ | Ctrl+Return |
| Paste | ⌘V | Ctrl+V |
| Find | ⌘F | Ctrl+F |
| Find and Replace | ⌥⌘F | Ctrl+H |
| Undo / Redo | ⌘Z / ⇧⌘Z | Ctrl+Z / Ctrl+Y |
| Escape / Unescape String | ⇧⌘E / ⌘K ⌘E | Ctrl+Shift+E / Ctrl+K, Ctrl+E |
| Bigger / Smaller / Actual | ⌘+ / ⌘− / ⌘0 | Ctrl+ / Ctrl− / Ctrl+0 |
| Word wrap | ⌥Z | Alt+Z |
| Show whitespace | ⇧⌘8 | Ctrl+Shift+8 |
| Stack Panes | ⌘\ (also ⇧⌘L) | Ctrl+\ (also Ctrl+Shift+L) |
| Fold | ⌥⌘[ | Ctrl+Shift+[ |
| Unfold | ⌥⌘] | Ctrl+Shift+] |
| Next / Previous Problem | ⌘' / ⇧⌘' (also F8 / ⇧F8) | F8 / Shift+F8 |
| Full screen | ^⌘F | F11 |
| Find next / prev | ↵ / ⇧↵ in field | same |
| Close find / popover / dialog | Esc | Esc |

Format on Windows is Ctrl+Return (portable text `Ctrl+Return`, the main Return key). Find and Replace is ⌥⌘F on macOS (portable `Ctrl+Alt+F`) and Ctrl+H on Windows and Linux, set by platform rather than with `QKeySequence.StandardKey.Replace`, which is ⌘H (the system Hide) on macOS. No shortcut uses Ctrl+Alt on Windows or Linux, because Windows treats AltGr as Ctrl+Alt and layouts such as German type `[`, `]`, `@` and `€` with AltGr (#95). Word Wrap is ⌥Z on macOS and Alt+Z on Windows, as in VS Code. Unescape String is ⌘K ⌘E on macOS and Ctrl+K, Ctrl+E on Windows. Stack Panes is ⌘\ on macOS and Ctrl+\ on Windows, with ⇧⌘L / Ctrl+Shift+L as a second key because `\` needs AltGr on German layouts; menus show the first. Fold and Unfold are Ctrl+Shift+[ and Ctrl+Shift+] on Windows, as in VS Code; macOS keeps ⌥⌘[ and ⌥⌘], because ⇧⌘[ and ⇧⌘] switch tabs there. Next Problem and Previous Problem (View menu) are F8 and Shift+F8 everywhere and wrap around; on macOS F8 is Play/Pause unless Fn is held, so the menu shows ⌘' and ⇧⌘' and F8 / ⇧F8 stay as second keys (#78). All are swappable in the platform layer (§12). ⌘W / Ctrl+W closes the window, and ⇧⌘W stays free.

Focus order in main window: toolbar → Input tab strip → Input editor → Input footer → splitter rail → Output tab strip → Output editor → Output footer → status bar actions.

---

## 7. Screen states

Each entry: what is on screen, behavior, interactions, keyboard. Mock IDs in parentheses.

### 3a — Toolbar trim, Edit menu open (ref: 3a)
- Empty app (as 6c) with Edit menu open, `Find and Replace…` highlighted (`accent.strong` row, white text).
- Purpose: documents the trimmed toolbar order (§5.1) and that Undo, Redo, Find also live in Edit, alongside Cut, Find and Replace, Escape/Unescape. Font, size, wrap, whitespace live in View (4a).
- Keyboard: menu navigable with arrows; shortcuts listed right-aligned in muted text.

### 4a — Output find (default), View menu open (ref: 4a)
- Input holds minified one-line JSON; Output holds formatted JSON with breadcrumb `… › items › [1] › price`, `Copy path`.
- Find bar open on Output: query `price`, `2 of 3`, scope `Keys` selected. Current match at line 23 highlighted. Line 8 folded (`{ … } 3 keys`); line 14 annotated `3 items`.
- View menu open.
- Path-scope variant: query `order.items[2].price`, `1 match`, `Path` selected; jumps and unfolds parents.
- Status bar: `✓ Valid JSON · formatted in 3 ms · 34 lines`. Output footer `34 lines`, Copy.
- Empty query: count hidden. No matches: show `No matches` in muted text (proposal, not in mocks) and keep field focused; do not color the field red.
- Keyboard: ⌘F opens/focuses; ↵/⇧↵ next/prev; Esc closes and returns focus to editor.

### 4b — Input find and replace (ref: 4b)
- Find bar on Input: `qty`, Match case + Whole word on (Regex off), `2 of 3`; replace row `quantity`, `Replace`, `Replace All`. Matches highlighted in the one-line input; current match `selection` fill with accent outline.
- Output shows breadcrumb `root`.
- Replace replaces current match and advances; Replace All is one undo step.
- Invalid regex: show inline error under the field with icon + text (proposal).
- Keyboard: ⌥⌘F opens with replace row; Tab moves find → replace → buttons.

### 5a, 5b — Pop-out windows (ref: 5a, 5b)
- Removed. Galley v1 has no pop-out windows, so these screens are not built.

### 6a — Invalid input: JSON and YAML (ref: 6a)
- Format runs a strict parse. On failure the input stays exactly as pasted.
- Each failing line: magenta gutter icon + `error.tint` row. Current error also gets wavy underline and an inline message box: `Expected a comma after "price": 4.5 (line 3, column 38). Repair can't place this one safely.`
- Status bar: error link (icon + underline), `‹ 1 of 3 ›` navigation, `Repair…` button, `2 of 3 fixable`.
- Input footer: `6 lines · 3 problems`.
- Output keeps the last good result at full strength (not dimmed), tab shows `Stale` tag (icon + word), and a banner: `Showing the last good result, from 10:39. Fix the input and press Format to update it.` Output footer `Last good result`.
- YAML variant (`deploy.yaml`, Input as YAML): line 4 tab indentation, visible tab glyph `→` underlined; message `Tab character used for indentation; YAML requires spaces (line 4, column 1)` with inline `Repair` button; status bar repeats message with `Repair`; direction `YAML → JSON`; footer `8 lines · 1 problem`.
- Keyboard: clicking/activating status error moves caret to line/column; ‹ › (and a shortcut, TBD) cycle errors.

### 6b — Repair preview (ref: 6b)
- Dialog `Repair input`. Summary: wrench icon `2 problems can be fixed safely. 1 needs you.`
- Section `✓ Will fix 2`: rows `Line 5, column 35` — `{"sku":"NB-A5","price":12,"qty":1},]` (removed comma marked) `Remove the trailing comma before ].`; `Line 7, column 1` — `'notes'` struck / `"notes"` added — `Use double quotes for the key.` Each with `Show in input →`.
- Section `✋ Needs you 1`: `Line 3, column 38` — `"price":4.5 "qty":3}` with wavy underline — `A comma is missing after "price": 4.5. Repair won't guess where it goes, so the line stays highlighted until you add it.`
- Legend: `removed` (strike, error tint), `added` (accent tint). Note: `↺ Nothing changes until you apply. Undo reverts both fixes in one step.`
- Buttons: `Cancel`, primary `✓ Apply 2 fixes` (default, focused).
- Apply changes only Will-fix items; one undo step. Show in input closes nothing? — proposal: scrolls the input behind the dialog and keeps the dialog open; confirm.
- Almost-JSON cards (detection banners in the input, each its own action):
  - Escaped JSON string `"{\"id\":1,...}"` → `This is JSON stored inside a string.` → `Unescape and format`.
  - Python dict printout → `This looks like a Python dictionary. Converting opens the same preview dialog (see 6i–6l).` → `</> Convert from Python…`.
  - One object per line → `Found 3 JSON objects, one per line.` → `Wrap lines in an array`.
  - Cut off partway → error icon `The text appears to end early. Some data may be missing. (line 1, column 31)`; italic `Nothing here is safe to fix. Repair lists it under Needs you and never fills in missing data.` No action button.

### 6c — Empty state (ref: 6c)
- Input editor has keyboard focus (accent focus ring, caret on line 1).
- Center: `Paste JSON or YAML, or drop a file here`, `⌨ ⌘V to paste · Ctrl+V on Windows`, primary `Paste`, secondary `Open file…`.
- Output: `Formatted output will appear here.`
- Format disabled (only state where it is). `Clear` and output `Copy` disabled. Input footer `Empty`. Status `Ready`. Direction `JSON → JSON`.
- Drag-over (not in mocks): accent outline on the input pane; drop loads the file into Input, after asking first when Input has unsaved text (#86).

### 6d — Large file: writing the output (ref: 6d)
- 4.8 MB minified file `orders-2026-10.json`, one line. Word wrap off; horizontal scrollbar. Input banner: `One 4.8 MB line. Word wrap is off for it so the editor stays fast.` + `{·} Pretty-print to edit`.
- Output banner: `Large file: syntax colors are off to keep things fast.` + link `Turn on anyway`.
- While output fills in: breadcrumb shows `Path  Available when formatting finishes` greyed; `Copy path` greyed; find works as plain text (`Kei Tanaka`, `1 so far`), `Path` scope greyed. Output footer `1,284 lines so far`, Copy greyed.
- Status: spinner `Formatting… 4.8 MB · writing output`, progress bar (accent fill on `gutter.bg` track) + `62%`, `✕ Cancel`. **Progress bar appears only while writing.**
- Format button in Working state.
- **Cancel** (ref: 6d cancel crop): rolls Output back to the result it had before Format was pressed; a half-written output is never left behind. Status: `ⓘ Cancelled. Showing the previous result.` Footer `34 lines`, Copy enabled.
- Esc in the main window while working = Cancel (proposal; confirm).

### 6e — JSON to YAML (ref: 6e)
- Input JSON, Output as YAML. YAML uses JSON colors, dash lists, folding by indentation.
- Line 10 `zip: "04101"` keeps quotes; gutter info icon; inline note `ⓘ Kept "04101" in quotes so YAML doesn't read it as the number 4101.` Non-1:1 conversions are info notes, never errors.
- Output focused (accent focus border). Breadcrumb `root › order › customer › address › zip`.
- Footers: `34 lines · 612 characters`; `22 lines · 1 note`. Status `✓ Converted · 1 note`. Direction `JSON → YAML`. Caption `Editing Output`.

### 6f — Replace existing output? (ref: 6f)
- Shown only when Format would overwrite Output the user edited (footer `Edited`).
- Title `Output has changes. Replace it?` Body `You edited Output after the last Format. Replacing it discards those edits.`
- Buttons: `Cancel` (text button, focused default, so Return never throws work away), `Replace`.
- No `Don't ask again`: it only took effect with `Format into a new tab`, which Galley v1 doesn't have.

### 6g — Tab menu, rename, unsaved changes (ref: 6g)
- Removed. Galley v1 has no tabs, so this screen is not built. Edited Output still shows `Edited` in the output footer (6f).

### 6h — Output options (ref: 6h)
- Popover from Output footer `Options` (button shows pressed state). Applies to Output. Indent, sort keys, non-ASCII escaping, line endings apply to any format; YAML section appears only when output is YAML. Changes apply on the next Format.
- Keyboard: Tab through segmented controls; arrows move within a segment; Space toggles checkboxes; Esc closes.

### 6i — Convert from Python: everything converts (ref: 6i)
- Trigger: input is a Python dict printout; input shows info note `This looks like a Python dictionary, not JSON.` with Convert button; status shows `! Not valid JSON: single-quoted string (line 2, column 3)` + `</> Convert from Python…`.
- Dialog `Convert from Python`: `</> Everything converts. 16 changes: quotes, True and None, a tuple, and 2 trailing commas.` Diff preview of resulting JSON with per-line margin labels (`quotes`, `True → true`, `None → null`, `tuple → list · comma`, `trailing comma`). Legend `"a" changed`, removed glyph `removed`; `↺ Undo reverts every change in one step.`
- Buttons `Cancel`, primary `✓ Apply 16 changes`.
- Output pane empty (`…will appear here.`), output Copy disabled.

### 6j — Convert from Python: needs you (ref: 6j)
- `! 3 items can't be converted. The other 19 changes are ready, but nothing applies until these are resolved.`
- `✋ Needs you 3`: `Line 5, column 17` `datetime(2026, 10, 14, 9, 30)` — `A datetime has no JSON equivalent.`; `Line 9, column 15` `Decimal('12.00')` — `A Decimal has no JSON equivalent.`; `Line 12, column 11` `get_rate()` — `Function calls can't be converted. The value only exists when the code runs.` Each `Show in input →`, snippets with wavy underline; same lines marked in input gutter.
- `Edit them in the input, or convert them to strings and review every change before applying.`
- No Apply. Buttons `Cancel`, secondary-emphasis `❝ Convert unsupported items to strings` (focused) → opens 6k.

### 6k — Convert from Python: unsupported items as strings (ref: 6k)
- `</> 22 changes. 3 items become strings holding their exact source text, tagged in the margin.`
- Preview lines 4, 9, 12: values `"datetime(2026, 10, 14, 9, 30)"`, `"Decimal('12.00')"`, `"get_rate()"` with `amber.tint` highlight and margin tag `❝ to string` (line 12 also `comma`). Each string holds the **exact source text**.
- Line 9 keeps the source's indentation, which aligns `"price"` under `"sku"` (one column past the `{` on line 8). This is deliberate: edits are in place on the user's text; do not re-indent.
- Legend: `"a" changed`, `"a" source text kept as a string` (amber), removed. `↺ Undo reverts every change in one step.`
- Buttons `← Back` (returns to 6j Needs you list), primary `✓ Apply 22 changes`.
- No magenta for these items: magenta means error.

### 6l — Convert from Python: not valid Python (ref: 6l)
- `! This isn't valid Python, so it can't be converted.` Single row: `Line 4, column 15` `<Customer object at 0x10a3f2b50>` underlined — `Unexpected <. A printed object like this doesn't include its data.` `Show in input →`.
- `Print the object's fields instead, then paste again, or edit the line in the input.`
- Only `Close` (primary). Nothing to apply.

### 6m — Formatting in progress: reading the input (ref: 6m)
- Right after Format on the 4.8 MB file. Parse can't report a percentage: status shows **spinner only** + `Formatting… 4.8 MB` + `✕ Cancel`. **No progress bar, no percentage.**
- Output pane center: spinner, `Formatting orders-2026-10.json…`, `Reading the input. The result appears here as it's written.`
- Format button Working. Input banner as in 6d. Small pastes finish too fast to show this (show only if work exceeds a threshold, e.g. ~300 ms — TBD). Next state: 6d.

### State matrix summary

| State | Where shown |
|---|---|
| Empty | 6c (input invite, output placeholder, Format disabled) |
| Loading | 6m (spinner only), 6d (writing: spinner + progress bar + %) |
| Error | 6a (input errors, stale output), 6l (not Python), 6j (needs you) |
| Success | 4a/4b status `Valid JSON`, 6e `Converted · 1 note`, 6i/6k apply |
| Cancelled | 6d crop (rolled back) |
| Conflict | 6f (edited output) |

---

## 8. Splitter behavior

Architecture:
- `MainWindow(QMainWindow)` central widget contains toolbar row, `QSplitter` with two **pane slots**, status bar.
- Each pane is a `PaneWidget` (tab strip + editors + footer). No `QDockWidget` anywhere.
- Persist window geometry and splitter ratio per session (QSettings).
- Splitter: min pane width ~240 px (proposal); readout updates live, rounded to whole percent. Reset returns the split to 50/50 and is disabled while the readout already says 50/50.

---

## 9. Convert from Python — implementation constraints (UI-relevant)

- Never executes code. Parse with `ast.parse` only; no `eval`, `exec`, `ast.literal_eval` side-paths that import anything.
- Edits the user's own text in place using ast node source positions (`lineno`, `col_offset`, `end_lineno`, `end_col_offset`). Each change is a span replacement (quotes, `True`/`False`/`None`, tuple brackets, trailing commas, unsupported → exact source slice as JSON string). No custom JSON writer that re-serializes the whole document; unchanged text keeps its original whitespace (why 6k line 9 stays aligned).
- All changes apply as one undo step.
- Syntax errors map to the single error row in 6l with ast line/column (1-based column in UI).

## 10. Performance

- No hard file-size cap.
- Large-file adaptations seen: wrap off for very long lines, syntax colors off with `Turn on anyway`, streamed output with `so far` counts, path features disabled until done.
- Thresholds (performance bands) are TBD by benchmarks. Open.
- Formatting runs off the UI thread (QThread / worker); UI stays responsive; Cancel always available.

---

## 11. Accessibility baseline

- Every icon-only control has an accessible name and tooltip (Outdent, Indent, Expand all, Collapse all, reset, find toggles `.*` `Aa` `ab`). Outdent, Indent, Expand all, and Collapse all use the action text as the accessible name, including while they are unbuilt; the Coming soon tooltip stays. Reset's accessible name is `Reset split`.
- Visible 2 px accent focus ring on panes, fields, and buttons; default button in dialogs has a ring.
- Full keyboard operation for every screen (§6, §7). Dialogs trap focus; Esc cancels (except 6l Close).
- Color never the only cue (§2).
- Error and status messages exposed via accessible descriptions; status changes announced (QAccessible events) for Format complete, errors found, cancel.
- Respect system text scaling where Qt allows; editor zoom via Bigger/Smaller.
- Contrast targets: 4.5:1 body text, 3:1 UI boundaries; known borderline items listed in §2.
- Reduced motion: spinners may be replaced with static icon + text when the platform requests reduced motion.

---

## 12. Platform-swappable layer

Isolate behind a `platform` module for macOS and Windows:
- **Menus**: a global menu bar on macOS, an in-window menu bar on Windows, same action objects. App menu `Galley` (mocks showed `Formatter`). About and Quit use `QAction.MenuRole`. There is no Window menu. The editor right-click menu is `Undo`, `Redo` | `Cut`, `Copy`, `Paste`, `Select All`, and Output's adds `Copy As ▸` (§5.4).
- **Shortcuts**: define actions with `QKeySequence.StandardKey` where one exists. Find and Replace is set by platform, not with `QKeySequence.StandardKey.Replace`: ⌥⌘F on macOS and Ctrl+H on Windows and Linux. Custom bindings are the §6 table. Display hints (`⌘↵` vs `Ctrl+Return`, `⌘V` vs `Ctrl+V`) come from this layer. Empty-state already shows both paste hints. Word Wrap is ⌥Z on macOS and Alt+Z on Windows. No shortcut uses Ctrl+Alt on Windows or Linux. Format on Windows is Ctrl+Return.
- **Window chrome**: title bar, traffic lights vs. Windows caption buttons, full-screen action (`^⌘F` vs F11), unified-title look. Keep the Broadsheet surfaces identical across platforms.
- **Defaults**: line endings default (LF; popover notes CRLF is the Windows default — whether default flips on Windows is open).
- **File dialogs, drag-and-drop, second-display placement** via Qt defaults per platform.
- Fonts: the UI uses the platform system font (`QFontDatabase.systemFont(GeneralFont)`). The editor keeps a generic monospace. Do not name a UI family and do not bundle a UI font.

---

## 13. What NOT to invent

- No new brand elements: no logo, icon set, mascot, tagline, or extra colors beyond §2. The empty state (6c) has no download icon.
- Do not call the app macOS-native.
- No `QDockWidget`. No tabs: each pane holds one document.
- No percentage bar during the parse/read step (6m). Progress bar only while writing (6d).
- No magenta for 6k converted items (amber only).
- No Replace on Output find.
- No re-indentation or custom JSON writer for Convert from Python; no code execution.
- No guessing missing data in Repair (cut-off input, missing comma stay in Needs you).
- No hard file-size limit; don't invent numeric performance bands.
- Don't add File or Help menu items, a Window menu or window commands, settings screens, onboarding, or themes (dark mode) beyond this spec.
- Don't rely on the older written summary (it is wrong about 6k, 6m, and docks).

---

## 14. Open questions

1. **Product name in chrome**: Resolved. Menus and window titles use `Galley`. The mocks showed `Formatter`.
2. **Fonts**: Resolved. The UI font is the platform system font (`QFontDatabase.systemFont(GeneralFont)`), set on `QApplication`. The editor stays monospace. Families are not named or bundled.
3. **Base yellow hex** that amber is mixed from; only the mixed tint `#F1E3BE` and ink `#6E4604` are visible.
4. **Indent guide depth colors**: Resolved. Galley v1 has no indent guides.
5. **White on accent**: Resolved. Button-face `accent` is `#0080A6` (same hue as measured `#0088B0`). White on `#0080A6` is 4.53:1 against `#FFFFFF` (was 4.08:1).
6. **Line numbers**: Resolved. The line-number color (`text.muted`) is `#5E5B5B` (same hue as measured `#605D5D`). Contrast on gutter `#EAE7E7` is 5.47:1.
7. **Default split**: Resolved. The default splitter ratio is 50/50. The mocks' readout said 40/60.
8. **Swap**: Resolved. The rail has no swap button. Reset (`↔`) calls `apply_even_split()` and is named `Reset split`. The readout's accessible name stays `50 / 50`.
9. **⌥⌘W conflict**: Resolved by #95. Word Wrap is ⌥Z on macOS and Alt+Z on Windows. Close Others was removed with Tabs.
10. File and Help menu contents. Window is resolved: there is no Window menu in v1 (§12).
11. Show in input from a dialog: keep dialog open and scroll the input, or close?
12. Quitting with edits — copy and flow not in mocks. Closing a dirty tab is resolved: Galley v1 has no tabs.
13. Performance bands and thresholds (wrap-off line length, colors-off size, when to show 6m) — TBD by benchmarks.
14. Whether to commit reference PNGs alongside this spec (currently text only).
15. Mock set count: the instructions list 18 PNGs including a separate 6d Cancel crop; in the received set the Cancel state appears inside the 6d image ("After Cancel while the output is filling in"). Confirm nothing is missing.
