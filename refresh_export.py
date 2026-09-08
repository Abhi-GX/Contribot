"""
refresh_export.py

The RECURRING job. Run this on a schedule (cron / Task Scheduler / Azure
Function timer trigger / etc.) -- e.g. daily or a few times a day.

It reuses the session saved by capture_login_session.py to open the Learn
admin contribution page headlessly, click the Export button, download the
resulting .xlsx, and feed it into contribution_calc.py to regenerate
totals.json -- the cache the Teams bot reads from.

WHY THIS APPROACH INSTEAD OF SCRAPING THE TABLE DIRECTLY:
Learn's admin page is a single-page app backed by a private, undocumented
GraphQL endpoint. Scraping that table (or reverse-engineering the GraphQL
calls) is fragile and technically closer to unauthorized API use. The
"Export" button, by contrast, is a normal product feature already exposed
to admins -- it gives us a clean, structured file with an EMAIL column,
so this script just automates clicking a button a human is already
allowed to click, and reads the file that comes out.

KNOWN FRAGILE POINT:
The exact selector for the Export button (and any filter state needed
first, e.g. filtering to SUBMITTED/VERIFIED) depends on Learn's current
DOM, which can change without notice. This script tries a few common
patterns and falls back to a screenshot + clear error if none work --
see EXPORT_BUTTON_SELECTORS below. If Learn's UI changes, updating that
list is usually a five-minute fix once someone can see the live page.

KNOWN FRAGILE POINT #2:
The saved login session (learn_session.json) will eventually expire
(SSO sessions typically last hours to a couple of weeks depending on
tenant policy). When this script starts failing with a "still looks
like a login page" error, someone needs to re-run capture_login_session.py.
Consider adding alerting around this job's exit code so that failure
doesn't go unnoticed.

USAGE:
    python refresh_export.py [--out-dir .]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from contribution_calc import compute_totals
import json

LEARN_URL = (
    "https://learn.epam.com/admin/contribution"
    "?filter=%7B%22contributionStatus%22%3A%7B%22in%22%3A%5B%22SUBMITTED%22%2C%22VERIFIED%22%5D%7D%7D"
    "&presetId=-2"
)
SESSION_FILE = Path(__file__).parent / "learn_session.json"

# Tried in order. Update this list if Learn's UI changes -- inspect the
# real Export button with browser devtools and add its selector here.
EXPORT_BUTTON_SELECTORS = [
    "button:has-text('Export')",
    "[data-testid='export-button']",
    "button[aria-label='Export']",
    "text=Export",
]


def find_and_click_export(page):
    for selector in EXPORT_BUTTON_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=5000)
                return selector
        except PlaywrightTimeoutError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).parent),
        help="Directory to write the downloaded export + totals.json into",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not SESSION_FILE.exists():
        print(
            f"ERROR: {SESSION_FILE} not found. Run capture_login_session.py "
            "once first to log in and save a session.",
            file=sys.stderr,
        )
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(storage_state=str(SESSION_FILE))
        page = context.new_page()
        page.goto(LEARN_URL, wait_until="networkidle")

        if "login" in page.url.lower() or "sso" in page.url.lower():
            screenshot_path = out_dir / "refresh_export_failure.png"
            page.screenshot(path=str(screenshot_path))
            print(
                f"ERROR: session appears expired (redirected to {page.url}). "
                f"Screenshot saved to {screenshot_path}. "
                "Re-run capture_login_session.py to log in again.",
                file=sys.stderr,
            )
            browser.close()
            sys.exit(1)

        # Give the SPA a moment to render the table before we look for
        # the export control.
        page.wait_for_timeout(2000)

        with page.expect_download(timeout=15000) as download_info:
            clicked_selector = find_and_click_export(page)
            if clicked_selector is None:
                screenshot_path = out_dir / "refresh_export_failure.png"
                page.screenshot(path=str(screenshot_path))
                print(
                    "ERROR: could not find the Export button using any known "
                    f"selector. Screenshot saved to {screenshot_path}. "
                    "Inspect the live page and update EXPORT_BUTTON_SELECTORS "
                    "in refresh_export.py.",
                    file=sys.stderr,
                )
                browser.close()
                sys.exit(1)
            print(f"Clicked export button (selector: {clicked_selector})")

        download = download_info.value
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = out_dir / f"contribution_export_{timestamp}.xlsx"
        download.save_as(str(export_path))
        print(f"Downloaded export to {export_path}")

        browser.close()

    # Recompute totals from the freshly downloaded export
    totals = compute_totals(str(export_path))
    totals_path = out_dir / "totals.json"
    with open(totals_path, "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)

    print(f"Refreshed {totals_path} with {len(totals)} people.")


if __name__ == "__main__":
    main()
