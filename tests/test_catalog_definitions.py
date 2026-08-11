"""Formal 2B catalog metadata remains complete, stable and group-safe."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = PROJECT_ROOT / "catalogs" / "formal" / "pig-and-food-definitions.json"
PAIRED_GROUP_SCOPES = (
    (
        "qq:1092931381",
        "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    ),
    (
        "qq:237716658",
        "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
    ),
)


def _entries() -> list[dict[str, object]]:
    payload = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    return list(payload["entries"])


def test_formal_catalog_has_all_215_named_assets_and_stable_ids() -> None:
    entries = _entries()
    assert len(entries) == 215
    assert len({entry["template_id"] for entry in entries}) == 215
    assert len({entry["source_path"] for entry in entries}) == 215
    assert all(str(entry["description"]).strip() for entry in entries)
    pig_counts = Counter(
        int(entry["rarity"])
        for entry in entries
        if entry["kind"] == "pig"
    )
    food_counts = Counter(
        int(entry["rarity"])
        for entry in entries
        if entry["kind"] == "food"
    )
    assert pig_counts == {1: 20, 2: 20, 3: 21, 4: 28, 5: 31, 6: 32}
    assert food_counts == {1: 3, 2: 6, 3: 7, 4: 8, 5: 7, 6: 32}


def test_high_rarity_food_effects_cover_new_gameplay_families() -> None:
    foods = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "food"
    }
    assert foods["猪咪虾寿司"]["effect_id"] == "next-catch-quality"
    assert foods["猪猪玉子烧"]["effect_id"] == "next-cook-quality"
    assert foods["猪寿司拼盘"]["effect_params"] == {"count": 2}
    assert foods["猪寿司拼盘"]["effect_id"] == "today-window-catches"
    assert foods["一猪六吃"]["effect_id"] == "next-six-star-cook-bonus"
    assert foods["一猪六吃"]["effect_params"] == {"bonus_percent": 15}
    assert foods["一盒油炸猪"]["effect_id"] == "current-window-catches"
    assert foods["一盒油炸猪"]["effect_params"] == {"count": 2}
    assert foods["猪利猪"]["effect_id"] == "next-small-six-star-catch"
    assert foods["猪利猪"]["effect_params"] == {"bonus_percent": 1}
    assert foods["猪籽军舰"]["effect_params"] == {"rarity": 5, "multiplier": 2.0}
    assert foods["猪猪玉子烧"]["effect_params"] == {"shift_percent": 15, "uses": 1}
    assert foods["猪饺"]["effect_id"] == "next-stackable-six-star-cook-bonus"
    assert foods["猪饺"]["effect_params"] == {"bonus_percent": 1, "max_stacks": 5}
    assert foods["黑猪麻汤圆"]["effect_id"] == "next-giant-five-star-catch"
    assert foods["黑猪麻汤圆"]["effect_params"] == {
        "five_star_multiplier": 3.0,
        "stature_bias": 0.5,
        "giant_template_multiplier": 4.0,
    }
    assert foods["猪猪白菜炖粉条"]["effect_id"] == "next-collaboration-catch"
    assert foods["猪猪白菜炖粉条"]["effect_params"] == {
        "three_star_percent": 15,
        "four_star_percent": 55,
        "five_star_percent": 30,
    }
    assert foods["猪咪莓蛋糕"]["effect_id"] == "next-extreme-five-star-cook"
    assert foods["猪咪莓蛋糕"]["effect_params"] == {"five_star_percent": 85}
    assert foods["猪果冻"]["effect_params"] == {
        "multiplier": 3.0,
        "uses": 3,
    }
    assert foods["猪皮奶"]["effect_id"] == "next-five-six-star-catch"
    assert foods["猪皮奶"]["effect_params"] == {
        "five_star_bonus_percent": 20,
        "six_star_bonus_percent": 3,
    }
    assert foods["小马猪蒙布朗"]["effect_params"] == {
        "six_star_percent": 60,
        "uses": 5,
    }
    assert foods["雾蓝键盘大福"]["effect_params"] == {
        "uses": 10,
        "four_star_percent": 60,
        "five_star_percent": 30,
        "six_star_percent": 10,
    }
    assert foods["雾蓝键盘大福"]["effect_id"] == "next-high-star-catch"
    assert foods["彩彩修车猪慕斯"]["effect_id"] == "next-five-star-cook"
    assert foods["彩彩修车猪慕斯"]["effect_params"] == {"uses": 10}
    assert foods["猪保千猪排轮盘"]["effect_id"] == "even-catch-distribution"
    assert foods["猪保千猪排轮盘"]["effect_params"] == {"uses": 10}
    assert foods["糖醋排骨"]["effect_id"] == "quota-reset"
    assert foods["糖醋排骨"]["effect_params"] == {"count": 1}
    assert foods["猪鼻蛋包饭"]["effect_params"] == {
        "six_star_percent": 60,
        "uses": 2,
    }
    assert foods["撅撅猪派"]["effect_params"] == {"count": 1, "max_bonus": 5}
    assert foods["向你道早猪猪巧克力螺"]["effect_params"] == {"count": 5}
    # 每道不同名菜的效果签名必须唯一（群专属双群复制品除外）
    signatures: dict[tuple[str, str], list[str]] = {}
    for entry in _entries():
        if entry["kind"] != "food" or not entry.get("effect_id"):
            continue
        key = (
            entry["effect_id"],
            str(sorted((entry.get("effect_params") or {}).items())),
        )
        signatures.setdefault(key, []).append(entry["display_name"])
    for names in signatures.values():
        # 同一道菜在多个群的作用域复制允许相同签名
        assert len(set(names)) == 1, f"不同菜品效果重复：{set(names)}"


def test_five_star_food_routes_are_stronger_than_four_star_counterparts() -> None:
    foods = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "food"
    }

    four_catch = foods["猪咪虾寿司"]["effect_params"]
    five_catch = foods["猪果冻"]["effect_params"]
    assert five_catch["multiplier"] > four_catch["multiplier"]
    assert five_catch["uses"] > four_catch["uses"]

    assert foods["猪猪玉子烧"]["effect_params"]["shift_percent"] == 15
    assert foods["猪咪莓蛋糕"]["effect_params"]["five_star_percent"] == 85
    collaboration = foods["猪猪白菜炖粉条"]["effect_params"]
    assert collaboration["four_star_percent"] + collaboration["five_star_percent"] == 85

    assert foods["珍猪奶茶"]["effect_params"] == {"rarity": 5, "multiplier": 2.5}
    assert foods["猪皮奶"]["effect_params"] == {
        "five_star_bonus_percent": 20,
        "six_star_bonus_percent": 3,
    }

    assert foods["一盒油炸猪"]["effect_id"] == "current-window-catches"
    assert foods["猪寿司拼盘"]["effect_id"] == "today-window-catches"
    assert foods["猪寿司拼盘"]["rarity"] > foods["一盒油炸猪"]["rarity"]


def test_semantic_body_ranges_match_visual_scale_without_changing_normal_pigs() -> None:
    pigs = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "pig" and not entry.get("group_scope_id")
    }
    assert pigs["地球猪"]["stature_profile"] == "giant"
    assert pigs["地球猪"]["weight_min_kg"] == 20000
    assert pigs["磁流体约束恒星物质猪"]["weight_max_kg"] == 100000
    assert pigs["Pigseek"]["length_min_cm"] == 300
    assert pigs["猪蹄"]["stature_profile"] == "mini"
    assert pigs["猪鼻"]["weight_max_kg"] == 2
    assert pigs["二维猪"]["weight_max_kg"] == 2
    assert pigs["扁猪"]["weight_max_kg"] == 5
    assert "length_min_cm" not in pigs["普通猪"]


def test_group_custom_assets_are_confined_and_keep_user_text() -> None:
    entries = _entries()
    group_entries = [
        entry
        for entry in entries
        if entry.get("group_scope_id")
    ]
    assert len(group_entries) == 64
    assert {entry["group_scope_id"] for entry in group_entries} == {
        "qq:1092931381",
        "qq:237716658",
        "qq-official:5E5854406D0297D6FEAE696A13E3A339",
        "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
    }
    assert all(
        f"/{str(entry['group_scope_id']).split(':', 1)[1]}/"
        in f"/{entry['source_path']}"
        for entry in group_entries
    )
    descriptions = {
        entry["display_name"]: entry["description"]
        for entry in group_entries
        if entry["group_scope_id"] == "qq:1092931381"
    }
    assert descriptions["撅撅猪"] == "撅撅。"
    assert descriptions["1004猪鼻哥"] == "救我！！！！！晚上救来不及咯！"
    assert {
        "小马猪",
        "小马猪蒙布朗",
        "彩彩修车猪",
        "彩彩修车猪慕斯",
        "ob一串猪",
        "糖醋排骨",
    } <= set(descriptions)
    assert "社区" in descriptions["彩彩修车猪"]
    assert "不是官方职业设定" in descriptions["彩彩修车猪"]
    assert "糖醋排骨" in descriptions["ob一串猪"]

    by_scope = {
        scope: {
            str(entry["display_name"]): {
                key: entry.get(key)
                for key in (
                    "kind",
                    "rarity",
                    "description",
                    "fat_profile",
                    "stature_profile",
                    "length_min_cm",
                    "length_max_cm",
                    "weight_min_kg",
                    "weight_max_kg",
                    "recipe_tags",
                    "effect_id",
                    "effect_params",
                )
            }
            for entry in group_entries
            if entry["group_scope_id"] == scope
        }
        for scope in {str(entry["group_scope_id"]) for entry in group_entries}
    }
    baseline = by_scope[PAIRED_GROUP_SCOPES[0][0]]
    assert len(baseline) == 16
    assert all(scope_catalog == baseline for scope_catalog in by_scope.values())
    for qq_scope, official_scope in PAIRED_GROUP_SCOPES:
        assert by_scope[qq_scope] == by_scope[official_scope]


def test_every_custom_six_star_pig_has_one_same_group_food_pair() -> None:
    entries = _entries()
    by_id = {entry["template_id"]: entry for entry in entries}
    pigs = [
        entry
        for entry in entries
        if entry["kind"] == "pig" and entry["rarity"] == 6
    ]
    foods = {
        entry["template_id"]
        for entry in entries
        if entry["kind"] == "food" and entry["rarity"] == 6
    }
    paired = []
    for pig in pigs:
        paired_id = pig["paired_food_template_id"]
        food = by_id[paired_id]
        assert food["kind"] == "food"
        assert food["rarity"] == 6
        assert food["group_scope_id"] == pig["group_scope_id"]
        paired.append(paired_id)
    assert len(paired) == len(set(paired))
    assert set(paired) == foods


def test_bandori_collaboration_mappings_use_official_profiles_and_five_slots() -> None:
    collabs = {
        entry["display_name"]: entry["collection"]
        for entry in _entries()
        if entry.get("collection")
    }
    assert {
        name: (value["character_name"], value["collection_name"])
        for name, value in collabs.items()
    } == {
        "星星猪": ("户山香澄", "Poppin'Party"),
        "兔吉猪": ("花园多惠", "Poppin'Party"),
        "巧克力猪": ("牛込里美", "Poppin'Party"),
        "面包鼓猪": ("山吹沙绫", "Poppin'Party"),
        "傲娇猪": ("市谷有咲", "Poppin'Party"),
        "红挑染猪": ("美竹兰", "Afterglow"),
        "摩卡猪": ("青叶摩卡", "Afterglow"),
        "大绯猪": ("上原绯玛丽", "Afterglow"),
        "巴巴猪": ("宇田川巴", "Afterglow"),
        "鸫鸫猪": ("羽泽鸫", "Afterglow"),
        "粉音猪": ("千早爱音", "MyGO!!!!!"),
        "企鹅猪": ("高松灯", "MyGO!!!!!"),
        "抹茶猪咪": ("要乐奈", "MyGO!!!!!"),
        "红茶猪": ("长崎素世", "MyGO!!!!!"),
        "熊猫鼓猪": ("椎名立希", "MyGO!!!!!"),
        "偶像猪": ("丸山彩", "Pastel＊Palettes"),
        "天才猪": ("冰川日菜", "Pastel＊Palettes"),
        "淑女猪": ("白鹭千圣", "Pastel＊Palettes"),
        "器材猪": ("大和麻弥", "Pastel＊Palettes"),
        "武士道猪": ("若宫伊芙", "Pastel＊Palettes"),
        "白日梦猪": ("仓田真白", "Morfonica"),
        "潮流猪": ("桐谷透子", "Morfonica"),
        "普通猪": ("广町七深", "Morfonica"),
        "仓鼠猪": ("二叶筑紫", "Morfonica"),
        "提琴猪": ("八潮瑠唯", "Morfonica"),
        "绿茶猪": ("薇欧拉", "梦限大动画"),
        "HAPPY猪": ("弦卷心", "Hello, Happy World!"),
        "歌剧猪": ("濑田熏", "Hello, Happy World!"),
        "迷路猪": ("松原花音", "Hello, Happy World!"),
        "美咲猪": ("奥泽美咲", "Hello, Happy World!"),
        "可乐饼猪": ("育美", "Hello, Happy World!"),
        "米歇尔猪": ("米歇尔", "Hello, Happy World!"),
        "歌姬猪": ("凑友希那", "Roselia"),
        "妈妈猪": ("今井莉莎", "Roselia"),
        "薯条猪": ("冰川纱夜", "Roselia"),
        "魔王猪": ("宇田川亚子", "Roselia"),
        "宅宅猪": ("白金燐子", "Roselia"),
        "LAYER猪": ("LAYER", "RAISE A SUILEN"),
        "LOCK猪": ("LOCK", "RAISE A SUILEN"),
        "摩托猪": ("MASKING", "RAISE A SUILEN"),
        "PAREO猪": ("PAREO", "RAISE A SUILEN"),
        "chuchu猪": ("CHU²", "RAISE A SUILEN"),
    }
    assert all(
        value["total"] == (
            6 if value["collection_id"] == "bandori-hello-happy-world" else
            (1 if value["collection_id"] == "bandori-yumemita-viola" else 5)
        )
        for value in collabs.values()
    )
    assert all(
        str(value["official_profile_url"]).startswith((
            "https://bang-dream.com/",
            "https://anime.bang-dream.com/",
            "https://bang-dream-gbp-en.bushiroad.com/",
        ))
        for value in collabs.values()
    )
    afterglow_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Afterglow"
    }
    assert afterglow_slots == {1, 2, 3, 4, 5}
    poppin_party_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Poppin'Party"
    }
    assert poppin_party_slots == {1, 2, 3, 4, 5}
    morfonica_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Morfonica"
    }
    assert morfonica_slots == {1, 2, 3, 4, 5}
    mygo_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "MyGO!!!!!"
    }
    assert mygo_slots == {1, 2, 3, 4, 5}
    pastel_palettes_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Pastel＊Palettes"
    }
    assert pastel_palettes_slots == {1, 2, 3, 4, 5}
    assert collabs["绿茶猪"]["collection_id"] == "bandori-yumemita-viola"
    assert collabs["绿茶猪"]["slot"] == 1
    hhw_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Hello, Happy World!"
    }
    assert hhw_slots == {1, 2, 3, 4, 5, 6}
    roselia_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Roselia"
    }
    assert roselia_slots == {1, 2, 3, 4, 5}
    raise_a_suilen_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "RAISE A SUILEN"
    }
    assert raise_a_suilen_slots == {1, 2, 3, 4, 5}
    assert collabs["LAYER猪"]["character_id"] == "layer"
    assert collabs["LOCK猪"]["character_id"] == "lock"
    assert collabs["摩托猪"]["character_id"] == "masking"
    assert collabs["PAREO猪"]["character_id"] == "pareo"
    assert collabs["chuchu猪"]["character_id"] == "chu2"
    assert collabs["歌姬猪"]["character_id"] == "yukina"
    assert collabs["妈妈猪"]["character_id"] == "lisa"
    assert collabs["薯条猪"]["character_id"] == "sayo"
    assert collabs["魔王猪"]["character_id"] == "ako"
    assert collabs["宅宅猪"]["character_id"] == "rinko"


def test_new_pigs_keep_reviewed_descriptions_and_rarities() -> None:
    pigs = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "pig"
    }
    assert pigs["猪纵连"]["rarity"] == 3
    assert pigs["猪纵连"]["description"] == (
        "三只小猪首尾相接排成一列，队伍一旦启动就越连越长，谁先掉队谁负责请全队加餐。"
    )
    assert pigs["面包鼓猪"]["rarity"] == 4
    assert pigs["面包鼓猪"]["description"] == (
        "扎着山吹沙绫的侧马尾，一边守着面包一边敲响小鼓；"
        "总把大家照顾得稳稳当当，散场后还会记得给全队留一份加餐。"
    )
    assert pigs["兔吉猪"]["rarity"] == 4
    assert pigs["兔吉猪"]["description"] == (
        "学着花园多惠抱起蓝色吉他，头上的小花和身后的兔子一起听它即兴；"
        "想法总是自由跳脱，弹起琴来却比谁都认真。"
    )
    assert pigs["傲娇猪"]["rarity"] == 5
    assert pigs["傲娇猪"]["description"] == (
        "借来市谷有咲的双马尾，在键盘、乐谱和盆栽之间忙得团团转；"
        "嘴上嫌麻烦，伙伴一开口却总是第一个把演出撑起来。"
    )
    assert pigs["提琴猪"]["rarity"] == 4
    assert pigs["提琴猪"]["collection"]["character_name"] == "八潮瑠唯"
    assert pigs["潮流猪"]["rarity"] == 4
    assert pigs["潮流猪"]["collection"]["character_name"] == "桐谷透子"
    assert pigs["仓鼠猪"]["rarity"] == 5
    assert pigs["仓鼠猪"]["collection"]["character_name"] == "二叶筑紫"
    assert pigs["白日梦猪"]["rarity"] == 5
    assert pigs["白日梦猪"]["collection"]["character_name"] == "仓田真白"
    assert pigs["普通猪"]["rarity"] == 5
    assert pigs["普通猪"]["collection"]["character_name"] == "广町七深"
    assert pigs["抹茶猪咪"]["collection"]["character_name"] == "要乐奈"
    assert pigs["企鹅猪"]["collection"]["character_name"] == "高松灯"
    assert pigs["熊猫鼓猪"]["collection"]["character_name"] == "椎名立希"
    assert pigs["偶像猪"]["rarity"] == 5
    assert pigs["偶像猪"]["collection"]["character_name"] == "丸山彩"
    assert pigs["天才猪"]["rarity"] == 5
    assert pigs["天才猪"]["collection"]["character_name"] == "冰川日菜"
    assert pigs["武士道猪"]["rarity"] == 5
    assert pigs["武士道猪"]["collection"]["character_name"] == "若宫伊芙"
    assert pigs["器材猪"]["rarity"] == 4
    assert pigs["器材猪"]["collection"]["character_name"] == "大和麻弥"
    assert pigs["淑女猪"]["rarity"] == 4
    assert pigs["淑女猪"]["collection"]["character_name"] == "白鹭千圣"
    assert pigs["偶像猪"]["description"] == (
        "戴着丸山彩式的粉色双马尾和舞台耳麦，紧张得冒汗也会把每句歌词唱完；"
        "它像那位最喜欢偶像的偶像一样不轻言放弃，偶尔说话打结，努力却从不打折。"
    )
    assert pigs["天才猪"]["description"] == (
        "借来冰川日菜的薄荷青短发和蓝色吉他，抬眼就把新乐句摸透；"
        "最爱追着能让自己“るんっ♪”的趣事跑，直来直往，却一直把Pastel＊Palettes放在心上。"
    )
    assert pigs["淑女猪"]["description"] == (
        "借来白鹭千圣的浅金长发和奶白贝斯，茶杯与场记板都摆得一丝不乱；"
        "童星出身的演员兼偶像让它冷静、现实又讲究专业，温柔总藏在从容的舞台礼仪后面。"
    )
    assert pigs["器材猪"]["description"] == (
        "顶着大和麻弥的棕灰短发，趴在电子鼓垫前研究旋钮和音色；"
        "前录音室乐手的基本功很稳，一聊器材就越说越快，兴奋时还会漏出一声“フヘヘ”。"
    )
    assert pigs["武士道猪"]["description"] == (
        "编着若宫伊芙的银白双辫，背起紫色键盘，把“武士道”当成每次登台的信念；"
        "性格坦率又一心一意，哪怕方向跑偏，也会用热情把大家重新带回节拍。"
    )
    assert pigs["LAYER猪"]["rarity"] == 4
    assert pigs["LAYER猪"]["description"] == (
        "抱着琥珀色贝斯守在麦克风前，借来LAYER成熟冷静的舞台气场；"
        "平日和群猪保持恰到好处的距离，一开口却能像专业歌姬般驾驭各种曲风，沉稳低音下也藏着炽热。"
    )
    assert pigs["LOCK猪"]["rarity"] == 5
    assert pigs["LOCK猪"]["description"] == (
        "扎起LOCK的雾紫双辫，扶好蓝色吉他和滑落的圆框眼镜；"
        "平时是有点不走运却凡事拼尽全力的苦劳猪，一踏上舞台便被吉他点亮，对Poppin'Party的热爱也从不藏着。"
    )
    assert pigs["摩托猪"]["rarity"] == 4
    assert pigs["摩托猪"]["description"] == (
        "把红黑头盔扣在MASKING式的金发上，看着像要骑摩托冲出后台，真正发动的却是“狂犬”鼓点；"
        "外表有点凶，心里重情又热血，敲完鼓还会悄悄端出一只精致小蛋糕。"
    )
    assert pigs["PAREO猪"]["rarity"] == 5
    assert pigs["PAREO猪"]["description"] == (
        "梳着PAREO的蓝粉双马尾，键盘、星星应援棒和“可爱”缺一不可；"
        "它是Pastel＊Palettes的忠实偶像猪，也把发掘自己的CHU²当成最重要的主人，热情上来时谁都拦不住。"
    )
    assert pigs["chuchu猪"]["rarity"] == 5
    assert pigs["chuchu猪"]["description"] == (
        "戴上CHU²的猫耳耳机站到DJ台前，作词、作曲和制作全都要由自己掌控；"
        "这只年少却专业的制作人猪想用最强音乐改变世界，态度再强势也守礼，手边永远少不了肉干。"
    )
    assert pigs["LOCK猪"]["source_path"].endswith("LOCK猪.png")
    assert pigs["彩彩修车猪"]["paired_food_template_id"].endswith(
        "aya-repair-mousse"
    )
    assert pigs["绿茶猪"]["collection"]["collection_id"] == "bandori-yumemita-viola"
    assert pigs["向你道早猪"]["source_path"].endswith("向你道早猪.gif")
    assert pigs["向你道早猪"]["paired_food_template_id"].endswith(
        "xiangni-daozao-chocolate-cornet"
    )
    assert pigs["软糯丰川祥猪"]["paired_food_template_id"].endswith(
        "mist-blue-keyboard-daifuku"
    )
    assert pigs["HAPPY猪"]["rarity"] == 5
    assert pigs["HAPPY猪"]["collection"]["character_name"] == "弦卷心"
    assert pigs["歌剧猪"]["rarity"] == 5
    assert pigs["歌剧猪"]["collection"]["character_name"] == "濑田熏"
    assert pigs["迷路猪"]["rarity"] == 5
    assert pigs["迷路猪"]["collection"]["character_name"] == "松原花音"
    assert pigs["美咲猪"]["rarity"] == 4
    assert pigs["美咲猪"]["collection"]["character_name"] == "奥泽美咲"
    assert pigs["可乐饼猪"]["rarity"] == 4
    assert pigs["可乐饼猪"]["collection"]["character_name"] == "育美"
    assert pigs["米歇尔猪"]["rarity"] == 3
    assert "米歇尔" in pigs["米歇尔猪"]["description"]
    assert pigs["米歇尔猪"]["collection"]["character_name"] == "米歇尔"
    assert pigs["米歇尔猪"]["collection"]["collection_id"] == "bandori-hello-happy-world"
    assert pigs["米歇尔猪"]["collection"]["slot"] == 6
    assert pigs["米歇尔猪"]["collection"]["total"] == 6
    assert pigs["保千猪"]["rarity"] == 6
    assert pigs["保千猪"]["alternate_image"].endswith("猪保千表情包.png")
    assert pigs["保千猪"]["paired_food_template_id"].endswith(
        "baogian-pork-roulette"
    )
    # Roselia 联动与 ob 一串六星定制（按模板遍历，QQ 官方群与普通群均有复制）
    ob_pigs = [
        entry
        for entry in _entries()
        if entry["kind"] == "pig" and entry["display_name"] == "ob一串猪"
    ]
    assert len(ob_pigs) == 4
    for entry in ob_pigs:
        assert entry["rarity"] == 6
        assert "糖醋排骨" in entry["description"]
        assert entry["paired_food_template_id"].endswith("tangcu-paigu")
        scope_suffix = str(entry["group_scope_id"]).split(":", 1)[1]
        expected_prefix = (
            "food-g" + scope_suffix
            if entry["group_scope_id"].startswith("qq:")
            else "food-qo" + scope_suffix.lower()
        )
        assert entry["paired_food_template_id"].startswith(expected_prefix)
    assert pigs["歌姬猪"]["rarity"] == 5
    assert "凑友希那" in pigs["歌姬猪"]["description"]
    assert pigs["歌姬猪"]["collection"]["character_name"] == "凑友希那"
    assert pigs["妈妈猪"]["rarity"] == 5
    assert "今井莉莎" in pigs["妈妈猪"]["description"]
    assert pigs["薯条猪"]["rarity"] == 4
    assert "侧麻花辫" in pigs["薯条猪"]["description"]
    assert pigs["薯条猪"]["collection"]["character_name"] == "冰川纱夜"
    assert pigs["魔王猪"]["rarity"] == 5
    assert "宇田川亚子" in pigs["魔王猪"]["description"]
    assert pigs["宅宅猪"]["rarity"] == 4
    assert "白金燐子" in pigs["宅宅猪"]["description"]
