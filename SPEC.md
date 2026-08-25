# Project Spec: Agent-First Task Manager

## Concept
A Todoist-style task manager built primarily for AI agents to use, not humans.
Tagline: "from agents, to agents." Free forever, fully local, no cloud dependency.

## Core Principles
- Local-first: runs entirely on the user's machine, no external services required.
- Free forever: no paid tiers, no subscriptions.
- Agent-first, human-secondary: the primary consumer of the system is AI agents
  (via API/MCP), not a human clicking around a UI.
- One source of truth: business logic lives in a single core API. Every other
  interface (MCP, terminal UI) is a thin wrapper around that core — no
  duplicated logic.

## Access Layers
Support **both** of the following, and let the user pick whichever fits their
workflow:

1. **REST API** — the source of truth. All task operations (create, read,
   update, delete, claim, complete) live here first.
2. **MCP server** — a thin adapter that wraps the REST API. Exposes the same
   operations as MCP tools/resources for agents that speak MCP natively.

Do not implement task logic twice. The MCP server should call the REST API
internally (or share the same underlying service layer) rather than
reimplementing anything.

## Data Model

### Human-facing fields (always shown)
Standard task fields a person would recognize from any todo app:
- Title
- Description
- Due date
- Priority
- Project / category
- Status (e.g. todo / in progress / blocked / done)

### Agent-facing fields (custom metadata)
Agents need to attach arbitrary structured data to a task that humans don't
normally need to see. Support this via a flexible metadata layer — either a
JSON blob column on the task, or a separate key/value table if it needs to be
queryable. Agents can create **any number of custom fields**, on the fly, with
no fixed schema — this is not a preset list of allowed field names. An agent
should be able to invent a new field the first time it needs one. Example
fields agents might create (not exhaustive, not fixed):
- Retry count
- Blocking / dependency task IDs
- Owning agent / session ID
- Expected output schema or format
- Execution notes / intermediate results
- Timeout or expiry info

**Multi-agent write safety:** if more than one agent can write metadata on the
same task, namespace the fields (e.g. `metadata.<agent_id>.notes`) so agents
don't clobber each other's data.

### Priority levels
Tasks have a priority set via color, not a numeric scale:
Red, Orange, Yellow, Green, Blue, White.

## Background Service
The app runs as an **always-on background process** that owns the database
and does the actual task management work. It must be as lightweight as
possible — minimal RAM and CPU footprint, since it's expected to run
continuously in the background at all times.

- The REST API and MCP server should be exposed by this background service.
- The terminal UI (foreground) is a separate process that talks to the
  background service — it should be closeable/reopenable without affecting
  the background service or losing data.
- All tasks are persisted in a database (see SQLite recommendation below) that
  the background service owns and manages.
- Efficiency matters here more than anywhere else in the stack — this is the
  one piece that's always running, so pick lightweight tooling (e.g. avoid
  heavy frameworks or polling loops; prefer async I/O and event-driven
  patterns) to keep idle resource usage near zero.

## UI Modes
The terminal UI has two display modes:
- **Human mode (default):** shows only the human-facing fields listed above.
  Clean, readable, no raw metadata clutter.
- **AI mode (toggle):** reveals everything, including the full custom
  metadata blob per task, formatted for readability (or raw JSON, whichever
  is faster to build first).

## Design / Aesthetic
- Terminal-themed, inspired by Hermes styling.
- Sharp monospace font, minimal color palette (e.g. amber or green on black).
- Box-drawing characters for borders/panels, no rounded UI elements.
- Suggested stack: Textual (Python) or Bubble Tea (Go) for the terminal UI.

## Settings Tab
A dedicated settings tab/screen in the terminal UI where the user can
customize whatever's necessary. Confirmed settings so far:
- **Foreground opacity** — adjustable transparency of the terminal UI window.
- **Themes** — switchable visual themes (color palette, styling).

Treat this as an extensible section — more settings will likely be added as
the app grows (see Future Vision below).

## Future Vision (not part of initial build)
Long-term, the goal is for this to become an Obsidian-style ecosystem: a
community library where users can share and install community-made themes,
plugins, and other extensions. Not needed for the initial build, but worth
keeping the architecture (especially theming and settings) flexible enough
that this is realistic to bolt on later rather than requiring a rewrite.

## Documentation Strategy
Two **separate** documentation sets, generated/maintained differently:

1. **Agent docs** — optimized for AI consumption. Short, function/schema
   focused, minimal prose. One example call per endpoint/tool, no narrative
   explanation. Should be **auto-generated** from the OpenAPI spec (REST) and
   MCP tool/resource schemas so they never drift out of sync with the code.

2. **Human docs** — optimized for human understanding. Written by hand in
   Markdown. Includes explanations, walkthroughs, screenshots of the terminal
   UI, and "why would I use this" context. Not auto-generated — this is where
   actual writing effort goes.

## Suggested Stack (local-first, zero/low-config)
- **Storage:** SQLite (embedded, no server, zero config)
- **API:** FastAPI (Python) — gets OpenAPI docs auto-generated for free, which
  directly supports the agent-docs goal. (Go + a REST framework is a fine
  alternative if preferred.)
- **MCP layer:** official MCP SDK (Python or TypeScript), wrapping the FastAPI
  service layer
- **Terminal UI:** Textual (Python) or Bubble Tea (Go)

## Open Questions to Resolve During Build
- Exact task lifecycle: creation → an agent claiming/locking a task → agent
  writing progress into metadata → completion → human review.
- Whether task claiming needs locking to prevent two agents grabbing the same
  task simultaneously.
- Whether metadata fields need schema validation per field, or stay fully
  freeform.
- How the background service and foreground UI communicate (local HTTP on
  localhost, Unix socket, etc.) and how the UI detects if the background
  service isn't running.
- Theme file format — worth designing this now with the future community
  library in mind (e.g. a simple, documented theme spec from day one).
