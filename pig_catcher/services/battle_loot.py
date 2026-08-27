"""败者五次独立抓猪：只继承永久加成，直接赋予胜者，不进入普通抓猪副作用链。"""

from datetime import UTC, datetime
from uuid import uuid4

from ..domain.battle import dumps, loot_weights, randbelow
from ..domain.battle_catalog import BATTLE_VERSION, BattleError
from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard
from ..domain.errors import CatchCooldownError, DailyCatchLimitError, NoDrawableTemplateError
from ..domain.gameplay import generate_pig_attributes, level_progress
from ..domain.quota import catch_quota_window
from ..domain.rules import choose_rarity
from ..domain.short_codes import new_short_code
from ..domain.special_content import KFC_PIG_TEMPLATE_ID, is_crazy_thursday
from ..infrastructure.repositories.dispatch import iso_ms
from ..infrastructure.repositories.economy import EconomyRepository
from ..infrastructure.repositories.gameplay import GameplayRepository
from ..infrastructure.repositories.restrictions import CATCH_WINDOW_LIMIT, RestrictionRepository
from ..version import RULESET_VERSION
from .battle_views import view
from .command_state import iso_timestamp
from .gameplay import _cooldown_remaining


async def claim_loot(service, session, identity, now_ms: int, key: str):
    row = await session.fetch_one(
        """SELECT l.*,b.random_seed FROM battle_loot l
        JOIN battle_matches b USING(battle_id) WHERE l.actor_id=? AND l.scope_id=? AND l.used<5
        ORDER BY l.created_ms,b.sequence LIMIT 1""",
        (identity.player_id, identity.scope.value),
    )
    if not row:
        raise BattleError("没有待交付的战利品次数；仅自然力竭败者获得额外5次，猪直接归胜者。")
    grant = dict(row)
    recipient = await session.fetch_one(
        "SELECT * FROM players WHERE player_id=? AND scope_id=?", (grant["recipient_id"], identity.scope.value)
    )
    if not recipient:
        raise BattleError("胜者档案异常，暂缓交付，剩余次数保留。")
    await service.check_participants(session, identity.scope.value, [identity.player_id, grant["recipient_id"]], now_ms)
    game, economy = GameplayRepository(), EconomyRepository()
    now_dt, now = datetime.fromtimestamp(now_ms / 1000, UTC), iso_ms(now_ms)
    window = catch_quota_window(
        now_dt, refresh_hours=service.catching.quota_refresh_hours, timezone_name=service.catching.daily_reset_timezone
    )
    _, total, last = await game.catch_usage(
        session,
        player_id=identity.player_id,
        window_start=iso_timestamp(window.start),
        window_end=iso_timestamp(window.end),
    )
    penalty = await RestrictionRepository().active_restriction(
        session, player_id=identity.player_id, restriction_type=CATCH_WINDOW_LIMIT, now=now
    )
    if penalty and total >= max(0, int(penalty["limit_value"] or 0)):
        raise DailyCatchLimitError("账号处于抓猪限额期，本时段次数已用尽；战利品次数保留，不能绕过限额。")
    cooldown = _cooldown_remaining(
        now=now_dt, last_acquired_at=last, cooldown_seconds=service.catching.cooldown_seconds
    )
    if cooldown:
        raise CatchCooldownError(cooldown)
    feed = await game.get_feed_level(session, player_id=identity.player_id)
    experience = await game.get_player_experience(session, player_id=identity.player_id)
    level = level_progress(experience).level
    cloud = await economy.six_star_progress_stacks(session, player_id=identity.player_id)
    templates = await game.list_drawable_pig_templates(session, scope_id=identity.scope.value)
    if not is_crazy_thursday(now_dt, timezone_name=service.catching.daily_reset_timezone):
        templates = [t for t in templates if t["template_id"] != KFC_PIG_TEMPLATE_ID]
    buckets = {star: [t for t in templates if t["rarity"] == star] for star in range(1, 7)}
    weights = loot_weights(level=level, feed=feed, cloud=cloud, six_available=bool(buckets[6]))
    if any(weight > 0 and not buckets[index + 1] for index, weight in enumerate(weights)):
        raise NoDrawableTemplateError("本群素材池不完整，已保留战利品次数，请联系管理员。")
    ordinal = grant["used"] + 1
    prefix = f"loot:{ordinal}"
    seed = grant["random_seed"]
    rarity_roll = randbelow(seed, prefix + ":rarity", 1 << 53) / (1 << 53)
    rarity = choose_rarity(weights, rarity_roll)
    candidates = buckets[int(rarity)]
    template_roll = randbelow(seed, prefix + ":template", len(candidates))
    template = candidates[template_roll]
    attribute_rolls = tuple(randbelow(seed, prefix + f":attribute:{index}", 1 << 53) / (1 << 53) for index in range(5))
    attributes = generate_pig_attributes(
        rarity=rarity,
        length_min=float(template["length_min"]),
        length_max=float(template["length_max"]),
        weight_min=float(template["weight_min"]),
        weight_max=float(template["weight_max"]),
        fat_profile=str(template["fat_profile"]),
        random_values=attribute_rolls,
    )
    for _ in range(64):
        code = new_short_code()
        if not await game.short_code_exists(session, code):
            break
    else:
        raise BattleError("无法生成唯一编号，次数已保留。")
    pig_id = uuid4().hex
    snapshot = {
        "source": "battle-loot",
        "battle_version": BATTLE_VERSION,
        "battle_id": grant["battle_id"],
        "ordinal": ordinal,
        "actor_id": identity.player_id,
        "recipient_id": grant["recipient_id"],
        "pig_instance_id": pig_id,
        "weights": weights,
        "level": level,
        "feed": feed,
        "cloud": cloud,
        "rarity_roll": rarity_roll,
        "template_roll": template_roll,
        "attribute_rolls": attribute_rolls,
        "remaining": 5 - ordinal,
        "normal_catch": False,
        "weekly_value": 0,
        "experience_reward": 0,
        "coin_reward": 0,
    }
    await game.insert_pig_instance(
        session,
        values={
            "pig_instance_id": pig_id,
            "short_code": code,
            "scope_id": identity.scope.value,
            "owner_player_id": grant["recipient_id"],
            "template_id": template["template_id"],
            "template_version": template["template_version"],
            "rarity": int(rarity),
            "display_name_snapshot": template["display_name"],
            "size_value": attributes.size_value,
            "size_percentile": attributes.size_percentile,
            "weight_value": attributes.weight_value,
            "weight_percentile": attributes.weight_percentile,
            "fat_ratio": attributes.fat_ratio,
            "official_value": attributes.official_value,
            "ruleset_version": RULESET_VERSION,
            "random_snapshot_json": dumps(snapshot),
            "acquired_at": now,
            "updated_at": now,
        },
    )
    discovered = await game.upsert_pig_catalog(
        session,
        player_id=grant["recipient_id"],
        template_id=template["template_id"],
        size_value=attributes.size_value,
        weight_value=attributes.weight_value,
        now=now,
    )
    await session.execute(
        "INSERT INTO battle_loot_deliveries VALUES(?,?,?,?,?,?)",
        (grant["battle_id"], ordinal, pig_id, key, dumps(snapshot), now_ms),
    )
    await session.execute("UPDATE battle_loot SET used=used+1 WHERE battle_id=?", (grant["battle_id"],))
    for pid, role in ((identity.player_id, "actor"), (grant["recipient_id"], "recipient")):
        await service.repo.fact(
            session,
            pid,
            identity.scope.value,
            grant["battle_id"],
            f"loot:{ordinal}",
            now_ms,
            {**snapshot, "role": role},
        )
    recipient_name = safe_display_name(recipient["display_name"], recipient["platform_user_id"])
    return view(
        identity,
        "战利品抓猪 · 已交付",
        battle_id=grant["battle_id"],
        round_label=f"第{ordinal}/5次 · 归胜者所有",
        banner=(
            f"{safe_display_name(identity.display_name, identity.user_id)} 抓到的 {template['display_name']} "
            f"已直接进入 {recipient_name} 的背包。"
        ),
        pigs=(
            DispatchPigCard(
                template["display_name"],
                code,
                int(rarity),
                str(template["image_relpath"]),
                ("战利品", "已交付"),
                f"{attributes.size_value:g}cm · {attributes.weight_value:g}kg · 价值{attributes.official_value}猪币",
                False,
                template["template_id"],
            ),
        ),
        stats=(
            Line("本场剩余", f"{5 - ordinal}/5", "不消耗正常额度"),
            Line("最终归属", recipient_name, "新增图鉴" if discovered else "已有图鉴"),
            Line("永久加成", f"Lv.{level} · 饲料{feed}级", f"云冻{cloud}层"),
        ),
        panels=(
            Panel(
                "本次最终概率",
                (Line("星级概率", "  ".join(f"{index + 1}★{weight:.3f}%" for index, weight in enumerate(weights))),),
                "仅采用败者等级、饲料与云冻永久加成。临时道具、美食、群术式均不参与也不消耗。",
            ),
        ),
        hints=("未用次数跨日保留，多场按完成顺序；不重复发猪币/经验，不计普通抓猪次数或本周抓猪价值。",),
    )
