# Stylesheets

Plain CSS, no preprocessor, no CSS-in-JS. `web/src/main.tsx` imports the files in this order, and the
order is the cascade's:

| File | Holds |
|---|---|
| `fonts.css` | The bundled Inter and JetBrains Mono faces (Latin only; CJK uses the system font). |
| `tokens.css` | Every design token, then the dark (`:root, [data-theme='dark']`) and light (`[data-theme='light']`) values. Nothing else reads a raw colour. |
| `base.css` | Elements and the shared controls: buttons, inputs, switches, badges, scrollbars, the reduced-motion switch, `.render-fault`. |
| `app.css` | The shell and app-wide components: sidebar, session header, composer, menus, dialogs, settings, toasts, find bar, tooltip, control room, inbox. |
| `transcript.css` | The conversation: messages, Markdown, tool cards, permission and question cards, workflows. |
| `panels.css` | The side panel's tabs: changes, files, terminal, preview, tasks, timeline, info. |
| `dispatch.css` | The dispatch page (`dp-`). |
| `motion.css` | How things move: `animation` and `transition` for the selectors it names, the parts that exist only to move (segmented thumb, settings ink, sidebar selection), the lightbox, and the dark theme's two cross-cutting finishes (lit top edges, sheet wells). |

**One property, one file.** No selector sets the same property, or a shorthand and one of its
longhands, in two files, and no `@keyframes` name is defined twice; `scripts/css-overlap.mjs` checks
it as part of `npm run lint`. A component's motion goes in `motion.css`; everything else about it
goes in the file that owns the component.

## Names

- **Feature prefix, then part**: `side-row`, `side-row-main`, `ov-tile`, `pal-input`, `ns-folder`,
  `tl-row-btn`, `dp-table`. One prefix per component; a new component picks a new prefix.
- **State is a separate class** on the element it describes: `.on` (chosen), `.active` (keyboard or
  current item), `.open`, `.live` (being written now), `.running`, `.done`, `.error`, `.stale`,
  `.unread`, `.focused`, and `.leaving` (an exit is playing, see below). ARIA state attributes are
  styled directly where they exist (`[aria-expanded='true']`).
- **Size modifiers** are plain classes on the base: `.btn.sm`, `.btn.lg`, `.seg.sm`, `.chip.tiny`.
- `.leaving` is set on the still copy that `lib/motion` (`useExitGhost`, `<Leave>`) leaves behind
  when a component unmounts; its rule in `motion.css` is the exit.

## Tokens

- **Surfaces**: `--canvas` (conversation), `--chrome` (sidebar, panel), `--raised` (composer, cards),
  `--raised-2`, `--inset` (code, fields), `--card-ask` (cards that wait on you); `--glass` and
  `--glass-2` for floating panes over `--glass-blur`.
- **Lines and fills**: `--hair`, `--hair-2`, `--hair-3`; `--fill-1…3` for hover and selection.
- **Text**: `--text`, `--text-2`, `--text-3`, `--text-4`, from primary to faintest.
- **Meaning**: `--accent` (primary action, attention, focus), `--ok`, `--err`, `--info`, `--warn`,
  `--plan`, `--think` (reasoning only), each with a `-wash`; `--accent-ink` is the text on any solid one.
- **Spacing**: `--sp-N` is N × 4px, with `_5` half steps (`--sp-1_5` = 6px). A value off the scale
  is an optical correction and stays a number.
- **Type**: `--fs-tiny` 10 … `--fs-display` 22; a `-plus` step is half a pixel up for faces that run
  small (code, keycaps, counters). `--font-size` is the reading size the person sets.
- **Radii**: `--r-1` 4, `--r-2` 6, `--r-3` 10, `--r-4` 16, `--r-5` pill.
- **Stacking**: `--z-float` inside a pane, then `--z-boot`, `--z-dialog`, `--z-popover`, `--z-toast`,
  `--z-lightbox`, `--z-ghost`, `--z-tip`. Inside a component, `z-index` 1 and 2 only order siblings.
- **Motion**: `--t-*` durations with `--ease*`, and the sampled springs `--spring-soft|snappy|bouncy`
  with their settle times (`-t`), always used as a pair.

A theme difference is a token with a value in each theme, never a `[data-theme]` rule in a component.
The exceptions are the two finishes in `motion.css` that exist only on dark. `public/theme.js` sets
`data-theme` before the first frame, so there is no unthemed state to style.

## Engine and platform corrections

They key off attributes set on `<html>` before anything renders, and each says why it exists:

- `data-engine='webkit'` (`public/theme.js`): WebKitGTK skips off-screen boxes unreliably inside a
  scrolling pane, so `content-visibility` paging in `transcript.css` is for Blink only.
- `data-os='win'` (`main.tsx`): ClearType draws light weights thin, so `app.css` ("platform tuning")
  nudges weights and the two smallest sizes.
- Scrollbars use only the `-webkit-scrollbar` rules (`base.css`): setting `scrollbar-color` would make
  Chromium fall back to the platform scrollbar.
