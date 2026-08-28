"""静态展示标签与物理单位格式；不参与概率、排序或价值计算。"""

from __future__ import annotations

import json
import math
import unicodedata


def normalize_display_tags(value: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """限制展示密度并去重，独立于参与料理和派遣判定的 recipe_tags。"""

    if not isinstance(value, (list, tuple)) or len(value) > 5:
        raise ValueError("展示标签必须是至多 5 个标签的列表")
    normalized: list[str] = []
    for raw_tag in value:
        if not isinstance(raw_tag, str):
            raise ValueError("展示标签必须是文字")
        tag = raw_tag.strip()
        if not tag or len(tag) > 20:
            raise ValueError("单个展示标签须为 1—20 个字符")
        if any(unicodedata.category(char).startswith("C") for char in tag):
            raise ValueError("展示标签不能包含换行或控制字符")
        if tag not in normalized:
            normalized.append(tag)
    return tuple(normalized)


def display_tags_from_json(value: object) -> tuple[str, ...]:
    """旧模板缺省为空；损坏的模板元数据必须显式报错，不混入业务标签。"""

    payload = json.loads(str(value or "[]"))
    return normalize_display_tags(payload)


def _finite_number(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("展示的物理数值必须是有限数")
    return number


def format_weight(value: float, *, include_base: bool = False) -> str:
    """内部一直使用 kg；吨保留至 0.00001 t，精度等价于 0.01 kg。"""

    number = _finite_number(value)
    if abs(number) < 1000:
        return f"{number:.2f} kg"
    tonnes = f"{number / 1000:.5f}".rstrip("0").rstrip(".")
    result = f"{tonnes} t"
    return f"{result}（{number:.2f} kg）" if include_base else result


def format_length(value: float, *, include_base: bool = False) -> str:
    """十米及以上改用 m；保留至 0.001 m，精度等价于 0.1 cm。"""

    number = _finite_number(value)
    if abs(number) < 1000:
        return f"{number:.1f} cm"
    metres = f"{number / 100:.3f}".rstrip("0").rstrip(".")
    result = f"{metres} m"
    return f"{result}（{number:.1f} cm）" if include_base else result


def format_measurement(value: float, unit: str) -> str:
    """纪录页沿用基础单位标识，由展示层选择友好单位。"""

    if unit == "kg":
        return format_weight(value)
    if unit == "cm":
        return format_length(value)
    raise ValueError(f"不支持的物理单位：{unit}")
