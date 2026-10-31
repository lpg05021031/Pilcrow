# The Qt client's UX: an audit and what changed

The rule of thumb: **let the layout do the explanation.** A screen should say what it is and what
to do next by how it is arranged — what is present, what is absent, what sits next to what — and
reach for a sentence only when the arrangement cannot carry the meaning. A state that explains
itself twice (a heading that says "nothing yet" over a card that says "nothing yet", two buttons
for the one first action) is a layout that has not finished its job.

This note records a tour of every screen and state of the client, offscreen against the mock
server, what the tour found, and what was changed. The tour script lives in the session that made
it; the screenshots it took are in `test/out` after `python qt/tests/snap.py` runs the standard
scenarios.

## 1. What was looked at

- The welcome page; the control room quiet, with a session needing an answer, and with one at work;
  the inbox empty.
- A fresh session, a short one, a long one scrolled up (the Latest pill), find in the conversation,
  the permission card, the question card, streaming, a queued message, the error bar, the MCP
  notices.
- Every side-panel tab: Changes, Files, Terminal, Preview, Tasks (empty and full), Timeline (off),
  Session.
- The sidebar searching, showing archived sessions, collapsed; a row's hover and its menu; the
  composer's permission and model menus.
- Every dialog: new session, every settings page, schedule, the palette, shortcuts, confirm, MCP
  sign-in; toasts; the window at 1920, 1480, 1100 and 900 px wide.

What holds up well: the conversation itself (bubbles, folds with their one-line summaries and step
counts, the duration marks), the permission and question cards with their numbered keys and the
hints under the composer, the control room when something is happening, the sidebar's grouping by
folder with the "Needs you" group lifted to the top, the settings pages' two-column rows, the
palette and the shortcuts list, the menus with a line under each entry saying what it does.

## 2. Findings and changes

Each item names the screen, what the layout was saying wrongly, and what it says now.

### Empty states that explained themselves twice

| Screen | Was | Now |
|---|---|---|
| Control room, nothing running | The header said "Nothing running", the Working tile said 0, and a box under a "Working 0" heading said it a third time in two sentences. | The Working section appears when something works. With nothing live the page is the tiles, today's finished sessions and the spend. A tile with nothing behind it jumps to the first section on screen. |
| Inbox, no schedules | A "Schedules" rail saying "No schedules yet." with a "+ New" button, beside a hero saying "Nothing runs on its own yet." with a "New schedule" button. | The hero alone, with the one button. The rail comes back with the first schedule, and then its button is the way to add another. |
| Tasks tab, nothing yet | Two headed sections ("To-do list", "Running now") each holding a sentence about what would appear there. | One empty state at the top of the tab. A section appears when it has something to show; an empty one is not drawn. |
| Settings → Skills / MCP servers, none yet | The intro card's "New skill" / "Add server" and the toolbar's "+ New" / "+ Add server", two buttons for the one first action; a group whose hint at the right ("Open a session to see project skills") was repeated as its empty line. | The intro card holds the first action and the toolbar's button waits until there is a list to add to. A hint that is also the empty line is said once. |
| Terminal unavailable | The icon in the middle of the tab, the sentence far below it at the bottom. | One block at the top like every other tab's empty state: icon, the sentence, and what to do about it. |
| Timeline off | "Turn it on in Settings → General" as a sentence, with no way to do so from there. | The empty state carries the button when the setting is the reason (a folder too close to the root keeps its own explanation and no button). |
| Files tab, empty tree | A blank column beside "Pick a file to view or edit it." | The tree says why it is blank: still reading, nothing in the folder, or the error. |

### Things that were cut off or mislabelled

| Screen | Was | Now |
|---|---|---|
| A fresh session's example prompts | The second row of chips was clipped: the card had been measured before its branch line appeared under the path. | An island (a real widget in the conversation) measures itself again when its layout asks for room, so a card never clips what it grew. |
| Toasts | A toast whose text wrapped kept a one-line height and clipped; a toast added right after another was rebuilt (or after a dialog closed) could land in a column 0 px tall and not show until the window was resized. | Each card is as wide as its words want and as tall as they wrap to; the column's height is its own sum, computed when it is rebuilt. |
| Session header, narrow column | The title was cut at the edge with no ellipsis; the folder and branch crumbs were cut mid-word. | The title elides with …; under 560 px the crumbs go (the web's rule) and the title keeps the room. |
| Composer, narrow column | "Ask before edits" was cut to "Ask b". | Under 420 px the permission chip keeps only its icon (the web's rule); the label returns with the room. |
| Changes tab, a file card's folder | `./web/src/lib` read `web/src/lib/.` (a right-to-left layout trick carried over from the web). | `web/src/lib`. |
| Sidebar, archived sessions | The list changed with no word of what it had become; the only sign was the lit archive icon at the bottom. | An "← Archived" head at the top of the list, the arrow being the way back. |
| New session, the folder | Two buttons, "Browse" and "System…", for one job. | One "Browse"; the system dialog is an icon in the browser's own toolbar, where the folder is being chosen. |

### Left as it is, and why

- The duration mark under a reply (✱ 4.1s) and the step counts at the right of a fold line read as
  decoration at first but are learned once; they carry no action and cost no room.
- The control room's "Allow / Deny" on a card that is really a question (AskUserQuestion) is a
  limitation of the mock, which marks nothing as interactive; the real server does, and the card
  then shows "Answer" and opens the session.
- Below ~1000 px with both the sidebar and the side panel open the conversation is squeezed to
  about 300 px. The web does the same; the sidebar collapses with Ctrl+B and the panel with Ctrl+\.
  Auto-collapsing one of them would change a setting the person did not touch.
- The first switch to a busy state can lag the first streamed words by a frame on the mock, which
  sets its status after it starts streaming; the real server sets it first.

## 3. Principles kept while changing things

- **Absence over prose.** When nothing is there, the strongest statement is the missing section.
  A tile with a 0 in it already says "nothing"; so does a header. One of them is enough.
- **One first action.** The place that explains a thing (an intro card, a hero) is where its first
  action lives. The toolbar's button is for the second and the tenth.
- **Where it is, is what it is.** A list that replaced another says so at its head, with the way
  back in the same spot. A picker's alternatives sit inside the picker.
- **Nothing cut.** Text that cannot fit elides, with the ellipsis showing that it did; a card that
  grows is measured again; a column of toasts is as tall as its toasts.
- **The web's breakpoints, in a window.** 560 px drops the crumbs, 420 px the permission label,
  760 px the side panel's tab names: the same thresholds, so the two clients agree.

## 4. Verifying

`qt/tests` covers the changed states: the control room without the Working section, the inbox's
hero alone, the Tasks tab's sections appearing with their content, the timeline's settings button,
the file card's folder, the narrow header and composer. The screenshot scenarios in
`qt/tests/snap.py` and the tour in this audit were re-run after the changes.
