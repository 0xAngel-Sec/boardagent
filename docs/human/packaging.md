# Running the BoardAgent server

BoardAgent has two parts: a **server** that stores your tasks and runs in
the background, and a **terminal app** that you use to view and edit
them. This page is about the server — how to start it and keep it
running on Windows.

## The three programs

After installing BoardAgent you get three programs:

- `boardagent-server` — the background server. This is what stores your
  tasks and lets the app and AI tools talk to them.
- `boardagent` — the terminal app you actually look at and type in.
- `boardagent-mcp` — a helper for connecting AI tools (see
  [mcp.md](mcp.md)).

For everyday use you only need the first two: start the server, then
open the app.

## Three ways to run the server

### Option 1: double-click the exe (simplest)

If you have the prebuilt Windows executables (in the `dist/` folder),
just double-click `boardagent-server.exe`. It starts quietly in the
background — no window pops up. Then run `boardagent.exe` (or
double-click it) to open the task board.

This is the easiest way. The only downside: you have to start it again
every time you restart your computer.

### Option 2: start automatically at logon (set-and-forget)

If you want the server to start every time you log in to Windows, use
**Task Scheduler**:

1. Open Task Scheduler (search for it in the Start menu).
2. Click "Create Basic Task."
3. Name it "BoardAgent Server."
4. Choose "When I log on" as the trigger.
5. Choose "Start a program" as the action.
6. Browse to `boardagent-server.exe` and select it.
7. Finish, then right-click the task, open Properties, and check "Run
   with highest privileges" and "Hidden."

From now on the server starts on its own when you log in. You just open
the app whenever you need it.

### Option 3: run it manually from a terminal

Open a terminal (Command Prompt, PowerShell, or Git Bash) and type:

```
boardagent-server
```

Leave that terminal window open while you work. When you close it, the
server stops. Your tasks are safe — they are saved to disk — and the app
reconnects next time you start the server.

## Windows executables (no Python needed)

Prebuilt one-file executables live in the `dist/` folder. These bundle
everything BoardAgent needs, so you do not have to install Python or any
libraries on the target computer.

- `boardagent-server.exe` — the background server (no console window).
- `boardagent.exe` — the terminal app (run it in a terminal, or
  double-click).
- `boardagent-mcp.exe` — the MCP helper for AI tools.

To build them yourself (if you have Python installed):

```
python scripts/build_exes.py
```

Build scripts are provided as examples:

- `scripts/build_server_exe.bat`
- `scripts/build_tui_exe.bat`
- `scripts/build_mcp_exe.bat`

## A note on durability

The server writes your tasks to a file on disk as you go. If the server
crashes or you close it by accident, your tasks are not lost — just
start the server again and the app picks up where you left off.