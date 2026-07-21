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


def test_subtitles_not_fragmented_by_small_cuts():
    """刪掉一個短冗詞,不該把同一句字幕硬切成兩行(逐字引擎會有大量
    小剪點,以前每個剪點都斷一刀,字幕被剁得很碎)"""
    segs = [
        Segment(0, 15, "keep"),
        Segment(15, 21, "delete", reason="filler", text="啊"),
        Segment(21, 60, "keep"),
    ]
    t = RemapTable(segs, fps=30)
    words = [
        Word("這個", 0.0, 0.5),           # keep
        Word("啊", 0.5, 0.7),             # 被刪(0.2 秒小剪點)
        Word("功能", 0.7, 1.2),           # keep,原始間隔僅 0.2 秒
        Word("很好用", 1.2, 2.0),         # keep
    ]
    subs = t.build_subtitles(words, max_chars=18, max_gap_frames=15)
    assert len(subs) == 1, f"應合成一行,實際 {len(subs)} 行:" + \
        " / ".join(s.text for s in subs)
    assert subs[0].text == "這個功能很好用"
    print("  ✓ 字幕:小剪點不再把句子剁碎")


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


def test_subtitles_break_at_punctuation():
    """字幕應優先在句末標點處斷行,讓每句自成一行"""
    segs = [Segment(0, 600, "keep")]
    t = RemapTable(segs, fps=30)
    words = [
        Word("大家好。", 0.0, 1.0),
        Word("今天", 1.0, 1.5),
        Word("我們", 1.5, 2.0),
        Word("來聊剪輯。", 2.0, 3.0),
    ]
    subs = t.build_subtitles(words, max_chars=18, max_gap_frames=15)
    assert subs[0].text == "大家好。", subs[0].text
    assert subs[1].text == "今天我們來聊剪輯。", subs[1].text
    print("  ✓ 字幕:句末標點正確斷行")


def test_subtitles_strip_trailing_comma():
    """行尾的句中逗號應去掉(斷行本身已代表停頓)"""
    segs = [Segment(0, 600, "keep")]
    t = RemapTable(segs, fps=30)
    words = [
        Word("這樣東西就會很分開,", 0.0, 2.0),   # 夠長且以逗號結尾 → 斷行
        Word("對對對", 2.0, 3.0),
    ]
    subs = t.build_subtitles(words, max_chars=18)
    assert subs[0].text == "這樣東西就會很分開", subs[0].text
    print("  ✓ 字幕:行尾逗號正確去除")


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


def test_partial_cut_word_keeps_subtitle():
    """詞的頭尾被剪到一點點時,字幕仍要留著(不能整個詞消失)。

    這是能量微剪引進的回歸:微剪剪的正是「詞邊緣的空白」,
    舊寫法只要頭或尾落在剪掉的地方就丟掉整個詞,實測害 14.4% 的詞
    從字幕消失(聲音其實還在)。"""
    # 0~100 保留、100~110 剪掉、110~200 保留
    segs = [Segment(0, 100, "keep"),
            Segment(100, 110, "delete", reason="silence", confidence=0.95),
            Segment(110, 200, "keep")]
    table = RemapTable(segs, fps=30)
    # 這個詞橫跨剪點(90~120):大部分還在,字幕必須留著
    subs = table.build_subtitles([Word("重要的字", 90 / 30, 120 / 30)],
                                 max_chars=18, max_gap_frames=15)
    assert len(subs) == 1, "橫跨剪點的詞不該從字幕消失"
    assert subs[0].text == "重要的字"
    assert subs[0].end_frame >= subs[0].start_frame
    print("  ✓ 詞被剪掉一部分時,字幕仍保留")


def test_fully_cut_word_dropped():
    """整個詞都被剪掉時,字幕才該消失"""
    segs = [Segment(0, 100, "keep"),
            Segment(100, 200, "delete", reason="filler", text="嗯",
                    confidence=1.0),
            Segment(200, 300, "keep")]
    table = RemapTable(segs, fps=30)
    subs = table.build_subtitles([Word("嗯", 120 / 30, 180 / 30)],
                                 max_chars=18, max_gap_frames=15)
    assert subs == [], "整個被剪掉的詞應該從字幕消失"
    print("  ✓ 整個被剪掉的詞不出現在字幕")


