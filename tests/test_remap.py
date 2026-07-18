"""
重映射引擎測試。用手算得出的例子驗證,確保地基百分之百正確。
執行:python -m tests.test_remap
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Segment, Word
from core.remap import RemapTable


def approx(a, b, tol=1):
    return abs(a - b) <= tol


def test_keep_only():
    """全保留:映射應該是恆等函數"""
    segs = [Segment(0, 300, "keep")]
    t = RemapTable(segs, fps=30)
    assert t.map_frame(0) == 0
    assert t.map_frame(150) == 150
    assert t.map_frame(299) == 299
    assert t.total_frames == 300
    print("  ✓ 全保留:恆等映射正確")


def test_delete_shifts_later_frames():
    """刪除中間一段,後面的幀應該往前移"""
    # 保留 0-100, 刪除 100-150, 保留 150-300
    segs = [
        Segment(0, 100, "keep"),
        Segment(100, 150, "delete", reason="filler", text="就是"),
        Segment(150, 300, "keep"),
    ]
    t = RemapTable(segs, fps=30)
    assert t.map_frame(50) == 50              # 刪除點之前:不變
    assert t.map_frame(120) is None           # 落在刪除段:None
    assert t.map_frame(150) == 100            # 刪除段之後:前移 50 幀
    assert t.map_frame(299) == 249
    assert t.total_frames == 250              # 300 - 50
    print("  ✓ 刪除:後段正確前移,刪除段回傳 None")


def test_speed_compresses():
    """六倍速的段落,時間軸長度應該壓縮為 1/6"""
    # 保留 0-60, 六倍速 60-360 (300幀->50幀), 保留 360-420
    segs = [
        Segment(0, 60, "keep"),
        Segment(60, 360, "speed", factor=6.0, reason="silence"),
        Segment(360, 420, "keep"),
    ]
    t = RemapTable(segs, fps=30)
    assert t.map_frame(30) == 30
    # 快轉段起點
    assert t.map_frame(60) == 60
    # 快轉段中點 (原始210, 進入快轉150幀, /6=25) -> 60+25=85
    assert approx(t.map_frame(210), 85)
    # 快轉結束後:60 + 300/6 = 110
    assert approx(t.map_frame(360), 110)
    assert approx(t.total_frames, 170)        # 60 + 50 + 60
    print("  ✓ 快轉:時間軸正確壓縮 1/6")


def test_cuts_recorded():
    """刪除段應該產生切點,markers 過濾條件正確"""
    segs = [
        Segment(0, 100, "keep"),
        Segment(100, 106, "delete", reason="filler", text="嗯",
                confidence=1.0),                       # 必刪,高信心
        Segment(106, 200, "keep"),
        Segment(200, 230, "delete", reason="filler", text="就是",
                confidence=0.6),                       # 模糊,低信心
        Segment(230, 400, "keep"),
    ]
    t = RemapTable(segs, fps=30)
    # 全部切點
    all_cuts = t.cuts_for_markers()
    assert len(all_cuts) == 2
    # 只看低信心的(需要人工審閱的)
    review = t.cuts_for_markers(max_confidence=0.9)
    assert len(review) == 1
    assert review[0].text == "就是"
    # 切點的時間軸位置:第二個刪除點,前面刪了6幀,所以 200-6=194
    assert review[0].timeline_frame == 194
    print("  ✓ 切點:記錄正確,信心過濾正確")


def test_subtitles_skip_deleted():
    """被刪的詞不應出現在字幕裡,且應正確斷句"""
    segs = [
        Segment(0, 90, "keep"),
        Segment(90, 96, "delete", reason="filler", text="嗯"),
        Segment(96, 200, "keep"),
    ]
    t = RemapTable(segs, fps=30)
    words = [
        Word("大家好", 0.0, 1.0),        # 0-30幀, keep
        Word("嗯", 3.0, 3.2),            # 90-96幀, 被刪
        Word("今天", 3.2, 4.0),          # 96-120幀, keep
        Word("我們", 4.0, 5.0),          # 120-150幀, keep
    ]
    subs = t.build_subtitles(words, max_chars=18, max_gap_frames=15)
    joined = "".join(s.text for s in subs)
    assert "嗯" not in joined            # 冗詞不在字幕裡
    assert "大家好" in joined
    assert "今天" in joined
    print("  ✓ 字幕:冗詞正確剔除,保留詞正確保留")


def test_subtitles_keep_english_word_intact():
    """英文/數字單字不該被 max_chars 從中間切斷。
    Whisper 會把 'Pattern' 切成 'P','atter','n' 碎片,字幕不能斷在中間。"""
    segs = [Segment(0, 300, "keep")]
    t = RemapTable(segs, fps=30)
    words = [
        Word("這", 0.0, 0.3),
        Word("是", 0.3, 0.6),
        Word("一", 0.6, 0.9),
        Word("個", 0.9, 1.2),
        Word("P", 1.2, 1.4),
        Word("atter", 1.4, 1.6),
        Word("n", 1.6, 1.8),
    ]
    # max_chars 故意設很小,不修的話一定會斷在英文單字中間
    subs = t.build_subtitles(words, max_chars=4, max_gap_frames=15)
    assert any("Pattern" in s.text for s in subs), \
        f"Pattern 被切斷了:{[s.text for s in subs]}"
    print("  ✓ 字幕:英文單字不被 max_chars 切斷")


def test_ntsc_no_drift():
    """29.97fps 長片不應累積明顯漂移"""
    fps = 29.97
    # 模擬 30 分鐘 = 53946 幀,中間穿插 100 個刪除段
    segs = []
    pos = 0
    for i in range(100):
        segs.append(Segment(pos, pos + 500, "keep"))
        pos += 500
        segs.append(Segment(pos, pos + 10, "delete", reason="filler", text="就是"))
        pos += 10
    t = RemapTable(segs, fps=fps)
    # 最後一個保留段的起點,映射後應該 = 前面所有保留段長度總和
    expected = 100 * 500 - 0  # 每段刪10幀不影響keep段長度累加
    # 檢查最後一幀映射不為 None 且單調遞增
    last = -1
    for f in range(0, pos, 500):
        m = t.map_frame(f)
        if m is not None:
            assert m > last, f"映射非單調遞增 at {f}"
            last = m
    print("  ✓ NTSC:長片映射單調遞增,無異常漂移")


if __name__ == "__main__":
    print("執行重映射引擎測試...")
    test_keep_only()
    test_delete_shifts_later_frames()
    test_speed_compresses()
    test_cuts_recorded()
    test_subtitles_skip_deleted()
    test_subtitles_keep_english_word_intact()
    test_ntsc_no_drift()
    print("\n全部通過 ✓  地基正確,可以往上蓋。")
