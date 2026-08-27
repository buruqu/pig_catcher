"""猪猪远行社的纯规则；与抓猪概率、群术式和时间调度器无关。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GameplayError

DISPATCH_VERSION = 1
BLOCK_MS = 4 * 60 * 60 * 1000
MATERIAL_SCALE = 10_000_000
DURATIONS = (4, 8, 12, 24)
PROFICIENCY_HOURS = (12, 36, 72, 120, 192)
MATERIALS = {
    "training-ore": "训练矿石",
    "machine-parts": "机关零件",
    "agility-fiber": "灵巧纤维",
    "stage-components": "舞台组件",
    "travel-supplies": "旅行补给",
    "travel-notes": "远行手记",
}
BASIC_MATERIALS = tuple(MATERIALS)[:4]
TAGS = frozenset(("后勤", "搜寻", "搬运", "采掘", "机械", "研究", "灵巧", "探路", "音乐器材", "交涉"))


class DispatchError(GameplayError):
    """可直接向玩家解释的派遣操作错误。"""


@dataclass(frozen=True, slots=True)
class Region:
    region_id: str
    name: str
    material: str
    tags: tuple[str, str]
    fee: int
    attribute: str
    prefer_large: bool
    souvenirs: tuple[str, ...]
    story: str


REGIONS = (
    Region(
        "grassland",
        "青草近郊",
        "travel-supplies",
        ("后勤", "搜寻"),
        0,
        "size",
        False,
        ("露水便签", "四叶草书签", "晨雾明信片", "小径邮戳"),
        "猪猪沿着露水找到了回家的小径，给大家打包了青草便当。",
    ),
    Region(
        "echo-mine",
        "回声矿洞",
        "training-ore",
        ("搬运", "采掘"),
        20,
        "weight",
        True,
        ("回声石片", "矿灯票根", "银纹拓片", "矿洞邮戳"),
        "矿洞把哼声还了回来。猪猪认真回了三次礼，才想起那是回声。",
    ),
    Region(
        "old-workshop",
        "废旧工坊",
        "machine-parts",
        ("机械", "研究"),
        20,
        "size",
        False,
        ("黄铜齿轮挂件", "旧图纸残页", "小扳手徽记", "工坊邮戳"),
        "停转的机器又亮了一小会儿，猪猪为这次修复郑重盖了蹄印。",
    ),
    Region(
        "windbell-forest",
        "风铃林地",
        "agility-fiber",
        ("灵巧", "探路"),
        20,
        "size",
        False,
        ("风铃叶签", "林间风笛", "月光纤维结", "林地邮戳"),
        "风铃响起时，它们暂时忘了赶路，把这一阵风夹进了游记。",
    ),
    Region(
        "old-stage-warehouse",
        "旧舞台仓库",
        "stage-components",
        ("音乐器材", "交涉"),
        20,
        "weight",
        True,
        ("退役拨片", "老舞台票根", "霓虹电路片", "舞台邮戳"),
        "旧追光灯亮了起来。没有观众也没关系，搬运间隙同样值得一次谢幕。",
    ),
)
REGIONS_BY_ID = {region.region_id: region for region in REGIONS}

# 按区域和奖励分支编写的旅行片段，不使用网络或模型临时生成。
# 出发后的实际候选会写入旅程快照，阅读游记不会重新抽取故事。
ENCOUNTER_STORIES = {
    "grassland": {
        "materials": "小队沿着车辙找到一处野餐旧营地，把散落的干净补给仔细装进了行囊。",
        "notes": "路口的老邮筒夹着一页路线手记，猪猪认真补画了容易走错的那条岔路。",
        "souvenir": "一阵风翻开草丛，露出一件小小的旅行纪念。它们决定把今天的好运带回家。",
        "recipe": "巡路员教了它们一招收拾行李的办法，还把器具配方和一件样品交给了小队。",
    },
    "echo-mine": {
        "materials": "小队循着清脆的敲击声找到一条浅矿脉，只采走松动的矿石，给岩壁留下了支撑。",
        "notes": "旧矿灯旁有一本防迷路笔记，猪猪照着回声的方向，补上了几处转弯标记。",
        "souvenir": "离开矿道前，它们在废弃值班亭找到一件旧纪念，像是矿洞寄来的一封回信。",
        "recipe": "退休矿工演示了一件随身器具的用法，小队收下配方与样品，还多听了一段矿洞故事。",
    },
    "old-workshop": {
        "materials": "落满灰尘的抽屉终于拉开了。猪猪把还能使用的小零件分门别类，没带走生锈的废料。",
        "notes": "工作台下压着一页修理记录，小队把褪色的尺寸抄清，又在旁边画了一只满意的猪。",
        "souvenir": "旧机器吐出最后一张检修签，旁边还挂着一件小纪念。它们郑重收好了这次来访的证明。",
        "recipe": "一本工具册没有被雨水打湿，猪猪照图装好样品，连同完整配方一起打包。",
    },
    "windbell-forest": {
        "materials": "风吹下几束柔韧的纤维，小队只收集已经脱落的部分，让枝头的风铃继续歌唱。",
        "notes": "林间向导送来一页辨风手记，猪猪学会先听铃声，再决定下一段路往哪里走。",
        "souvenir": "树洞里安静躺着一件林地纪念，像是昨天的风特意留下的礼物。小队给树洞放回一片落叶。",
        "recipe": "守林人分享了一张旅行器具配方，把试好的成品送给小队，叮嘱它们天黑前记好路标。",
    },
    "old-stage-warehouse": {
        "materials": "旧音箱旁还有一箱可用的舞台组件，小队逐件检查接口，给下一次演出攒下可靠的配件。",
        "notes": "调音台下留着一页演出手记，上面记的不是掌声，而是每一次按时亮起的灯。",
        "souvenir": "小队在票根盒里找到一件舞台纪念，虽然灯光已经熄灭，那晚的热闹好像还在。",
        "recipe": "仓库管理员讲解了巡路器具的收纳办法，并交给它们完整配方和一件备用成品。",
    },
}


@dataclass(frozen=True, slots=True)
class TravelTool:
    tool_id: str
    name: str
    costs: tuple[tuple[str, int], ...]
    summary: str


TOOLS = (
    TravelTool(
        "region-map",
        "区域地图",
        (("travel-supplies", 3), ("machine-parts", 1)),
        "返程时最多将4份通用补给等量改成指定基础材料，不改近郊主产物。",
    ),
    TravelTool(
        "souvenir-camera",
        "纪念相机",
        (("travel-supplies", 4), ("agility-fiber", 2)),
        "命中纪念品分支时优先未拥有的本区纪念品，不增加奇遇概率。",
    ),
    TravelTool(
        "encounter-compass",
        "奇遇罗盘",
        (("travel-supplies", 8), ("machine-parts", 4), ("travel-notes", 1)),
        "本趟第一次奇遇提供两个固定候选；返程后选择，下次出发仍未选则按预设选择。",
    ),
    TravelTool(
        "sorting-box",
        "整理箱",
        (("travel-supplies", 2), ("machine-parts", 1)),
        "返程后将指定材料超出保留量的整数部分按3:1整理成另一种基础材料。",
    ),
)
TOOLS_BY_ID = {tool.tool_id: tool for tool in TOOLS}


def region_definition(value: str) -> Region:
    normalized = str(value).strip().casefold()
    for region in REGIONS:
        if normalized in (region.name.casefold(), region.region_id):
            return region
    raise DispatchError(f"未找到“{value}”，请先查看 /猪猪派遣 路线 或 /派遣背包。")


def tool_definition(value: str) -> TravelTool:
    normalized = str(value).strip().casefold()
    for tool in TOOLS:
        if normalized in (tool.name.casefold(), tool.tool_id):
            return tool
    raise DispatchError(f"未找到器具“{value}”，请先查看 /派遣背包 配方。")


def material_id(value: str, *, basic_only: bool = False) -> str:
    normalized = str(value).strip().casefold()
    for key, name in MATERIALS.items():
        if normalized in (key, name.casefold()) and (not basic_only or key in BASIC_MATERIALS):
            return key
    raise DispatchError("材料名称不正确；转换只支持矿石、机关零件、灵巧纤维和舞台组件的完整名称。")


def proficiency(hours: int) -> int:
    return sum(hours >= threshold for threshold in PROFICIENCY_HOURS)


def team_slots(effective_seconds: int) -> int:
    return 1 + int(effective_seconds >= 12 * 3600) + int(effective_seconds >= 72 * 3600)


def normalized_attribute(value: object, minimum: object, maximum: object) -> float:
    try:
        number, low, high = float(value), float(minimum), float(maximum)
    except (ValueError, TypeError):
        return 0.5
    if not all(math.isfinite(x) for x in (number, low, high)) or high <= low:
        return 0.5
    return min(1.0, max(0.0, (number - low) / (high - low)))


def team_bonus(members: list[dict[str, Any]], region: Region) -> dict[str, int]:
    if not 1 <= len(members) <= 3:
        raise DispatchError("每支派遣队需要1至3只不同的猪。")
    if len({member["pig_instance_id"] for member in members}) != len(members):
        raise DispatchError("同一只猪不能在队伍里出现两次。")
    high_count = sum(int(member["rarity"]) >= 4 for member in members)
    if high_count > 1 or high_count == len(members):
        raise DispatchError("每队至少一只1至3星猪，最多带一只4至6星猪。")
    covered = set().union(*(set(member["tags"]) for member in members))
    quality = []
    for member in members:
        q = float(member[f"{region.attribute}_q"])
        if not math.isfinite(q) or not 0 <= q <= 1 or not 0 <= int(member["proficiency"]) <= 5:
            raise DispatchError("猪猪的派遣属性快照不合法。")
        quality.append(q if region.prefer_large else 1 - q)
    return {
        "low_star_ppm": 100_000 if high_count == 0 else 0,
        "tags_ppm": 50_000 * len(covered.intersection(region.tags)),
        "attribute_ppm": round(sum(quality) / len(members) * 50_000),
        "proficiency_ppm": round(sum(int(m["proficiency"]) for m in members) / len(members) * 10_000),
    }


def block_yield(member_count: int, bonus_ppm: int) -> tuple[int, int]:
    """返回定点主产物／通用补给，绝不对每块向上取整。"""
    if member_count not in (1, 2, 3) or not 0 <= bonus_ppm <= 300_000:
        raise DispatchError("派遣产量快照不合法。")
    tenths = (4, 7, 10)[member_count - 1]
    return 6 * tenths * (1_000_000 + bonus_ppm), 2 * tenths * (1_000_000 + bonus_ppm)


def exploration_step(tenths: int, member_count: int, misses: int, roll: float) -> tuple[int, int, bool, bool]:
    if not (0 <= roll < 1 and 0 <= misses <= 9 and 0 <= tenths <= 9 and 1 <= member_count <= 3):
        raise DispatchError("奇遇状态或随机数越界。")
    accumulated = tenths + (4, 7, 10)[member_count - 1]
    if accumulated < 10:
        return accumulated, misses, False, False
    forced = misses == 9
    hit = forced or roll < 0.1
    return accumulated - 10, 0 if hit else misses + 1, hit, forced


def random_at(seed: str, block: int, key: str) -> float:
    """随机种子仅写内部快照；按块与用途定址，重试和领取顺序不重抽。"""
    digest = hashlib.sha256(f"{seed}|{block}|{key}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") >> 11) / (1 << 53)


def souvenir_id(region_id: str, index: int) -> str:
    return f"dispatch-souvenir-{region_id}-{index + 1}"


def encounter_options(
    region: Region,
    seed: str,
    block: int,
    tags: set[str],
    *,
    camera: bool,
    known: set[str],
    count: int = 1,
) -> list[dict[str, Any]]:
    """区域事件是静态作者内容；特长只调整分支，不改10%判定或保底。"""
    branches = ["materials"] * 4 + ["notes"] * 2 + ["souvenir"] * 3 + ["recipe", "story"]
    if set(region.tags).issubset(tags):
        branches += ["notes", "souvenir"]
    first = int(random_at(seed, block, "event") * len(branches))
    if count not in (1, 2):
        raise DispatchError("奇遇候选数量不合法。")
    options = []
    for ordinal in range(count):
        branch = branches[(first + ordinal * 3) % len(branches)]
        if ordinal and branch == options[0]["kind"]:
            branch = next(kind for kind in branches[(first + 1) :] + branches[: first + 1] if kind != branch)
        option: dict[str, Any] = {
            "kind": branch,
            "story": ENCOUNTER_STORIES.get(region.region_id, {}).get(branch, region.story),
            "event_id": f"{region.region_id}:{branch}",
        }
        pick = random_at(seed, block, f"reward-{ordinal}")
        if branch == "materials":
            option.update(material_id=region.material, quantity=1 + int(pick * 3))
        elif branch == "notes":
            option.update(material_id="travel-notes", quantity=1)
        elif branch == "souvenir":
            indices = list(range(len(region.souvenirs)))
            fresh = [i for i in indices if souvenir_id(region.region_id, i) not in known]
            if camera and fresh:
                indices = fresh
            index = indices[int(pick * len(indices))]
            option.update(souvenir_id=souvenir_id(region.region_id, index), name=region.souvenirs[index], quantity=1)
        elif branch == "recipe":
            tool = TOOLS[int(pick * len(TOOLS))]
            option.update(tool_id=tool.tool_id, name=tool.name, quantity=1)
        options.append(option)
    return options


def load_specialties() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).with_name("data") / "dispatch_specialties.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = {key: tuple(tags) for key, tags in data["templates"].items()}
    if any(not 1 <= len(tags) <= 2 or not set(tags).issubset(TAGS) for tags in mapping.values()):
        raise ValueError("派遣模板特长目录不合法")
    return mapping


SPECIALTIES = load_specialties()


def specialties(template_id: str) -> tuple[str, ...]:
    # 未录入的新模板仍可旅行；中性后勤不按名称、售价或星级推断。
    return SPECIALTIES.get(template_id, ("后勤",))


def safe_display_name(name: str, stable_user_id: str = "") -> str:
    text = str(name).strip()
    if not text or text == stable_user_id or text.isdigit() or (len(text) >= 24 and text.isalnum()):
        return "未命名群友"
    return text[:128]
