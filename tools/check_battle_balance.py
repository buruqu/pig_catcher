"""离线模拟已定稿战斗盘；报告观察值，不自动修改用户指定数值。"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.domain.battle import new_state, play_chunk, resolve_round, roll_count  # noqa: E402
from pig_catcher.domain.battle_catalog import BATTLE_VERSION  # noqa: E402


def simulate(samples: int, a_level: int, b_level: int) -> dict:
    wins = Counter()
    rounds, moves, core_max = [], [], 0
    for index in range(samples):
        seed = f"offline-balance:{index}:{a_level}:{b_level}"
        state = new_state([{"fighter_id": "sukuna", "level": a_level}, {"fighter_id": "gojo", "level": b_level}])
        count = 0
        while state["status"] == "active":
            for side in (0, 1):
                roll_count(state, side, seed)
                while not state["sides"][side]["turn"]["done"]:
                    count += len(play_chunk(state, side, seed))
            resolve_round(state, seed)
        wins[state["winner"]] += 1
        rounds.append(state["round"])
        moves.append(count)
        core_max = max(core_max, *(side["core"] for side in state["sides"]))
    return {
        "samples": samples,
        "sukuna_level": a_level,
        "gojo_level": b_level,
        "sukuna_win_percent": round(wins[0] / samples * 100, 2),
        "gojo_win_percent": round(wins[1] / samples * 100, 2),
        "average_rounds": round(statistics.mean(rounds), 3),
        "max_rounds": max(rounds),
        "average_moves": round(statistics.mean(moves), 3),
        "max_moves": max(moves),
        "max_observed_core": core_max,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5000)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100000:
        parser.error("模拟样本数应在1至100000之间；这不是玩法上限。")
    print(
        json.dumps(
            {
                "version": BATTLE_VERSION,
                "scope": "offline simulation; no rule changes",
                "runs": [simulate(args.samples, a, b) for a, b in ((0, 0), (5, 5), (5, 0), (0, 5))],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
