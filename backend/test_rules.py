"""判定规则单测：python test_rules.py

黄带 200～220 με 时：
  210 -> 合格且警戒（不是越界）；230 -> 越界。
"""

from rules import judge_microstrain


def main() -> None:
    # 题述验收用例。
    v = judge_microstrain(210)
    assert v[0] == "合格" and v[1] is True, v
    assert judge_microstrain(230)[:2] == ("越界", False)

    # 边界含端点：内沿、外沿本身都在黄带内；越过外沿才算越界。
    assert judge_microstrain(200)[:2] == ("合格", True)
    assert judge_microstrain(220)[:2] == ("合格", True)

    # 黄带以下仍是普通合格。
    assert judge_microstrain(199)[:2] == ("合格", False)
    assert judge_microstrain(150)[:2] == ("合格", False)
    assert judge_microstrain(80)[:2] == ("合格", False)

    # 低端越界。
    assert judge_microstrain(79)[:2] == ("越界", False)
    assert judge_microstrain(40)[:2] == ("越界", False)

    # 停用黄带：210 只是普通合格，221 才越界。
    assert judge_microstrain(210, band_enabled=False)[:2] == ("合格", False)
    assert judge_microstrain(221, band_enabled=False)[:2] == ("越界", False)

    # 改档后的另一套边界 205～215。
    assert judge_microstrain(210, True, 205, 215)[:2] == ("合格", True)
    assert judge_microstrain(216, True, 205, 215)[:2] == ("越界", False)
    assert judge_microstrain(204, True, 205, 215)[:2] == ("合格", False)

    print("test_rules OK")


if __name__ == "__main__":
    main()
