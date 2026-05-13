"""Automated FFIEC Call Report bulk-data downloader.

Drives Chromium via Playwright through the FFIEC CDR Public Data Distribution
form for each requested quarter. Saves single-period ZIPs to ``data/raw/``,
skipping any quarters whose ZIP already exists. Idempotent — safe to re-run.

Setup (one-time)::

    uv pip install -e ".[fetch]"
    uv run playwright install chromium

Usage::

    # Specific list of quarters
    uv run python -m scripts.fetch_ffiec --quarter 2019Q1 --quarter 2020Q4

    # A contiguous range
    uv run python -m scripts.fetch_ffiec --from 2019Q1 --to 2023Q1

    # Run with the browser visible (default — so you can watch the first run)
    uv run python -m scripts.fetch_ffiec --from 2019Q1 --to 2023Q1

    # Run headless once you're confident
    uv run python -m scripts.fetch_ffiec --from 2019Q1 --to 2023Q1 --headless

Etiquette: inserts a 3-second pause between downloads, sets a polite
User-Agent, and respects the FFIEC site's license-acceptance flow.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import click

from alm.config import PATHS
from alm.data.ffiec import Quarter

FFIEC_URL = "https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "alm-rate-shock-2023/0.1 "
    "(portfolio research project; contact jared@kuroshioflow.io)"
)
INTER_DOWNLOAD_DELAY_SEC = 3.0

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("fetch_ffiec")


# ---------------------------------------------------------------------------
# Quarter enumeration helpers
# ---------------------------------------------------------------------------

def _quarter_index(q: Quarter) -> int:
    return q.year * 4 + (q.quarter - 1)


def _quarters_in_range(start: Quarter, end: Quarter) -> list[Quarter]:
    """All quarters in the closed range [start, end]."""
    if _quarter_index(start) > _quarter_index(end):
        start, end = end, start
    return [
        Quarter(year=i // 4, quarter=(i % 4) + 1)
        for i in range(_quarter_index(start), _quarter_index(end) + 1)
    ]


def _already_have_zip(raw_dir: Path, quarter: Quarter) -> Path | None:
    """Return an existing ZIP for the quarter if any (filename or content match)."""
    token = quarter.filename_token
    # Filename match: cheap
    for p in raw_dir.glob("*.zip"):
        if "Call" in p.name and token in p.name:
            return p
    # Content match: also cheap, since the parser caches the content index.
    # (Skip the content scan here for speed; the parser will catch it later.)
    return None


# ---------------------------------------------------------------------------
# Playwright form-walking
# ---------------------------------------------------------------------------

def _wait_settled(page, label: str) -> None:
    """Wait for ASP.NET WebForms postback to settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        log.debug("networkidle wait timed out after %s — continuing", label)


def fetch_one(page, quarter: Quarter, raw_dir: Path) -> bool:
    """Drive the FFIEC form to download one quarter's bulk ZIP."""
    existing = _already_have_zip(raw_dir, quarter)
    if existing is not None:
        log.info("Skip %s — already have %s", quarter.label, existing.name)
        return True

    date_str = quarter.end_date.strftime("%m/%d/%Y")  # e.g. "12/31/2022"
    log.info("Fetching %s (%s)…", quarter.label, date_str)

    # Fresh navigation per quarter — guarantees a clean form state.
    page.goto(FFIEC_URL, wait_until="domcontentloaded")
    _wait_settled(page, "initial load")

    # 1. Ensure "Call Reports -- Single Period" is selected in the listbox.
    #    ASP.NET listbox renders as <select size=N>. The label is the safest
    #    way to identify the option across page revisions.
    try:
        listbox = page.locator('select').filter(
            has=page.locator('option', has_text="Call Reports -- Single Period")
        ).first
        listbox.select_option(label="Call Reports -- Single Period")
        _wait_settled(page, "product select")
    except Exception as e:
        _on_error(page, quarter, raw_dir, "product-listbox", e)
        return False

    # 2. Choose the reporting-period end date.
    try:
        date_select = page.locator('select').filter(
            has=page.locator('option', has_text=date_str)
        ).first
        date_select.select_option(label=date_str)
        _wait_settled(page, "date select")
    except Exception as e:
        _on_error(page, quarter, raw_dir, "date-select", e)
        return False

    # 3. Make sure "Tab Delimited" radio is checked.
    try:
        tab_radio = page.get_by_label("Tab Delimited", exact=True).first
        if not tab_radio.is_checked():
            tab_radio.check()
    except Exception:
        log.debug("Tab Delimited already selected (no-op)")

    # 4. Click Download, then accept the license, then catch the file.
    try:
        with page.expect_download(timeout=60_000) as dl_info:
            page.get_by_role("button", name="Download").first.click()
            # The license page may interpose. Try to accept it.
            _accept_license_if_present(page)
        download = dl_info.value
    except Exception as e:
        _on_error(page, quarter, raw_dir, "download-click", e)
        return False

    # 5. Save into data/raw/ with FFIEC's suggested filename.
    suggested = download.suggested_filename or f"FFIEC_Call_{quarter.filename_token}.zip"
    target = raw_dir / suggested
    download.save_as(target)
    log.info("Saved %s → %s (%.1f MB)",
             quarter.label, target.name, target.stat().st_size / 1024 / 1024)
    return True


