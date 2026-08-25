# TaskManager Theme Format

TaskManager themes are plain JSON files. Drop them in `~/.taskmanager/themes/` and they appear in the TUI Settings tab automatically.

## File layout

```json
{
  "name": "mytheme",
  "description": "Optional description",
  "colors": {
    "background": "#0a0a0a",
    "foreground": "#ffb000",
    ...
  }
}
```

## Required keys

Only `name` and `colors` are required. `description` is optional.

## Color tokens

See `taskmanager/themes/schema.json` for the full list. Important tokens:

- `background`, `foreground`, `primary`, `secondary`, `accent`, `border`, `muted`
- `error`, `success`
- `status-todo`, `status-in_progress`, `status-blocked`, `status-done`
- `priority-red`, `priority-orange`, `priority-yellow`, `priority-green`, `priority-blue`, `priority-white`

## Example

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

Future versions will load themes from a community library; the format will stay compatible.
