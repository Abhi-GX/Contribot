"""
capture_login_session.py

ONE-TIME, INTERACTIVE script. Run this yourself (as the service/admin
account that has access to https://learn.epam.com/admin/contribution),
by hand, whenever you set this up or whenever the saved session expires.

Uses your REAL Edge profile (with EPAM device-trust / Conditional Access
already satisfied) so that EPAM SSO does not block the login.

IMPORTANT: close all Edge windows before running this script.
Edge locks its profile directory while it is open, which prevents
Playwright from launching a new instance with the same profile.

USAGE:
    python capture_login_session.py
"""

import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

LEARN_URL = "https://learn.epam.com/admin/contribution"
SESSION_FILE = Path(__file__).parent / "learn_session.json"

# Your real Edge profile — Playwright reuses device trust / enrolled certs
# already stored here, which satisfies EPAM Conditional Access.
EDGE_USER_DATA_DIR = str(Path.home() / "AppData/Local/Microsoft/Edge/User Data")


def main():
    print("Closing any Edge background processes...")
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    print("Done.\n")

    print("Opening Edge with your real profile.")
    print(f"Navigate to {LEARN_URL} and log in if prompted.")
    print("Once you can see the contribution table, come back here and press Enter.\n")

    with sync_playwright() as p:
        # launch_persistent_context opens Edge with your actual profile,
        # inheriting device compliance / Intune enrollment / saved SSO cookies.
        context = p.chromium.launch_persistent_context(
            user_data_dir=EDGE_USER_DATA_DIR,
            headless=False,
            channel="msedge",
            args=["--no-first-run"],
        )
        page = context.new_page()

        print(f"\nEdge is open. In the address bar, go to:\n  {LEARN_URL}")
        print("Log in if prompted, wait for the contribution table to appear.")
        input("\n>>> Press Enter here once you can see the table... ")

        current_url = page.url
        if "login" in current_url.lower() or "sso" in current_url.lower():
            print(
                f"WARNING: current URL still looks like a login page ({current_url}). "
                "The saved session may not be authenticated. Consider trying again."
            )

        context.storage_state(path=str(SESSION_FILE))
        print(f"\nSaved session to {SESSION_FILE}")
        context.close()


if __name__ == "__main__":
    main()
