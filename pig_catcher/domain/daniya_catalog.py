"""Battle v19 达妮娅完整三形态盘；旧盘由 battle_catalog 独立冻结。"""

BLACK_HOLE = "black-hole"


def build_forms(Move, FighterForm):
    descriptions = {
        ("staging", "curtain"): "自身胜率+20。",
        ("staging", "knock"): "自身胜率+30。",
        ("staging", "flawless"): "自身胜率+35，再抽2招，本轮领域战胜权+0.4。",
        ("staging", "lie"): "再抽1招；下一次数值招的自身胜率和敌方减权翻倍；本轮敌领域战胜权-0.4；下轮出招数-1。",
        ("staging", "timer"): "自身胜率+52.1，永久领域战胜权+1。",
        (
            "staging",
            "domain",
        ): "自身胜率+40；领域命中或战胜进入幻灭，下轮出招数+2，通用领域加倍后本招额外再翻倍，"
        "并随机将本轮1招胜率翻倍。累计领域命中7次进入黑洞。",
        ("staging", "world-work"): "下一次蚀域领域战胜权+1。",
        ("staging", "collapse"): "直接进入黑洞。",
        ("disillusion", "curtain"): "敌方胜率-20。",
        ("disillusion", "knock"): "敌方胜率-30。",
        ("disillusion", "flawless"): "敌方胜率-35，敌下轮出招数-3，额外粒子+1，敌力竭权重永久+0.5。",
        (
            "disillusion",
            "lie",
        ): "本轮所有敌方减益翻倍，随机无视敌方2招，额外粒子+1；落败经粒子抵伤后伤势再降低1级；自身下轮出招数-1。",
        ("disillusion", "timer"): "敌方胜率-52.1，敌力竭权重永久+5，额外粒子+2。",
        (
            "disillusion",
            "domain",
        ): "敌方胜率-40；领域命中后自身下轮出招数+1，敌下轮出招数-2，额外粒子+0.5，"
        "敌力竭权重永久+1，随机无视敌当轮1招。累计领域命中7次进入黑洞。",
        ("disillusion", "world-work"): "敌力竭权重永久+0.5，额外粒子+1.5，再抽1招。",
        ("disillusion", "collapse"): "直接进入黑洞。",
        (
            BLACK_HOLE,
            "red-supergiant",
        ): "自身胜率+15，敌方胜率-20，取消敌当前3招；自身与敌减权按每粒子8%加成，25粒子为3倍。",
        (
            BLACK_HOLE,
            "wolf-rayet",
        ): "自身胜率+25，敌方胜率-30，取消敌当前4招；自身与敌减权按每粒子8%加成，25粒子为3倍。",
        (
            BLACK_HOLE,
            "iron-supernova",
        ): "自身胜率+35，敌方胜率-40，取消敌当前5招；自身与敌减权按每粒子8%加成，25粒子为3倍。",
        (BLACK_HOLE, "eternal-end"): "敌方本轮必败且力竭；固定伤势盘和免抽机制仍优先。",
    }
    for key in descriptions:
        form = key[0]
        if form == "staging":
            descriptions[key] += (
                "原生施放获得0.5粒子、下次领域出现与战胜权各+0.3、拟态坍塌权重+0.08；25粒子立即进入黑洞。"
            )
        elif form == "disillusion":
            descriptions[key] += "原生施放获得0.5粒子、敌永久力竭权重+0.3、虚质崩坏权重+0.05；25粒子立即进入黑洞。"
        else:
            descriptions[key] += (
                "原生施放获得0.5粒子；黑洞固定伤势盘82.3%无伤/12.49%递进受伤/5.21%力竭，敌力竭权重额外为回合数×5。"
            )
        descriptions[key] += "每获得新粒子永久敌减权：布景/幻灭每点0.521，黑洞每点0.823。"

    def move(form, suffix, name, *, gain=0, reduction=0, weight=10000, draws=0, loan=False, domain=False):
        tags = ("daniya-v19", f"daniya-v19-{form}", f"daniya-v19-{suffix}")
        if domain:
            tags += ("domain",)
        return Move(
            f"daniya-{form}-{suffix}",
            f"达妮娅-{'布景' if form == 'staging' else '幻灭' if form == 'disillusion' else '黑洞'}·{name}"
            if suffix != "world-work"
            else "达妮娅-世界·上班",
            draws=draws,
            loan=loan,
            tags=tags,
            gain_tenths=gain,
            opponent_reduction_tenths=reduction,
            draw_weight_units=weight,
            description=descriptions[(form, suffix)],
        )

    staging = (
        move("staging", "curtain", "帷幕终景", gain=200, weight=9000),
        move("staging", "knock", "久疏问候！", gain=300, weight=9000),
        move("staging", "flawless", "天衣无缝", gain=350, draws=2, weight=8000),
        move("staging", "lie", "未竟的谎言", draws=1, loan=True, weight=9000),
        move("staging", "timer", "计时的溃灭", gain=521, weight=9000),
        move("staging", "domain", "蚀域", gain=400, domain=True),
        move("staging", "world-work", "丸山大姐达妮娅·世界·上班", weight=5000),
        move("staging", "collapse", "拟态坍塌", weight=1000),
    )
    disillusion = (
        move("disillusion", "curtain", "帷幕终景", reduction=200),
        move("disillusion", "knock", "轻叩门扉", reduction=300),
        move("disillusion", "flawless", "天衣无缝", reduction=350, weight=6000),
        move("disillusion", "lie", "未竟的谎言", loan=True, weight=8000),
        move("disillusion", "timer", "计时的溃灭", reduction=521, weight=4000),
        move("disillusion", "domain", "蚀域", reduction=400, weight=12000, domain=True),
        move("disillusion", "world-work", "丸山大姐达妮娅·世界·上班", draws=1, weight=5000),
        move("disillusion", "collapse", "虚质崩坏", weight=5000),
    )
    black_hole = (
        move(BLACK_HOLE, "red-supergiant", "红特超巨星", gain=150, reduction=200, weight=40000),
        move(BLACK_HOLE, "wolf-rayet", "沃尔夫拉叶星", gain=250, reduction=300, weight=30000),
        move(BLACK_HOLE, "iron-supernova", "铁核坍塌超新星", gain=350, reduction=400, weight=20000),
        move(BLACK_HOLE, "eternal-end", "深黯 终末 恒常", weight=10000),
    )
    return (
        FighterForm("staging", "布景", staging),
        FighterForm("disillusion", "幻灭", disillusion),
        FighterForm(BLACK_HOLE, "黑洞", black_hole),
    )
