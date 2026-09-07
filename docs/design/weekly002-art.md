# 第二期寿司活动美术来源与复现

生成方式：内置imagegen；本轮1次生成、无编辑轮次。图像母版原字节保留。
母版：`pig_catcher/rendering/assets/ui/masters/weekly-002.png`；2172×724。
其他徽记与九宫格边框为`tools/build_cosmetic_art.py`内原生SVG，再以本机无界面Chromium精确导出。
未下载外部素材，未使用官方商标。AI母版无文字；汉字、期号与名次由本机字体排版。
运行`uv run python tools/build_cosmetic_art.py --theme weekly-002`，只重建第二期，保留旧活动成品。
所有来源、生成器、产物与尺寸的SHA-256见正式外观`manifest.json`。

## 完整生成提示词

Create a brand-new polished collectible event title-plate BACKGROUND for the cute Chinese pig-collecting rhythm-game-inspired mini game PiG Dream!. Illustration-story / premium mobile game reward asset. Theme: Sushi Platter King, sushi banquet celebration. Wide horizontal canvas approximately 3:1, 2100 by 700 feel. This is an original design, no existing logos. Delicate salmon pink ribbons, polished champagne gold filigree, ivory porcelain, dark nori-green accents, translucent little stars and rice-grain motifs. At the left 5-24% leave a large empty cream circular medallion inside gilded sushi-chef crown/laurel trim for an independently typeset rank number. At 25-38% a cheerful tiny pink pig chef wearing a clean white chef cap holding a gorgeous miniature pig-faced salmon nigiri and tamago sushi platter, art concentrated here, inviting and adorable, no human character. From 40% to 90% an uncluttered warm ivory central writing panel, exceptionally clean and bright, no marks so precise Chinese title can be typeset later. Sculpted metallic edges, enamel sparkle, coordinated fine sash along top and bottom, small nori roll with pig snout emblem at lower right. Full plate visible with 2% white outer padding, front-facing flat UI asset, crisp tiny details, high-quality hand-painted anime-game illustration, professional game live-event rank banner, not a photographed object. Absolutely NO text, NO numbers, NO letters, NO watermark. Do not put food or busy motifs over the central blank writing panel.
