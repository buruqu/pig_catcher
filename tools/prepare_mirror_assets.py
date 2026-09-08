"""Reproducible, idempotent import of the four user-approved September 8 images."""

import copy
import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GENERATED = Path("C:/Users/Administrator/.codex/generated_images/019fc2ca-b679-7e52-9eef-43098f94cf49")
TEMP = Path("C:/Users/Administrator/AppData/Local/Temp")
SPECS = (
    (
        "luoli-c",
        "洛璃c猪",
        "pig",
        GENERATED / "exec-05c26405-13f4-4e7f-9950-4369cc3a13aa.png",
        "白色猫耳探出抹茶香气，异色眼睛紧盯着杯里的翠绿小山。它把黄瓜当吸管，认真吸上一口芭菲：甜点要慢慢吃，好运也要倒过来尝。",
        ["群友定制", "抹茶猫咪", "黄瓜吸管", "异色瞳"],
        "",
        "luoli-jade-matcha-parfait",
    ),
    (
        "luoli-jade-matcha-parfait",
        "翠玉抹茶芭菲",
        "food",
        GENERATED / "exec-cc664258-372e-4453-a02f-2a90638a3008.png",
        "翠绿冻层、绵软奶霜与抹茶团子盛在晶莹高脚杯里，猫耳小猪藏在甜香之间。一根黄瓜吸管搅动回忆，把昨日平凡的星光翻成下一口惊喜。",
        ["群友定制", "抹茶", "芭菲", "历史镜像"],
        "history-mirror-catch",
        "",
    ),
    (
        "miumiu-flow",
        "空白缪缪流形猪",
        "pig",
        TEMP / "codex-clipboard-abe7848a-2917-477c-b833-764462564e13.png",
        "浅色长发与青绿色的发梢自然垂落，透明的流形在身前轻轻浮动，清澈得像一团会自己流动的水。周围点缀着植物与水珠，映着淡淡的绿色光泽，缪缪安静地待在其中——看起来只是一只猪，身边却已经多出了好几个‘自己’。",
        ["群友定制", "战斗猪", "流形", "青绿发梢", "水镜"],
        "",
        "miumiu-water-mirror-jelly",
    ),
    (
        "miumiu-water-mirror-jelly",
        "流形水镜冻",
        "food",
        TEMP / "codex-clipboard-5e8245a1-b5d8-4049-8685-1a4ddcba507a.png",
        "透明的水晶果冻中，几只流形随着水波静静漂浮，清澈的水面映出一个又一个相似的身影。仔细看去，连其中的猪猪都像是在水中被一一复刻。缪缪只是轻轻碰了碰水面，流形便已经替她把看到的一切留了下来。",
        ["群友定制", "水晶果冻", "流形", "复制"],
        "group-water-mirror",
        "",
    ),
)


def main():
    manifest_path = ROOT / "asset_library/current/assets.json"
    definitions_path = ROOT / "catalogs/formal/pig-and-food-definitions.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    for scope, prefix, directory in (
        ("qq:1092931381", "g1092931381", "1092931381"),
        ("qq:237716658", "g237716658", "237716658"),
        (
            "qq-official:5E5854406D0297D6FEAE696A13E3A339",
            "qo5e5854406d0297d6feae696a13e3a339",
            "5E5854406D0297D6FEAE696A13E3A339",
        ),
        (
            "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
            "qo9ea2810f378fbd7dc3219c56ceab3520",
            "9EA2810F378FBD7DC3219C56CEAB3520",
        ),
    ):
        for slug, name, kind, source, description, tags, effect, paired in SPECS:
            with Image.open(source) as image:
                assert image.format == "PNG" and min(image.size) >= 1000
                image.verify()
            old_id = f"{kind}-{prefix}-" + ("firefly-embrace" if kind == "pig" else "firefly-moonlight-roll")
            entry = copy.deepcopy(next(e for e in manifest["entries"] if e["template_id"] == old_id))
            entry.update(
                template_id=f"{kind}-{prefix}-{slug}",
                group_scope_id=scope,
                display_name=name,
                description=description,
                display_tags=tags,
                recipe_tags=tags,
                effect_id=effect,
                effect_params={},
                paired_food_template_id=f"food-{prefix}-{paired}" if paired else "",
                source="用户于2026-09-08提供并授权发布的定制素材",
            )
            entry["image"] = f"media/{'定制猪群友库' if kind == 'pig' else '定制美食库'}/{directory}/{name}.png"
            if kind == "pig":
                entry.update(length_min_cm=35, length_max_cm=145, weight_min_kg=30, weight_max_kg=480)
            else:
                entry.pop("display_tags", None)
            destination = ROOT / "asset_library/current" / entry["image"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            definition = copy.deepcopy(next(e for e in definitions["entries"] if e["template_id"] == old_id))
            definition.update(
                {
                    key: value
                    for key, value in entry.items()
                    if key not in {"image", "source", "license", "consent_status", "fit", "alternate_image"}
                }
            )
            definition["source_path"] = entry["image"].removeprefix("media/")
            for target, item in ((manifest, entry), (definitions, definition)):
                target["entries"] = [e for e in target["entries"] if e["template_id"] != item["template_id"]]
                target["entries"].append(item)
    for path, value in ((manifest_path, manifest), (definitions_path, definitions)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Imported 16 scoped entries from four approved PNGs; source files unchanged.")


if __name__ == "__main__":
    main()
