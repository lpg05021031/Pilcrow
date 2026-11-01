# Stylesheets

Plain CSS, no preprocessor, no CSS-in-JS. `web/src/main.tsx` imports the files in this order, and the
order is the cascade's:

| File | Holds |
|---|---|
| `fonts.css` | The bundled Inter and JetBrains Mono faces (Latin only; CJK uses the system font). |
| `tokens.css` | Every design token, then the dark (`:root, [data-theme='dark']`) and light (`[data-theme='light']`) values. No other stylesheet holds a colour (the `#000` in a mask gradient is an alpha, not a colour); the app icon's own artwork (`ui/bits.tsx` AppIcon) is the one drawing with fixed hexes. |
| `base.css` | Elements and the shared controls: buttons, inputs, switches, badges, scrollbars, `.render-fault`. |
| `shell.css` | The window: the opening, the shell's columns, the offline and update bars, resizers, the sheets on the ground, per-platform type tuning. |
| `sidebar.css` | The sidebar: brand, page rows, sessions by folder, one-click new session, collapse and expand. |
| `session.css` | Around the conversation: the header and crumbs, the folder picker, the fresh session, MCP notices, the find bar. |
| `composer.css` | The composer, its suggestions, and the queue of held messages. |
| `menus.css` | Popovers, menus, the themed select, tooltips. |
| `dialogs.css` | Dialogs, the new-session dialog, the segmented control, the palette, the shortcuts sheet, toasts. |
| `settings.css` | Settings: layout and rows, the model picker, skills and MCP servers, memory, repositories. |
| `welcome.css` | The welcome page and the first run. |
| `overview.css` | The control room. |
| `inbox.css` | The inbox and schedules. |
| `helpers.css` | Helpers (HELPERS.md). |
| `transcript.css` | The conversation: messages, Markdown, tool cards, permission and question cards, workflows. |
| `panels.css` | The side panel's tabs: changes, files, terminal, preview, tasks, timeline, info; the file-history mark. |
| `dispatch.css` | The dispatch page (`dp-`). |
| `motion.css` | How things move: every `animation`, `transition` and `@keyframes` (each component's own, under the file that owns the component, then the choreography across them and the less-motion switch), the parts that exist only to move (segmented thumb, settings ink, sidebar selection), the lightbox, and the dark theme's two cross-cutting finishes (lit top edges, sheet wells). |

**One property, one file.** No selector sets the same property, or a shorthand and one of its
longhands, in two files, and no `@keyframes` name is defined twice. A component's motion goes in
`motion.css`, and only there; everything else about it goes in the file that owns the component.
`scripts/css-overlap.mjs` checks all three as part of `npm run lint`, and that every `@keyframes`
is played by something.

## Names

- **Feature prefix, then part**: `side-row`, `side-row-main`, `ov-tile`, `pal-input`, `ns-folder`,
  `tl-row-btn`, `dp-table`. One prefix per component; a new component picks a new prefix.
- **State is a separate class** on the element it describes: `.on` (chosen), `.active` (keyboard or
  current item), `.open`, `.live` (being written now), `.running`, `.done`, `.error`, `.stale`,
  `.unread`, `.focused`, and `.leaving` (an exit is playing, see below). ARIA state attributes are
  styled directly where they exist (`[aria-expanded='true']`).
- **Size modifiers** are plain classes on the base: `.btn.sm`, `.btn.xs`, `.seg.sm`, `.chip.tiny`.
- `.leaving` is set on the still copy that `lib/motion` (`useExitGhost`, `<Leave>`) leaves behind
  when a component unmounts; its rule in `motion.css` is the exit.

## Tokens

- **Surfaces**: `--canvas` (conversation), `--chrome` (sidebar, panel), `--raised` (composer, cards),
  `--raised-2`, `--inset` (code, fields), `--card-ask` (cards that wait on you); `--glass` and
  `--glass-2` for floating panes over `--glass-blur`.
- **Lines and fills**: `--hair`, `--hair-2`, `--hair-3`; `--fill-1…3` for hover and selection.
- **Text**: `--text`, `--text-2`, `--text-3`, `--text-4`, from primary to faintest.
- **Meaning**: `--accent` (primary action, attention, focus), `--ok`, `--err`, `--info`, `--warn`,
  `--plan`, `--think` (reasoning only), each with a `-wash`. Orange or red that carries words or
  figures is `--accent-solid` (`-2` on hover) or `--err-solid`, which white (`--accent-ink`) meets
  4.5:1 on; `--accent` itself is for marks, focus and the icon-only send button.
- **Spacing**: `--sp-N` is N × 4px, with `_5` half steps (`--sp-1_5` = 6px) and, up to 13px, `_25`
  and `_75` quarter steps for the odd pixels that centre 13px type in an even box (`--sp-2_25` = 9px).
  What stays a number is an optical correction: 1px (a hairline's nudge), a negative margin (it
  undoes a padding rather than adds a step), and an inset that lines text up with an icon's column.
- **Type**: `--fs-tiny` 10 … `--fs-display` 22; a `-plus` step is half a pixel up for faces that run
  small (code, keycaps, counters). `--font-size` is the reading size the person sets.
- **Radii**: `--r-1…7` are 4 to 16 in steps of 2, and `--r-pill`. A radius off the scale follows
  from its frame (an inner box's is its frame's less the inset) and stays a number; a radius that is
  half the box's height is the pill.
- **Stacking**: `--z-float` inside a pane, then `--z-boot`, `--z-dialog`, `--z-popover`, `--z-toast`,
  `--z-lightbox`, `--z-ghost`, `--z-tip`. Inside a component, `z-index` 1 and 2 only order siblings.
- **Motion**: `--t-*` durations with `--ease*`, and the sampled springs `--spring-soft|snappy|bouncy`
  with their settle times (`-t`), always used as a pair. A duration that is not one of these is
  choreography of one piece (the beats of the opening, the step of a stagger, a loop's period) and is
  a number where it plays, the same rule as an optical correction in spacing.

A theme difference is a token with a value in each theme, never a `[data-theme]` rule outside
`tokens.css`: the two finishes that show only on dark (`motion.css`) read tokens that are empty on
light. `public/theme.js` sets `data-theme` before the first frame, so there is no unthemed state to
style.

## Engine and platform corrections

They key off attributes set on `<html>` before anything renders, and each says why it exists:

- `data-engine='webkit'` (`public/theme.js`): WebKitGTK skips off-screen boxes unreliably inside a
  scrolling pane, so `content-visibility` paging in `transcript.css` is for Blink only.
- `data-os='win'` (`main.tsx`): ClearType draws light weights thin, so `shell.css` ("platform tuning")
  nudges weights and the two smallest sizes.
- Scrollbars use only the `-webkit-scrollbar` rules (`base.css`): setting `scrollbar-color` would make
  Chromium fall back to the platform scrollbar.
