"""桥梁微应变判定。

合格带固定为 80～220 με：落内判「合格」，否则判「越界」。
合格带外侧设警戒黄带（warn_inner～warn_outer）：读数落入黄带仍判合格、
可入队，但标记 warning=True，结论须显示「警戒」；越过黄带外沿才判越界。
黄带边界以工人领单瞬间抄下的快照为准。

判定优先级：黄带命中（合格·警戒） → 合格带命中（合格） → 越界。
"""

PASS_INNER = 80.0
PASS_OUTER = 220.0


def judge_microstrain(
    microstrain: float,
    warn_inner: float | None = None,
    warn_outer: float | None = None,
) -> tuple[str, str, bool]:
    """返回 (结论, 说明, 是否警戒)。

    warn_inner/warn_outer 为领单时抄下的黄带边界快照；为 None 表示该单据
    领取时未设黄带，按纯合格带 80～220 判定。
    """
    value = float(microstrain)

    in_band = (
        warn_inner is not None
        and warn_outer is not None
        and warn_inner <= value <= warn_outer
    )
    if in_band:
        return (
            "合格",
            f"微应变落入警戒黄带 {_num(warn_inner)}～{_num(warn_outer)} με，"
            f"合格但须警戒（设计合格带 {_num(PASS_INNER)}～{_num(PASS_OUTER)} με）",
            True,
        )

    if PASS_INNER <= value <= PASS_OUTER:
        return "合格", f"微应变处于 {_num(PASS_INNER)}～{_num(PASS_OUTER)} με 设计允许范围内", False
    if value < PASS_INNER:
        return "越界", f"微应变低于 {_num(PASS_INNER)} με 设计下限", False
    return "越界", f"微应变高于 {_num(PASS_OUTER)} με 设计上限（并越过警戒黄带外沿）", False


def _num(x: float) -> str:
    """整数去小数点：220.0 -> '220'，220.5 -> '220.5'。"""
    f = float(x)
    return str(int(f)) if f.is_integer() else str(f)
