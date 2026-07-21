"""依序列版面產字幕(P5)測試。執行:python -m tests.test_live_subs"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Timeline, Segment
from core.remap import RemapTable
from modules.live_subs import build_from_layout
from modules.workspace import wpath, prepare
import config.settings as cfg

# 鎖回預設參數,不受使用者 settings_local 覆寫影響(理由見 test_decision)
cfg.SUBTITLE_MAX_CHARS = 18
cfg.SUBTITLE_MAX_GAP_SEC = 0.5


def _prep_workdir(fps=30.0):
    """做一個假的 output 資料夾:轉錄快取 + timeline(只為了 fps)"""
    d = tempfile.mkdtemp(prefix="live_subs_")
    prepare(d)              # 中繼檔放 _work/,跟正式流程一致
    words = [
        {"text": "第一句話", "start": 1.0, "end": 2.0},
        {"text": "被刪的話", "start": 5.0, "end": 6.0},
        {"text": "第三句話", "start": 10.0, "end": 11.0},
    ]
    with open(wpath(d, "02_transcript.json"), "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False)
    Timeline(fps=fps, source="x.mp4",
             segments=[Segment(0, 600, "keep")]).to_json(
        wpath(d, "03_timeline.json"))
    return d


def test_deleted_clip_words_dropped():
    """使用者在 Premiere 刪掉的片段,裡面的詞不出現在字幕"""
    d = _prep_workdir()
    # 版面:0~4 秒保留、4~8 秒被刪(不在清單)、8~12 秒接上(時間軸 4~8)
    layout = {"clips": [
        {"start": 0.0, "end": 4.0, "in": 0.0, "out": 4.0, "speed": 1.0},
        {"start": 4.0, "end": 8.0, "in": 8.0, "out": 12.0, "speed": 1.0},
    ]}
    lp = wpath(d, "05_layout.json")
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
    lp = wpath(d, "05_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    srt = build_from_layout(lp, d)
    text = open(srt, encoding="utf-8").read()
    # 原 10 秒的詞:4 + (10-4)/4 = 5.5 秒
    assert "00:00:05,5" in text
    print("  ✓ 快轉片段的字幕時間正確壓縮")


def test_premiere_lies_about_speed_clip_source_range():
    """Premiere 對「變速片段」回報的來源入出點是錯的,不能拿來對位。

    實測一條 923 個片段的真實序列:790 個 speed=1 的片段來源範圍完全正確,
    但 133 個變速片段**全部**是錯的 —— 例如某段真正的來源是 10.267~10.767 秒,
    Premiere 卻回報 0.850~0.900(跑到影片最前面去了)。
    特徵很一致:它把「時間軸長度」當成來源長度回報,完全沒有乘上倍率。

    不擋掉的下場很嚴重:假的區間會跟真的區間重疊,同一個詞同時對到兩個
    相距很遠的位置,字幕的結束時間被拉到幾十秒之後。實測那份字幕有 23 行
    長度超過 15 秒(最長一行 3 分鐘),等於整份時間軸壞掉。

    判斷方式刻意不是「差多少算不一致」的容差 —— 很短的片段判不出來
    (1 幀的片段,容差 0.204 秒比整段預期長度 0.2 秒還大)。
    改成問「來源長度比較像有換算、還是比較像沒換算」,哪個近算哪個。"""
    d = _prep_workdir()
    fps = 30.0
    # 第一段正常;第二段是 Premiere 回報錯誤的變速片段
    # (來源長度 0.5 秒 == 時間軸長度,沒有乘上 12 倍 -> 一看就知道沒換算)
    layout = {"clips": [
        {"start": 0.0, "end": 4.0, "in": 0.0, "out": 4.0, "speed": 1.0},
        {"start": 4.0, "end": 4.5, "in": 0.4, "out": 0.9, "speed": 12.0},
        {"start": 4.5, "end": 8.5, "in": 8.0, "out": 12.0, "speed": 1.0},
    ]}
    lp = wpath(d, "05_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    srt = build_from_layout(lp, d)
    text = open(srt, encoding="utf-8").read()

    # 那個假區間(來源 0.4~0.9 秒)若被採用,「第一句話」(1~2 秒)會同時
    # 對到時間軸 0~1 秒與 4~4.5 秒,結束時間就被拉到 4.5 秒去
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    assert blocks, "完全沒有產出字幕"
    for b in blocks:
        line = b.split("\n")[1]
        a, z = line.split(" --> ")

        def sec(t):
            h, m, rest = t.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        assert sec(z) - sec(a) < 5.0, \
            f"字幕長度被假區間拉爆了:{line}(「{b.split(chr(10))[2]}」)"
    assert "第一句話" in text and "第三句話" in text, "正常的字幕不該消失"
    print("  ✓ Premiere 回報錯誤的變速片段被擋下,字幕時間沒有被拉爆")


def test_correctly_reported_speed_clip_is_still_used():
    """反面:若來源長度真的等於「時間軸長度 × 倍率」,就要照常採用。

    只驗「擋掉壞的」不驗這個,等於用「乾脆全部不信變速片段」換到修好,
    那哪天 Premiere 修正了、或使用者自己做的變速,就永遠用不到。"""
    d = _prep_workdir()
    # 4~12 秒的來源(8 秒)以 4 倍速壓進時間軸 4~6(2 秒)-> 8 == 2 x 4 ✓
    layout = {"clips": [
        {"start": 0.0, "end": 4.0, "in": 0.0, "out": 4.0, "speed": 1.0},
        {"start": 4.0, "end": 6.0, "in": 4.0, "out": 12.0, "speed": 4.0},
    ]}
    lp = wpath(d, "05_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    text = open(build_from_layout(lp, d), encoding="utf-8").read()
    # 原本 10 秒的詞:4 + (10-4)/4 = 5.5 秒 —— 跟 test_speed_clip_compresses 一致
    assert "00:00:05,5" in text, \
        f"自洽的變速片段應該照常採用(第三句話沒對到 5.5 秒):\n{text}"
    print("  ✓ 來源時間點自洽的變速片段照常採用")


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
    test_premiere_lies_about_speed_clip_source_range()
    test_correctly_reported_speed_clip_is_still_used()
    test_from_spans_identity()
    print("\n全部通過 ✓  依序列產字幕邏輯正確。")
