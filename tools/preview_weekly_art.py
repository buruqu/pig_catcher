"""Compose registered event artwork into a self-contained, non-award preview."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pig_catcher.domain.weekly_competitions import WEEKLY_COMPETITIONS_BY_SEASON  # noqa: E402
from pig_catcher.rendering.cosmetics import cosmetic_detail, weekly_event_art  # noqa: E402


async def run(args):
    definition = WEEKLY_COMPETITIONS_BY_SEASON[args.season]
    art = weekly_event_art(args.season)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    def picture(key, cls=""):
        detail = cosmetic_detail(key, variant="detail")
        if not detail["available"]:
            raise ValueError(f"Missing event art: {key}")
        return f'<img class="{cls}" src="{detail["image_data_url"]}" alt="{escape(detail["name"])}">'

    plates = "".join(
        f"<figure>{picture(art['ranks'][rank])}<figcaption>{caption}</figcaption></figure>"
        for rank, caption in (
            (1, "金色冠军 · 第1名"),
            (2, "银蓝亚军 · 第2名"),
            (3, "赤铜季军 · 第3名"),
            (10, "前十纪念牌 · 第4—10名"),
        )
    )
    starts = datetime.fromisoformat(definition.fixed_starts_at)
    ends = datetime.fromisoformat(definition.fixed_ends_at)
    period_text = f"{starts:%Y年%m月%d日%H:%M}—{ends:%m月%d日%H:%M} · 北京时间"
    metric_text = f"{definition.metric_label}越多，排名越靠前。"
    medal_name = escape(cosmetic_detail(art["medal"], variant="detail")["name"])
    frame_name = escape(cosmetic_detail(art["frame"], variant="detail")["name"])
    card = f"data:image/png;base64,{base64.b64encode(args.card.read_bytes()).decode()}"
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:'Microsoft YaHei',sans-serif;background:#fff9f2;color:#315b4d}}
    main{{width:1128px;padding:36px;border:2px solid #d5ad6a;background:linear-gradient(150deg,#fff7eb,#fff,#fff0f0)}}
    h1{{font-size:50px;margin:8px 0 14px}}h2{{font-size:26px;margin:20px 0 8px}}
    p{{font-size:18px;line-height:1.7;margin:8px 0}}
    .kicker{{letter-spacing:3px;font-size:17px;color:#a17635}}.hero{{width:100%;margin:12px 0 18px}}
    .plates{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}figure{{margin:0;text-align:center}}
    .plates img{{width:100%}}figcaption{{font-size:18px;margin:6px 0 14px}}
    .bottom{{display:grid;grid-template-columns:270px 1fr;gap:30px;
      border-top:1px solid #d9c3a0;margin-top:22px;padding-top:18px}}
    .medal{{width:200px;display:block;margin:0 auto}}.frame{{width:184px;display:block;margin:14px auto}}
    .sample{{width:100%;display:block}}.note{{font-size:15px;color:#87745f;text-align:center;margin:20px 0 0}}
    </style><main data-preview><div class="kicker">PiG Dream! · 第{args.season}期活动 · 美术预览</div>
    <h1>{escape(definition.name)}</h1><p>卷起热爱，端出你的王冠！</p>
    <p>{period_text}<br>以群内正式开幕公告为准。{escape(metric_text)}</p>
    {picture(art["title"], "hero")}<div class="plates">{plates}</div>
    <div class="bottom"><section><h2>{medal_name}</h2>{picture(art["medal"], "medal")}
    <h2>{frame_name}</h2>{picture(art["frame"], "frame")}<p>前十专属纪念<br>获奖后可自由佩戴</p></section>
    <section><h2>边框装备实机样式</h2><img class="sample" src="{card}" alt="离线抓猪卡边框示意"></section></div>
    <p class="note">美术与排版预览，不是获奖通知；抓猪卡内为离线测试样例。</p></main></html>'''
    page_path = output / "index.html"
    page_path.write_text(html, encoding="utf-8")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser))
        try:
            page = await browser.new_page(viewport={"width": 1128, "height": 1200}, device_scale_factor=1)
            await page.route("https://**/*", lambda route: route.abort())
            await page.route("http://**/*", lambda route: route.abort())
            await page.goto(page_path.as_uri())
            await page.evaluate("document.fonts.ready")
            await page.evaluate("Promise.all([...document.images].map(i=>i.decode()))")
            diagnostics = await page.locator("main").evaluate("""root=>({
              overflow:root.scrollWidth>root.clientWidth+2,
              broken:[...root.querySelectorAll('img')].filter(i=>!i.naturalWidth).length,
              clipped:[...root.querySelectorAll('p,h1,h2,figcaption')].filter(e=>e.scrollWidth>e.clientWidth+2).length
            })""")
            if any(diagnostics.values()):
                raise ValueError(diagnostics)
            await page.locator("main").screenshot(path=str(output / f"{definition.name}-活动美术一览.png"))
        finally:
            await browser.close()
    (output / "report.json").write_text(json.dumps(diagnostics), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--browser", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    asyncio.run(run(parser.parse_args()))
