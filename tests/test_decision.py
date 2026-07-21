"""決策引擎測試。執行:python -m tests.test_decision"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Word, Segment
from core.decision import (build_segments, trim_quiet_inside,
                          find_retakes, drop_retakes, protect_words)
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
cfg.FILLER_PAUSE_SEC = 0.0          # 預設:不要求停頓(Whisper 用)
cfg.FILLER_ISOLATED_GAP_SEC = 0.25
cfg.RETAKE_DETECT = False           # 預設關閉(見 settings 說明)


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
    """黏在語流中/句尾的字不當語氣詞(FunASR 逐字輸出的「好啊」保護)。

    這個保護由 FILLER_PAUSE_SEC 控制,預設 0(不要求停頓、剪最兇);
    用 funasr 時要設 0.1 才會啟動,所以這裡明確設定它來測這個功能。"""
    cfg.FILLER_PAUSE_SEC = 0.1
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
    cfg.FILLER_PAUSE_SEC = 0.0          # 還原,不影響其他測試
    print("  ✓ 句中/句尾黏著的『啊』被保留(FILLER_PAUSE_SEC=0.1 時)")


def test_filler_deleted_without_pause_requirement():
    """FILLER_PAUSE_SEC=0(預設):語氣詞黏在句子裡也照刪(Whisper 用,剪最兇)"""
    words = [
        Word("好", 0.0, 0.3),
        Word("嗯", 0.3, 0.5),            # 前後零間隔
        Word("那", 0.5, 0.7),
    ]
    segs = build_segments(words, fps=30, total_frames=60)
    deletes = [s for s in segs if s.action == "delete"]
    assert any(s.text == "嗯" for s in deletes), \
        "不要求停頓時,黏著的語氣詞也該被刪"
    print("  ✓ 不要求停頓時,黏在句中的『嗯』照樣刪除")


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


def test_micro_trim_cuts_inside_keep():
    """能量微剪:保留段裡面的安靜區被挖成刪除段,前後說話部分留著"""
    segs = [Segment(0, 300, "keep")]
    out = trim_quiet_inside(segs, [(100, 160)], fps=30)
    assert [(s.start, s.end, s.action) for s in out] == [
        (0, 100, "keep"), (100, 160, "delete"), (160, 300, "keep")]
    assert out[1].reason == "silence"
    print("  ✓ 講話段裡的安靜區被剪掉,前後說話保留")


def test_micro_trim_never_touches_music():
    """音樂/音效段是刻意保護的,微剪絕對不能動它"""
    segs = [Segment(0, 200, "keep", reason="music", confidence=0.8)]
    out = trim_quiet_inside(segs, [(50, 150)], fps=30)
    assert len(out) == 1 and out[0].action == "keep" and out[0].reason == "music"
    print("  ✓ 音樂段不被微剪動到")


def test_micro_trim_keeps_coverage():
    """微剪後段落仍要首尾相連、完整覆蓋(時間軸不能出現破洞)"""
    segs = [Segment(0, 100, "keep"),
            Segment(100, 150, "delete", reason="filler", text="嗯"),
            Segment(150, 400, "keep")]
    out = trim_quiet_inside(segs, [(20, 60), (200, 260), (380, 500)], fps=30)
    assert out[0].start == 0 and out[-1].end == 400
    for a, b in zip(out, out[1:]):
        assert a.end == b.start, f"微剪後段落斷裂:{a.end} != {b.start}"
    assert any(s.action == "delete" and s.start == 20 for s in out)
    print("  ✓ 微剪後覆蓋完整、超出範圍的安靜區被夾住")


def test_micro_trim_no_quiet_is_noop():
    """沒偵測到安靜區時,結果必須跟原本完全一樣"""
    segs = build_segments([Word("大家好", 0.0, 1.0), Word("今天", 1.2, 2.0)],
                        fps=30, total_frames=90)
    assert trim_quiet_inside(segs, [], fps=30) == segs
    print("  ✓ 沒有安靜區時行為不變")


def _retake_defaults():
    cfg.RETAKE_DETECT = True
    cfg.RETAKE_SIMILARITY = 0.85
    cfg.RETAKE_MIN_CHARS = 4
    cfg.RETAKE_MAX_CHARS = 24
    cfg.RETAKE_BOUNDARY_GAP_SEC = 0.15
    cfg.RETAKE_CONFIDENCE = 0.5


def test_retake_full_repeat():
    """整句重講:砍掉前面那次,留後面那次"""
    _retake_defaults()
    words = [Word("我們", 0.0, 0.3), Word("按這個鈕", 0.3, 0.9),
            Word("我們", 1.2, 1.5), Word("按這個鈕", 1.5, 2.1)]
    r = find_retakes(words, fps=30)
    assert len(r) == 1, f"應抓到 1 處重講,實際 {r}"
    assert r[0][0] == 0 and r[0][1] == round(0.9 * 30)
    print("  ✓ 整句重講:砍掉前一次,保留重講的那次")


def test_retake_false_start():
    """講一半重來(前面是後面的開頭):砍掉沒講完的那次"""
    _retake_defaults()
    words = [Word("我們", 0.0, 0.3), Word("按這", 0.3, 0.6),
            Word("我們", 0.9, 1.2), Word("按這個鈕開始", 1.2, 2.0)]
    r = find_retakes(words, fps=30)
    assert len(r) == 1 and r[0][1] == round(0.6 * 30)
    print("  ✓ 講一半重來:砍掉沒講完的前半段")


def test_retake_needs_pause():
    """交界處沒停頓就不算重講(擋掉正常重複用字的誤判)"""
    _retake_defaults()
    words = [Word("我們", 0.0, 0.3), Word("按這個鈕", 0.3, 0.9),
            Word("我們", 0.92, 1.2), Word("按這個鈕", 1.2, 1.8)]
    assert find_retakes(words, fps=30) == []
    print("  ✓ 沒有停頓的重複不被當成重講")


def test_retake_off_by_default():
    """關掉時完全不動作"""
    _retake_defaults()
    cfg.RETAKE_DETECT = False
    words = [Word("我們", 0.0, 0.3), Word("按這個鈕", 0.3, 0.9),
            Word("我們", 1.2, 1.5), Word("按這個鈕", 1.5, 2.1)]
    assert find_retakes(words, fps=30) == []
    cfg.RETAKE_DETECT = False          # 維持預設關閉
    print("  ✓ RETAKE_DETECT=False 時不動作")


def test_drop_retakes_keeps_coverage():
    """砍掉重講後,段落仍要首尾相連、完整覆蓋"""
    _retake_defaults()
    segs = [Segment(0, 300, "keep")]
    out = drop_retakes(segs, [(50, 120, "我們按這個鈕")], fps=30)
    assert out[0].start == 0 and out[-1].end == 300
    for a, b in zip(out, out[1:]):
        assert a.end == b.start
    cut = [s for s in out if s.action == "delete"]
    assert len(cut) == 1 and cut[0].reason == "retake"
    assert cut[0].text == "我們按這個鈕"
    assert cut[0].confidence < cfg.MARKER_MAX_CONFIDENCE, \
        "重講刪除的信心必須低於 marker 門檻,才會下 marker 供人確認"
    cfg.RETAKE_DETECT = False
    print("  ✓ 砍重講後覆蓋完整,且會下 marker 供確認")


def test_protect_words_skips_whole_word_cuts():
    """安靜區若會把整個詞吃掉,就不剪(否則聲音和字幕都會缺字)"""
    words = [Word("你", 1.0, 1.2), Word("好嗎", 2.0, 2.6)]
    # (25,45) 完整蓋住「你」(30~36 幀) -> 應被丟棄
    # (100,140) 沒蓋住任何詞 -> 應保留
    out = protect_words([(25, 45), (100, 140)], words, fps=30)
    assert out == [(100, 140)], f"實際 {out}"
    print("  ✓ 會吃掉整個詞的安靜區不剪,其餘照剪")


def test_protect_words_allows_partial_overlap():
    """只蓋到詞的一部分(詞邊緣的空白)照剪不誤"""
    words = [Word("大家好", 1.0, 2.0)]      # 30~60 幀
    out = protect_words([(50, 80)], words, fps=30)
    assert out == [(50, 80)], "只重疊詞尾的安靜區應該照剪"
    print("  ✓ 只剪到詞邊緣的空白不受影響")


def test_presets_keep_their_promise():
    """每個內建組合的名字,不能承諾它做不到的事。

    這個專案已經在同一個地方栽過兩次:
      第一次 —— 「保守」「完整保留」寫了 SILENCE_ACTION=speed,卻又吃到
                 當時獨立的畫面偵測開關,畫面靜止的停頓照樣被剪掉。
      第二次 —— 「完整保留(僅壓縮停頓)」只用 FILLER_PAUSE_SEC 擋住了
                 「嗯、呃」,沒擋住「然後、就是」(那條路吃的是
                 FILLER_ISOLATED_GAP_SEC),名字說完整保留卻在砍連接詞。

    兩次都不是單一函式的邏輯錯誤,是「組合設定之間的交互作用」,
    所以這裡刻意走完整的 build_segments、逐個組合實跑,而不是檢查字典內容。
    名字裡有「保守 / 不剪掉 / 不動」的,就真的不准出現停頓被刪除。"""
    saved = {k: getattr(cfg, k) for k in cfg.PRESET_KEYS}
    try:
        for name, preset in cfg.SETTING_PRESETS.items():
            # 照面板 presetApply 的邏輯:組合沒寫的一律回內建預設
            for k in cfg.PRESET_KEYS:
                setattr(cfg, k, preset[k] if k in preset else cfg.DEFAULTS[k])
            words = [Word("大家好", 0.0, 1.0), Word("接下來", 6.0, 7.0)]
            segs = build_segments(words, 30.0, int(8.0 * 30))
            cut_silence = [s for s in segs
                           if s.action == "delete" and s.reason == "silence"]
            if "保守" in name:
                assert not cut_silence, \
                    f"組合「{name}」的名字承諾不剪停頓,實際剪掉了 {cut_silence}"
                assert any(s.action == "speed" for s in segs), \
                    f"組合「{name}」該把停頓壓縮掉,實際完全沒動"
            # 沒有任何組合能承諾「完全不刪字」——去不去冗詞不在 PRESET_KEYS 裡
            assert "完整保留" not in name, (
                f"組合「{name}」的名字暗示連冗詞都會留著,但組合動不到冗詞設定。"
                "請改成只承諾停頓的名字。")
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)
    print(f"  ✓ {len(cfg.SETTING_PRESETS)} 個內建組合的名字都跟實際行為相符")


if __name__ == "__main__":
    print("執行決策引擎測試...")
    test_always_filler_deleted()
    test_embedded_char_kept()
    test_filler_deleted_without_pause_requirement()
    test_conditional_filler_kept_in_context()
    test_conditional_filler_deleted_when_repeated()
    test_silence_becomes_speed()
    test_coverage_complete()
    test_micro_trim_cuts_inside_keep()
    test_micro_trim_never_touches_music()
    test_micro_trim_keeps_coverage()
    test_micro_trim_no_quiet_is_noop()
    test_retake_full_repeat()
    test_retake_false_start()
    test_retake_needs_pause()
    test_retake_off_by_default()
    test_drop_retakes_keeps_coverage()
    test_protect_words_skips_whole_word_cuts()
    test_protect_words_allows_partial_overlap()
    test_presets_keep_their_promise()
    print("\n全部通過 ✓  決策引擎邏輯正確。")
