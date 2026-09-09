"""Battle v18：用户提供的流萤/萨姆九格战斗盘。"""


def build_moves(move):
    return (
        move(
            "firefly-crimson-cocoon",
            "流萤·我曾安眠，赤染之茧",
            12,
            tags=("firefly", "firefly-skill", "firefly-crimson-cocoon"),
            description="胜率+12，燃芯+1；下一次萨姆技能+10。若本回合未进入萨姆，下回合萨姆招式出现权重+0.25。残梦：自己+8、对手-8、溃败+1。",
        ),
        move(
            "firefly-dream-destination",
            "流萤·梦应归于何处",
            tags=("firefly", "firefly-skill", "firefly-dream-destination"),
            opponent_reduction=16,
            description="对手胜率-16，自身本回合力竭权重-0.2，燃芯+1；对手本回合已有增益时再-8。残梦：对手-10、溃败+1、自身力竭-0.15；对手原有至少2层溃败时，其本回合力竭再+0.15。",
        ),
        move(
            "firefly-firefly-flame",
            "流萤·我会看见，飞萤之火",
            draw_weight_units=8500,
            tags=("firefly", "firefly-skill", "firefly-choice"),
            description="燃芯+1；再抽两个候选，自动择优使用一招。选流萤技再获燃芯+1、领域战权重+0.2；选萨姆技立即变身，该招+10、溃败+1。残梦只再抽一个：萨姆技+12，流萤技触发半效残梦，不获燃芯。",
        ),
        move(
            "firefly-silent-galaxy",
            "流萤·沉睡在静默的星河",
            draw_weight_units=8500,
            tags=("firefly", "firefly-skill", "firefly-silent-galaxy"),
            description="对手本回合有效正向胜率增益减少50%，自身本回合力竭权重-0.25，燃芯+1；对手胜率高于我方时再-8。若本回合未进入萨姆，下回合萨姆抽取权重+0.1。残梦：自身力竭-0.15、对手-8、溃败+1；对手原有至少2层溃败时，其本回合力竭再+0.1。",
        ),
        move(
            "sam-skyfire-bombardment",
            "萨姆·指令-天火轰击",
            22,
            opponent_reduction=10,
            tags=("firefly", "sam-skill", "sam-skyfire-bombardment"),
            description="燃芯+1，胜率+22、对手-10，命中后溃败+1；由流萤直接变身发动时额外溃败+1，本招出现权重-0.1。",
        ),
        move(
            "sam-bottom-fire-slash",
            "萨姆·火萤Ⅳ型-底火斩击",
            22,
            tags=("firefly", "sam-skill", "sam-bottom-fire-slash"),
            description="胜率+22，对手每层溃败另+5（与被动叠加）；本回合第一招萨姆技再使对手-8。命中溃败+1；由流萤直接变身再+1，本招出现权重-0.1。",
        ),
        move(
            "sam-deathstar-overload",
            "萨姆·火萤Ⅳ型-死星过载",
            26,
            opponent_reduction=14,
            tags=("firefly", "sam-skill", "sam-deathstar-overload"),
            draw_weight_units=9000,
            description="胜率+26、对手-14，命中后溃败+1；对手原有至少2层溃败时，下回合-1招；原有3层时本回合力竭再+0.15。由流萤直接变身额外溃败+1，本招出现权重-0.1。",
        ),
        move(
            "sam-ignite-star-sea",
            "萨姆·火萤Ⅳ型-点燃星海",
            28,
            tags=("firefly", "sam-skill", "sam-ignite-star-sea"),
            draw_weight_units=9000,
            description="流萤态：进入萨姆2回合、溃败+2；因此达到3层时，对手本回合力竭+0.15；自己下回合+1招。每层燃芯+5、抽取权重+0.1，结算后清空；直接变身本招权重-0.1。萨姆态改为+20、对手-10、形态延长1回合、溃败+1。",
        ),
        move(
            "firefly-falling-sky",
            "流萤/萨姆·自破碎的天空坠落",
            36,
            opponent_reduction=20,
            tags=("domain", "firefly", "firefly-domain"),
            draw_weight_units=8000,
            description="胜率+36、对手-20、溃败+1。领域获胜或命中后追加焦土陨击+12、对手本回合力竭+0.15；对手已有3层溃败时，再-15且力竭再+0.2。结算后延长已有萨姆形态1回合，下回合对手溃败+1、自己出招数+1。",
        ),
    )
