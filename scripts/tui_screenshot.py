import os
import sys
import time

# Headless TUI smoke: launch, wait for first paint, save screenshot, quit.
from boardagent.tui import BoardAgentApp


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/human/screenshots/tui_main.png"
    app = BoardAgentApp()
    # Run in headless mode with screenshot capture
    os.makedirs(os.path.dirname(out), exist_ok=True)
    app.run(
        headless=True,
        size=(120, 40),
        auto_pilot=auto_pilot(out),
    )


def auto_pilot(out):
    async def pilot(app):
        await app.screen_ready_wait()
        # Wait a short beat for the table to populate / paint
        await app._suspend_for(0.5)
        app.save_screenshot(out)
        app.exit()

    return pilot


if __name__ == "__main__":
    main()
