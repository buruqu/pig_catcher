from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the self-contained 2.0 page as a shareable long image.")
    parser.add_argument("html", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1080)
    args = parser.parse_args()

    html = args.html.resolve(strict=True)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser_path = next(
            path
            for path in (
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            )
            if path.is_file()
        )
        browser = playwright.chromium.launch(headless=True, executable_path=str(browser_path))
        page = browser.new_page(viewport={"width": args.width, "height": 900}, device_scale_factor=1)
        page.goto(html.as_uri(), wait_until="load")
        page.wait_for_timeout(1_000)
        page.evaluate(
            """
            document.querySelectorAll('.reveal').forEach((element) => element.classList.add('in'));
            document.querySelectorAll('img').forEach((image) => { image.loading = 'eager'; });
            """
        )
        page.wait_for_timeout(500)
        page.screenshot(path=str(output), full_page=True, type="jpeg", quality=88)
        browser.close()

    print(output)


if __name__ == "__main__":
    main()
