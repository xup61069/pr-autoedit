"""依序列版面產字幕(P5)測試。執行:python -m tests.test_live_subs"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Timeline, Segment
from core.remap import RemapTable
from modules.live_subs import build_from_layout


def _prep_workdir(fps=30.0):
    """做一個假的 output 資料夾:轉錄快取 + timeline(只為了 fps)"""
    d = tempfile.mkdtemp(prefix="live_subs_")
    words = [
        {"text": "第一句話", "start": 1.0, "end": 2.0},
        {"text": "被刪的話", "start": 5.0, "end": 6.0},
        {"text": "第三句話", "start": 10.0, "end": 11.0},
    ]
    with open(os.path.join(d, "02_transcript.json"), "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False)
    Timeline(fps=fps, source="x.mp4",
             segments=[Segment(0, 600, "keep")]).to_json(
        os.path.join(d, "03_timeline.json"))
    return d


def test_deleted_clip_words_dropped():
    """使用者在 Premiere 刪掉的片段,裡面的詞不出現在字幕"""
    d = _prep_workdir()
    # 版面:0~4 秒保留、4~8 秒被刪(不在清單)、8~12 秒接上(時間軸 4~8)
    layout = {"clips": [
        {"start": 0.0, "end": 4.0, "in": 0.0, "out": 4.0, "speed": 1.0},
        {"start": 4.0, "end": 8.0, "in": 8.0, "out": 12.0, "speed": 1.0},
    ]}
    lp = os.path.join(d, "05_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    srt = build_from_layout(lp, d)
    text = open(srt, encoding="utf-8").read()
    assert "第一句話" in text and "第三句話" in text
    assert "被刪的話" not in text
    # 第三句原本在 10 秒,刪了中間 4 秒後應落在 6 秒(00:00:06)
    assert "00:00:06" in text
    print("  ✓ 被刪片段的詞消失,後面的字幕正確前移")


def test_speed_clip_compresses():
    """快轉片段裡的詞,時間會被壓縮"""
    d = _prep_workdir()
    # 0~4 原速;4~12 秒的內容以 4 倍速壓進時間軸 4~6
    layout = {"clips": [
        {"start": 0.0, "end": 4.0, "in": 0.0, "out": 4.0, "speed": 1.0},
        {"start": 4.0, "end": 6.0, "in": 4.0, "out": 12.0, "speed": 4.0},
    ]}
    lp = os.path.join(d, "05_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    srt = build_from_layout(lp, d)
    text = open(srt, encoding="utf-8").read()
    # 原 10 秒的詞:4 + (10-4)/4 = 5.5 秒
    assert "00:00:05,5" in text
    print("  ✓ 快轉片段的字幕時間正確壓縮")


def test_from_spans_identity():
    """from_spans:恆等版面 = 原始時間"""
    t = RemapTable.from_spans([(0, 300, 0, 1.0)], fps=30)
    assert t.map_frame(150) == 150
    assert t.total_frames == 300
    print("  ✓ from_spans 恆等映射正確")


if __name__ == "__main__":
    print("執行序列版面字幕測試...")
    test_deleted_clip_words_dropped()
    test_speed_clip_compresses()
    test_from_spans_identity()
    print("\n全部通過 ✓  依序列產字幕邏輯正確。")