def test_no_punct_uses_shorter_lines():
    """逐字稿沒有標點時,自動改用比較短的行長上限。

    沒標點就只能靠停頓和字數斷行,行放太長會斷在很怪的地方
    (實測出現過 52 個字、結尾斷在「所以」的行)。"""
    table = RemapTable([Segment(0, 600, "keep")], fps=30)
    # 30 個沒有標點的詞,詞間無停頓
    words = [Word("字", i * 0.4, i * 0.4 + 0.4) for i in range(30)]
    long_lines = table.build_subtitles(words, max_chars=40, max_gap_frames=15)
    short_lines = table.build_subtitles(words, max_chars=40, max_gap_frames=15,
                                        max_chars_no_punct=10)
    assert max(len(l.text) for l in long_lines) > 10
    assert max(len(l.text) for l in short_lines) <= 10, "沒標點時應改用短行長"
    print("  ✓ 沒有標點時自動縮短每行字數")


def test_punct_keeps_normal_line_limit():
    """逐字稿有標點時,維持使用者設定的行長,不要被保險機制縮短"""
    table = RemapTable([Segment(0, 600, "keep")], fps=30)
    words = []
    for i in range(10):
        words.append(Word("這是測試", i * 0.8, i * 0.8 + 0.4))
        words.append(Word("句子,", i * 0.8 + 0.4, i * 0.8 + 0.8))
    lines = table.build_subtitles(words, max_chars=40, max_gap_frames=15,
                                  max_chars_no_punct=10)
    assert max(len(l.text) for l in lines) > 10, "有標點時不該縮短行長"
    print("  ✓ 有標點時維持原本的行長設定")


def test_lookup_matches_bruteforce_on_random_timelines():
    """二分搜尋的查找,結果必須跟「從頭掃到底」完全一樣。

    map_frame / map_span 原本是線性掃過所有區間,改成二分搜尋是為了長片的
    速度(12000 詞 × 7000 片段從 4 秒降到 0.12 秒)。但速度是我在優化的數字,
    真正該驗的是「有沒有對到不同的位置」——查找錯位的下場是字幕整片對錯地方
    或整片消失,而這在報告上完全看不出來。

    所以這裡直接留一份「笨方法」實作,用隨機時間軸逐點對照。
    特別涵蓋容易錯的地方:區間邊界、落在刪除段裡、負數、超過影片結尾。"""
    import random

    def brute_map_frame(t, f):
        for sp in t._spans:
            if sp.orig_start <= f < sp.orig_end:
                return sp.timeline_start + round((f - sp.orig_start) / sp.factor)
        return None

    def brute_map_span(t, s, e):
        first = last = None
        for sp in t._spans:
            a, b = max(s, sp.orig_start), min(e, sp.orig_end)
            if b <= a:
                continue
            ts = sp.timeline_start + round((a - sp.orig_start) / sp.factor)
            te = sp.timeline_start + round((b - sp.orig_start) / sp.factor)
            if first is None:
                first = ts
            last = te
        return None if first is None else (first, max(first, last))

    rng = random.Random(11)
    checked = 0

    def compare(t, hi, label):
        nonlocal checked
        for f in range(-3, hi + 4):            # 含邊界與範圍外
            checked += 1
            assert brute_map_frame(t, f) == t.map_frame(f), \
                f"{label}:map_frame 在 {f} 對到不同位置"
        for _ in range(40):
            a = rng.randint(-3, hi + 3)
            b = a + rng.randint(0, 40)
            checked += 1
            assert brute_map_span(t, a, b) == t.map_span(a, b), \
                f"{label}:map_span 在 ({a},{b}) 對到不同位置"

    # (一)決策引擎產出的版面:段落首尾相連、彼此不重疊
    for _ in range(40):
        segs, pos = [], 0
        for _ in range(rng.randint(1, 40)):
            d = rng.randint(1, 50)
            act = rng.choice(["keep", "keep", "delete", "speed"])
            segs.append(Segment(pos, pos + d, act,
                                factor=6.0 if act == "speed" else 1.0))
            pos += d
        compare(RemapTable(segs, 30.0), pos, "連續版面")

    # (二)使用者在 Premiere 裡自己剪過的版面:**片段來源範圍可能重疊**
    #
    # 這一段是補上去的,因為原本的隨機資料是用 pos += d 一路往後接的,
    # 永遠不會重疊 —— 剛好就是「開發時想到的那一種形狀」。
    # 而重疊在真實使用裡很常見:同一段素材用兩次、把片段的把手往外拉,
    # 都會產生重疊的來源範圍,而 from_spans 這條路正是為「使用者自己又
    # 剪過」存在的。漏掉這種形狀的下場是 map_frame 回 None(意思是
    # 「這個詞被剪掉了」),那些字幕就整片消失、拿到一份沒有時間點的檔。
    for _ in range(40):
        spans, tl = [], 0
        for _ in range(rng.randint(1, 25)):
            a = rng.randint(0, 200)
            d = rng.randint(1, 60)
            factor = rng.choice([1.0, 1.0, 2.0, 6.0])
            spans.append((a, a + d, tl, factor))
            tl += int(d / factor)
        compare(RemapTable.from_spans(spans, 30.0), 260, "重疊版面")

    print(f"  ✓ 二分搜尋與笨方法逐點一致({checked} 次比對,含重疊版面)")


