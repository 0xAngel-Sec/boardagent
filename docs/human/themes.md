# BoardAgent themes

A theme is a small file that tells BoardAgent which colors to use. By
making your own theme file you can change the background, text color,
status colors, priority colors, and more.

Themes are plain text files in a format called JSON (a way of writing
data as labeled values — `{"key": "value"}`). You can edit them in any
text editor.

## How to add a theme

1. Create a folder called `themes` inside your BoardAgent folder, so the
   path is `~/.boardagent/themes/`.
2. Put a theme file in that folder (see the format below).
3. Open BoardAgent, go to the **Settings** tab, and your theme appears in
   the theme list.

Themes are read when the app starts, so **restart the app** after adding
a theme file.

## File format

A theme file looks like this:

```json
{
  "name": "mytheme",
  "description": "Optional description",
  "colors": {
    "background": "#0a0a0a",
    "foreground": "#ffb000"
  }
}
```

- `name` — the name shown in Settings. Required.
- `colors` — the color settings. Required.
- `description` — a note for yourself. Optional.

Colors are written as hex codes (a `#` followed by six characters, like
`#ffb000` for amber or `#00ff66` for green). You can pick any color using
an online color picker.

## Color tokens

Each entry under `colors` controls one part of the app. Here are the
ones you can set:

**General colors:**

- `background` — the screen background.
- `foreground` — the default text color.
- `primary`, `secondary`, `accent` — accent colors used for highlights
  and buttons.
- `border` — lines and borders.
- `muted` — dimmed text (hints, secondary info).
- `error` — error messages (usually red).
- `success` — success messages (usually green).

**Status colors** (shown in the Status column):

- `status-todo` — tasks not yet started.
- `status-in_progress` — tasks being worked on.
- `status-blocked` — tasks waiting on something.
- `status-done` — finished tasks.

**Priority colors** (shown in the Priority column):

- `priority-red`, `priority-orange`, `priority-yellow`,
  `priority-green`, `priority-blue`, `priority-white`

You do not have to set every token. Anything you leave out falls back to
the built-in default.

## Example: a purple "plasma" theme

```json
{
  "name": "plasma",
  "description": "Purple cyberpunk theme",
  "colors": {
    "background": "#0d001a",
    "foreground": "#d580ff",
    "primary": "#bf40bf",
    "border": "#9932cc",
    "status-in_progress": "#d580ff"
  }
}
```

Save this as `~/.boardagent/themes/plasma.json`, open Settings, and
select "plasma."

## Built-in themes

BoardAgent comes with several themes already installed (amber, matrix,
synth, and others). They appear in the same Settings list. You can use
them as-is, or copy one into your themes folder and change the colors to
make your own.