def _accept_license_if_present(page) -> None:
    """Look for the post-download license-acceptance interstitial and click it."""
    candidates = [
        # ASP.NET button name variants the page might use
        lambda: page.get_by_role("button", name="I Accept").first.click(timeout=3000),
        lambda: page.get_by_role("button", name="Accept").first.click(timeout=3000),
        lambda: page.get_by_text("I Accept", exact=True).first.click(timeout=3000),
        lambda: page.locator('input[value*="Accept"]').first.click(timeout=3000),
        lambda: page.locator('input[id*="Accept"]').first.click(timeout=3000),
    ]
    for fn in candidates:
        try:
            fn()
            log.debug("License accepted via candidate %s", fn)
            return
        except Exception:
            continue
    # No interstitial — that's fine, the download might start directly.


def _on_error(page, quarter: Quarter, raw_dir: Path, where: str, err: Exception) -> None:
    """Save a screenshot + HTML dump for debugging when something fails."""
    debug_dir = raw_dir.parent / "fetch_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{quarter.label}_{where}"
    try:
        page.screenshot(path=str(debug_dir / f"{stem}.png"), full_page=True)
        (debug_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        log.error("Failed at %s for %s: %s. Debug artifacts in %s/%s.{png,html}",
                  where, quarter.label, err, debug_dir, stem)
    except Exception as e:
        log.error("Failed at %s for %s: %s (also failed to save debug: %s)",
                  where, quarter.label, err, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--quarter", "quarters", multiple=True,
              help="Quarter like 2019Q4. Repeatable.")
@click.option("--from", "start", default=None,
              help="Start of contiguous range, e.g. 2019Q1.")
@click.option("--to", "end", default=None,
              help="End of contiguous range, e.g. 2023Q1.")
@click.option("--headless/--no-headless", default=False,
              help="Run Chromium headless. Default: visible (so you can watch the first run).")
@click.option("--delay", default=INTER_DOWNLOAD_DELAY_SEC,
              show_default=True, help="Seconds to pause between downloads (be polite).")
def main(quarters: tuple[str, ...], start: str | None, end: str | None,
         headless: bool, delay: float) -> None:
    PATHS.ensure()

    # Resolve which quarters to fetch.
    qs: list[Quarter] = []
    if start and end:
        qs.extend(_quarters_in_range(Quarter.parse(start), Quarter.parse(end)))
    qs.extend(Quarter.parse(q) for q in quarters)
    # De-dupe while preserving order.
    seen: set[tuple[int, int]] = set()
    plan: list[Quarter] = []
    for q in qs:
        key = (q.year, q.quarter)
        if key not in seen:
            plan.append(q)
            seen.add(key)
    if not plan:
        log.error("No quarters specified. Use --quarter or --from/--to.")
        sys.exit(2)

    log.info("Plan: %d quarter(s) → %s", len(plan), ", ".join(q.label for q in plan))
    log.info("Output: %s", PATHS.raw)
    log.info("Headless: %s", headless)

    # Lazy import — playwright is optional.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright is not installed.\n"
                  "Install with: uv pip install -e \".[fetch]\" "
                  "&& uv run playwright install chromium")
        sys.exit(1)

    successes = 0
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            accept_downloads=True,
        )
        page = context.new_page()

        for i, q in enumerate(plan):
            ok = fetch_one(page, q, PATHS.raw)
            if ok:
                successes += 1
            else:
                failures.append(q.label)
            # Polite delay (skip after the last one)
            if i < len(plan) - 1 and delay > 0:
                time.sleep(delay)

        browser.close()

    log.info("Done. %d / %d succeeded.", successes, len(plan))
    if failures:
        log.warning("Failed: %s. See data/fetch_debug/ for screenshots + HTML dumps.",
                    ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
