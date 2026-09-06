"""Real SQLite money conservation, permissions, contention, replay and picture delivery."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from pig_catcher.domain.errors import DomainValidationError, InsufficientBalanceError, ReceiptConflictError
from pig_catcher.domain.red_packets import PacketRequest, draw_packet_amount, parse_packet_request
from pig_catcher.services.red_packets import RedPacketService
from pig_catcher.services.scheduled_rewards import BIRTHDAY_AT

from .helpers import build_message, create_test_plugin
from .test_admin_commands import _identity
from .test_gameplay import MutableClock
from .test_plugin import _command_kwargs


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1",
        "0 1",
        "1 2",
        "100 101",
        "-1 3",
        "1.5 2",
        "１２ ２",
        "10000001 1",
        "9" * 5000 + " 1",
        "100 1 " + "哈" * 81,
    ],
)
def test_invalid_inputs(text):
    with pytest.raises(DomainValidationError):
        parse_packet_request(text)


def test_integer_draw_conserves_every_coin():
    for total, count in ((1, 1), (100, 100), (1000, 50), (10_000_000, 100)):
        for roll in (0, 0.00001, 0.5, 0.99999999):
            remaining = total
            amounts = []
            for n in range(count, 0, -1):
                amount = draw_packet_amount(remaining, n, roll)
                amounts.append(amount)
                remaining -= amount
            assert min(amounts) >= 1 and sum(amounts) == total and remaining == 0


@pytest.fixture
async def world(tmp_path):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"access": {"admin_user_ids": ["admin"]}})
    await plugin._social_rewards_runner.stop()
    clock = MutableClock(BIRTHDAY_AT - timedelta(hours=1))
    service = RedPacketService(plugin.database, clock=clock)
    actor = _identity(user_id="admin", display_name="生日猪管")
    await plugin._administration_service.adjust_coins(
        actor, command_name="seed-coins", amount=10000, target_user_id="admin"
    )
    try:
        yield plugin, context, service, actor, clock
    finally:
        await plugin.on_unload()


async def balance(db, player):
    row = await db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (player,))
    return row[0] if row else 0


async def test_money_conservation_racing_and_replay(world):
    plugin, context, service, actor, clock = world
    sent = await service.send(actor, PacketRequest(1000, 10, "一起抓猪！"))
    assert await balance(plugin.database, actor.player_id) == 9000
    assert (await service.send(actor, PacketRequest(1000, 10, "一起抓猪！"))).receipt == sent.receipt
    with pytest.raises(ReceiptConflictError):
        await service.send(actor, PacketRequest(1001, 10, "一起抓猪！"))
    users = [replace(actor, user_id=f"p{i}", display_name=f"群友{i}", message_id=f"claim{i}") for i in range(20)]
    results = await asyncio.gather(*(service.claim(p, sent.view.scene_key) for p in users), return_exceptions=True)
    winners = [i for i, result in enumerate(results) if not isinstance(result, Exception)]
    assert len(winners) == 10
    assert (await plugin.database.fetch_one("SELECT SUM(amount) FROM red_packet_claims"))[0] == 1000
    assert (await plugin.database.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 10000
    winner = users[winners[0]]
    assert (await service.claim(winner, sent.view.scene_key)).receipt == results[winners[0]].receipt
    with pytest.raises(DomainValidationError):
        await service.claim(replace(winner, message_id="again"), sent.view.scene_key)
    assert "已全部领完" in (await service.detail(actor)).view.text()
    assert "手气最佳" in (await service.detail(actor)).view.text()


async def test_expiration_refunds_once_admin_does_not_mint_refund(world):
    plugin, context, service, actor, clock = world
    packet = await service.send(actor, PacketRequest(1000, 5))
    member = replace(actor, user_id="member", display_name="小猪", message_id="claim")
    await service.claim(member, packet.view.scene_key)
    system = await service.send(replace(actor, message_id="system"), PacketRequest(9000, 10), admin=True)
    before = await balance(plugin.database, actor.player_id)
    assert before == 9000
    clock.value += timedelta(hours=24)
    with pytest.raises(DomainValidationError):
        await service.claim(replace(member, message_id="expired"), packet.view.scene_key)
    await asyncio.gather(service.expire(), service.expire())
    assert await service.expire() == 0
    assert (await plugin.database.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 10000
    row = await plugin.database.fetch_one(
        "SELECT refunded_coins FROM red_packets WHERE short_code=?", (system.view.scene_key,)
    )
    assert row[0] == 0
    await plugin.on_unload()
    await plugin.on_load()
    await plugin._social_rewards_runner.stop()
    service = RedPacketService(plugin.database, clock=clock)
    assert await service.expire() == 0


async def test_rollback_no_negative_balance_and_cross_scope(world, monkeypatch):
    plugin, context, service, actor, clock = world
    with pytest.raises(InsufficientBalanceError):
        await service.send(actor, PacketRequest(20000, 1))
    real = service.receipts.reserve

    async def fail(*args, **kwargs):
        raise RuntimeError("fail receipt after coin debit")

    monkeypatch.setattr(service.receipts, "reserve", fail)
    with pytest.raises(RuntimeError):
        await service.send(actor, PacketRequest(1000, 10))
    assert await balance(plugin.database, actor.player_id) == 10000
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM red_packets"))[0] == 0
    monkeypatch.setattr(service.receipts, "reserve", real)
    packet = await service.send(actor, PacketRequest(1000, 10))
    from pig_catcher.domain.models import ScopeKey

    other = replace(actor, scope=ScopeKey("qq-official", "othergroup"), message_id="other")
    with pytest.raises(DomainValidationError):
        await service.claim(other, packet.view.scene_key)
    with pytest.raises(DomainValidationError):
        await service.detail(other, packet.view.scene_key)
    monkeypatch.setattr(service.receipts, "reserve", fail)
    with pytest.raises(RuntimeError):
        await service.claim(replace(actor, user_id="member", message_id="claim"), packet.view.scene_key)
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM red_packet_claims"))[0] == 0
    assert (await plugin.database.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 9000


async def test_commands_permissions_images_and_fallback(world):
    plugin, context, service, actor, clock = world
    kwargs = _command_kwargs(build_message(user_id="outsider", message_id="bad"), arguments="1000 10")
    assert not (await plugin.handle_admin_red_packet(stream_id="stream-10001", **kwargs))[0]
    kwargs = _command_kwargs(build_message(user_id="admin", message_id="packet"), arguments="1000 10 生快！")
    assert (await plugin.handle_admin_red_packet(stream_id="stream-10001", **kwargs))[0]
    assert len(context.send.images) == 1
    assert (await plugin.handle_admin_red_packet(stream_id="stream-10001", **kwargs))[0]
    assert len(context.send.images) == 1
    kwargs = _command_kwargs(build_message(user_id="member", message_id="claim"), arguments="")
    assert (await plugin.handle_claim_red_packet(stream_id="stream-10001", **kwargs))[0]
    assert len(context.send.images) == 2
    context.render.error = RuntimeError("injected picture failure")
    kwargs = _command_kwargs(build_message(user_id="member2", message_id="claim2"), arguments="")
    assert (await plugin.handle_claim_red_packet(stream_id="stream-10001", **kwargs))[0]
    assert "红包抢到啦" in context.send.texts[-1][1]
    assert (await plugin.handle_claim_red_packet(stream_id="stream-10001", **kwargs))[0]
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM red_packet_claims"))[0] == 2
