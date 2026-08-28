"""只渲染设计样张；不导入游戏服务、不读写数据库，不连接 QQ。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[3]
DESIGN = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "artifacts/art-balance-confirmation-20260828"
SOURCE_NAMES = (
    "地球猪", "糖醋排骨", "口琴猪", "上流猪", "土木猪", "阿拉蕾猪",
    "偶像猪", "器材猪", "巴巴猪", "saki猪", "五条猪", "宿傩猪",
)


async def render(output: Path, browser_path: Path) -> None:
    output = output.resolve()
    output.relative_to((ROOT / "artifacts").resolve())
    output.mkdir(parents=True, exist_ok=True)
    concept = output / "01-title-plates-concept.png"
    if not concept.is_file():
        raise FileNotFoundError("先把本轮生成的概念图复制为01-title-plates-concept.png，不替代为占位图。")
    catalog = json.loads((ROOT / "asset_library/current/assets.json").read_text(encoding="utf-8"))
    assets: dict[str, str] = {}
    hashes: dict[Path, str] = {}
    for name in SOURCE_NAMES:
        matching = [entry for entry in catalog["entries"] if entry["display_name"] == name]
        if name == "糖醋排骨":
            matching = [entry for entry in matching if str(entry["group_scope_id"]).endswith("CEAB3520")]
        if len(matching) != 1:
            raise ValueError(f"样张素材应唯一：{name}，实际{len(matching)}项")
        source = ROOT / "asset_library/current" / matching[0]["image"]
        source.resolve().relative_to((ROOT / "asset_library/current/media").resolve())
        assets[name] = source.as_uri()
        hashes[source] = hashlib.sha256(source.read_bytes()).hexdigest()
    environment = Environment(
        loader=FileSystemLoader(DESIGN),
        autoescape=select_autoescape(default=True),
    )
    html = environment.get_template("samples.html.j2").render(
        assets=assets,
        concept_src=concept.as_uri(),
        crops={
            "雨爱": "12 148 704 211",
            "远行家": "738 148 704 211",
            "全场安可": "12 375 704 211",
        },
        tiers=("#767881", "#338760", "#377fae", "#8257ae", "#ac7b20", "#c64c87"),
    )
    destination = output / "index.html"
    destination.write_text(html, encoding="utf-8")
    checks = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=str(browser_path))
        try:
            page = await browser.new_page(viewport={"width": 1240, "height": 1000})

            async def no_remote(route):
                if route.request.url.startswith(("http://", "https://")):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", no_remote)
            await page.goto(destination.as_uri(), wait_until="load")
            await page.evaluate("document.fonts.ready")
            for sheet in await page.locator("[data-sample]").all():
                label = await sheet.get_attribute("data-sample")
                diagnostic = await sheet.evaluate("""root => {
                  const rect=root.getBoundingClientRect();
                  const isText=el=>el.children.length===0 && el.textContent.trim();
                  const texts=[...root.querySelectorAll('*')].filter(isText);
                  const outside=texts.filter(el=>{const r=el.getBoundingClientRect();
                    return r.left<rect.left-1||r.right>rect.right+1||r.bottom>rect.bottom+1;});
                  const clipped=texts.filter(el=>el.clientWidth>0 &&
                    (el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+3));
                  return {width:Math.round(rect.width),height:Math.round(rect.height),
                    outside:outside.map(el=>el.textContent),clipped:clipped.map(el=>el.textContent),
                    broken:[...root.querySelectorAll('img')].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.alt)};
                }""")
                await sheet.screenshot(path=str(output / f"{label}.png"), animations="disabled")
                diagnostic["sample"] = label
                checks.append(diagnostic)
            # CSS缩放仅用于群聊阅读检查，不改动源图或生成新的游戏素材。
            await page.add_style_tag(content=".sheet{zoom:.2836879433;margin:0 auto 24px}.review-nav{display:none}")
            await page.set_viewport_size({"width": 360, "height": 900})
            for sheet in await page.locator("[data-sample]").all():
                label = await sheet.get_attribute("data-sample")
                await sheet.screenshot(path=str(output / f"{label}-320.png"), animations="disabled")
        finally:
            await browser.close()
    sources_unchanged = all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    report = {"stage": "design-prototype-only", "sources_unchanged": sources_unchanged, "samples": checks}
    (output / "sample-layout-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not sources_unchanged or any(check["outside"] or check["clipped"] or check["broken"] for check in checks):
        raise RuntimeError("设计样张有排版或来源检查问题；不代表业务验收。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    arguments = parser.parse_args()
    asyncio.run(render(arguments.output, arguments.browser))
