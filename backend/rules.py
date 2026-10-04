"""桥梁微应变判定。

合格带固定为 80～220 με。测量员可在合格带外侧（贴近上限一侧）设一条
“警戒黄带”，由黄带内沿/外沿两个边界界定：

* 读数落在黄带 [内沿, 外沿] 内 —— 仍判 **合格**，但标记 ``warning=True``（警戒）；
* 越过黄带外沿（> 外沿，即高于合格上限）或低于合格下限 —— 判 **越界**；
* 其余合格区 —— **合格**，不警戒。

判定函数只接收边界数值，不读取任何全局配置：调用方（worker）在领单瞬间
把当时的黄带边界抄录进读数行，再把这套快照边界传进来。因此黄带改档只影响
之后新认领的单据，已领单据永远按领单瞬间的边界判定，与详情脚注、表格着色
共用的是同一套边界。
"""

# 合格带固定边界（微应变）。
PASS_LOW = 80.0
PASS_HIGH = 220.0

# 默认黄带：合格带外侧 200～220 με。
DEFAULT_BAND_ENABLED = True
DEFAULT_BAND_INNER = 200.0
DEFAULT_BAND_OUTER = 220.0


def judge_microstrain(
    microstrain: float,
    band_enabled: bool = DEFAULT_BAND_ENABLED,
    band_inner: float = DEFAULT_BAND_INNER,
    band_outer: float = DEFAULT_BAND_OUTER,
) -> tuple[str, bool, str]:
    """返回 ``(结论, 是否警戒, 说明)``。

    结论取值为 ``"合格"`` 或 ``"越界"``；``warning=True`` 表示读数落在黄带内，
    结论仍是合格但需在总览与详情同色同字地标“警戒”。
    """
    value = float(microstrain)

    if value < PASS_LOW:
        return (
            "越界",
            False,
            f"微应变低于 {_fmt(PASS_LOW)} με 设计下限",
        )

    # 黄带启用时，其外沿即越界分界线；未启用则以合格带上限为界。
    outer_edge = float(band_outer) if band_enabled else PASS_HIGH
    if value > outer_edge:
        return (
            "越界",
            False,
            f"微应变越过黄带外沿 {_fmt(outer_edge)} με（高于设计上限）",
        )

    if band_enabled and float(band_inner) <= value <= float(band_outer):
        return (
            "合格",
            True,
            f"微应变 {_fmt(value)} με 落在警戒黄带 "
            f"{_fmt(band_inner)}～{_fmt(band_outer)} με 内，合格但须警戒",
        )

    return (
        "合格",
        False,
        f"微应变处于 {_fmt(PASS_LOW)}～{_fmt(PASS_HIGH)} με 设计允许范围内",
    )


def _fmt(x: float) -> str:
    """边界/读数格式化：整数不带小数点，其余原样保留合理精度。"""
    f = float(x)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"