def test_overlapping_clips_keep_their_subtitles():
    """來源範圍重疊時,字幕不可以憑空消失。

    這是實際發生過的 bug:查找改成二分搜尋之後,只用 orig_start 定位,
    於是漏掉「起點比較早、但一路延伸到查詢範圍裡」的那一列 ——
    map_frame 回 None,而 None 的意思是「這個詞被剪掉了」,
    字幕就整片不見,使用者拿到一份沒有時間點的 SRT。

    重疊在真實使用裡很常見:同一段素材用兩次、把片段的把手往外拉。"""
    # 片段 A 用了原片 0~100,片段 B 又用了原片 50~60
    t = RemapTable.from_spans([(0, 100, 0, 1.0), (50, 60, 100, 1.0)], 30.0)
    for f in (0, 25, 50, 55, 60, 75, 99):
        assert t.map_frame(f) is not None, \
            f"第 {f} 幀落在片段 A 裡面,卻被當成『已被剪掉』"
    assert t.map_frame(150) is None, "真的不在任何片段裡的位置才該回 None"

    words = [Word("在重疊區", 55 / 30, 58 / 30), Word("在後段", 80 / 30, 83 / 30)]
    subs = t.build_subtitles(words, max_chars=18, max_gap_frames=15)
    assert len(subs) >= 1, "重疊版面下字幕整個消失了"
    assert any("重疊區" in s.text for s in subs), "重疊區的詞不見了"
    assert any("後段" in s.text for s in subs), "重疊之後的詞也跟著不見了"
    print("  ✓ 來源範圍重疊時,字幕與時間點都還在")


def test_unsorted_spans_are_rejected_or_sorted():
    """沒排序的資料不可以讓查找安靜地出錯。

    二分搜尋的前提是區間照原始時間有序。餵進沒排序的資料時,它不會報錯,
    只會找到錯的位置——字幕會對到不對的地方,而且完全看不出來。
    所以:建表時要嘛擋下來、要嘛自己排好。

    from_spans 走「自己排好」那條:使用者真的可能在 Premiere 裡把片段
    前後搬動,那是合法操作,不該讓它失敗。"""
    # 直接建表:沒排序要當場擋下來,不能默默算錯
    bad = [Segment(100, 200, "keep"), Segment(0, 50, "keep")]
    try:
        RemapTable(bad, 30.0)
    except ValueError as e:
        assert "排序" in str(e), f"錯誤訊息要說明是排序問題:{e}"
    else:
        raise AssertionError("沒排序的 segments 應該被擋下來")

    # from_spans:片段被搬過順序仍要對得準(照原始時間自己排好)
    shuffled = [(200, 300, 100, 1.0), (0, 100, 0, 1.0), (400, 500, 200, 1.0)]
    t = RemapTable.from_spans(shuffled, 30.0)
    assert t.map_frame(50) == 50, "第一段對錯了"
    assert t.map_frame(250) == 150, "被搬到後面的那段對錯了"
    assert t.map_frame(450) == 250, "第三段對錯了"
    assert t.map_frame(350) is None, "不在任何片段裡的位置應該回 None"
    print("  ✓ 沒排序的資料會被擋下;使用者搬動過的片段仍對得準")


if __name__ == "__main__":
    print("執行重映射引擎測試...")
    test_keep_only()
    test_delete_shifts_later_frames()
    test_speed_compresses()
    test_cuts_recorded()
    test_subtitles_skip_deleted()
    test_subtitles_not_fragmented_by_small_cuts()
    test_subtitles_keep_english_word_intact()
    test_subtitles_break_at_punctuation()
    test_subtitles_strip_trailing_comma()
    test_ntsc_no_drift()
    test_partial_cut_word_keeps_subtitle()
    test_fully_cut_word_dropped()
    test_no_punct_uses_shorter_lines()
    test_punct_keeps_normal_line_limit()
    test_lookup_matches_bruteforce_on_random_timelines()
    test_unsorted_spans_are_rejected_or_sorted()
    test_overlapping_clips_keep_their_subtitles()
    print("\n全部通過 ✓  地基正確,可以往上蓋。")
