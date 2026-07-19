"""決策引擎測試。執行:python -m tests.test_decision"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Word
from core.decision import build_segments
import config.settings as cfg

# 測試以「預設參數」為前提;使用者面板存的 settings_local 覆寫
# 不應影響測試結果,這裡把測試相依的參數鎖回預設值。
cfg.SILENCE_ACTION = "speed"
cfg.SILENCE_THRESHOLD_SEC = 1.2
cfg.SILENCE_PADDING_SEC = 0.15
cfg.SILENCE_SPEED_FACTOR = 6.0
cfg.FILLERS_ALWAYS = ["嗯", "呃", "啊", "欸", "唉", "痾", "喔"]
cfg.FILLERS_CONDITIONAL = ["就是", "然後", "那個", "這個", "所以說", "對對對"]
cfg.CONDITIONAL_CONFIDENCE = 0.6


def test_always_filler_deleted():
    """嗯、呃 前後有停頓(真正的語氣詞)-> 刪除"""
    words = [
        Word("大家好", 0.0, 1.0),
        Word("嗯", 1.15, 1.35),          # 前後各停 0.15 秒,是真的語氣詞
        Word("今天", 1.5, 2.0),
    ]
    segs = build_segments(words, fps=30, total_frames=60)
    deletes = [s for s in segs if s.action == "delete"]
    assert any(s.text == "嗯" for s in deletes)
    assert all(s.confidence == 1.0 for s in deletes if s.text == "嗯")
    print("  ✓ 無條件冗詞『嗯』被刪除,信心=1.0")


def test_embedded_char_kept():
    """黏在語流中/句尾的字不當語氣詞(FunASR 逐字輸出的「好啊」保護)"""
    # 情境 1:句中緊貼(好「啊」那我們)
    words = [
        Word("好", 0.0, 0.3),
        Word("啊", 0.3, 0.5),            # 跟前後幾乎零間隔 = 句子的一部分
        Word("那", 0.55, 0.7),
        Word("我們", 0.7, 1.0),
    ]
    segs = build_segments(words, fps=30, total_frames=60)
    deletes = [s for s in segs if s.action == "delete"]
    assert not any(s.text == "啊" for s in deletes), "句中的『啊』不該被刪"
    # 情境 2:黏在句尾、後面才停頓(好「啊」……那我們)
    words = [
        Word("好", 0.0, 0.3),
        Word("啊", 0.3, 0.5),            # 前面貼著「好」,是「好啊」的一部分
        Word("那", 1.2, 1.4),            # 句尾之後才停頓
    ]
    segs = build_segments(words, fps=30, total_frames=90)
    deletes = [s for s in segs if s.action == "delete"]
    assert not any(s.text == "啊" for s in deletes), "句尾的『啊』不該被刪"
    print("  ✓ 句中/句尾黏著的『啊』被保留(逐字引擎保護)")


def test_conditional_filler_kept_in_context():
    """『然後』在句中連貫使用時應保留(不是孤立)"""
    words = [
        Word("我們", 0.0, 0.5),
        Word("先", 0.5, 0.8),
        Word("然後", 0.8, 1.1),      # 緊接前詞,gap小,語意連貫
        Word("開始", 1.1, 1.6),
    ]
    segs = build_segments(words, fps=30, total_frames=60)
    deletes = [s for s in segs if s.action == "delete"]
    assert not any(s.text == "然後" for s in deletes)
    print("  ✓ 連貫語境的『然後』被保留")


def test_conditional_filler_deleted_when_repeated():
    """『對對對』連續重複時應刪除"""
    words = [
        Word("好的", 0.0, 0.5),
        Word("對對對", 0.6, 0.9),
        Word("對對對", 0.9, 1.2),
    ]
    segs = build_segments(words, fps=30, total_frames=60)
    deletes = [s for s in segs if s.action == "delete" and s.text == "對對對"]
    assert len(deletes) >= 1
    assert all(s.confidence == cfg.CONDITIONAL_CONFIDENCE for s in deletes)
    print("  ✓ 重複的『對對對』被刪除,信心=低(需審閱)")


def test_silence_becomes_speed():
    """長靜音應變成快轉段"""
    words = [
        Word("第一句", 0.0, 1.0),
        Word("第二句", 4.0, 5.0),     # 中間 3 秒空白 > 1.2秒門檻
    ]
    segs = build_segments(words, fps=30, total_frames=180)
    speeds = [s for s in segs if s.action == "speed"]
    assert len(speeds) >= 1
    assert speeds[0].factor == cfg.SILENCE_SPEED_FACTOR
    print("  ✓ 長靜音正確轉為快轉段")


def test_coverage_complete():
    """輸出段落必須首尾相連,完整覆蓋整支影片"""
    words = [
        Word("大家好", 0.0, 1.0),
        Word("嗯", 1.5, 1.7),
        Word("今天", 2.0, 3.0),
    ]
    total = 120
    segs = build_segments(words, fps=30, total_frames=total)
    assert segs[0].start == 0
    assert segs[-1].end == total
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start, f"段落有斷裂:{a.end} != {b.start}"
    print("  ✓ 段落完整覆蓋,無斷裂無重疊")


if __name__ == "__main__":
    print("執行決策引擎測試...")
    test_always_filler_deleted()
    test_embedded_char_kept()
    test_conditional_filler_kept_in_context()
    test_conditional_filler_deleted_when_repeated()
    test_silence_becomes_speed()
    test_coverage_complete()
    print("\n全部通過 ✓  決策引擎邏輯正確。")
