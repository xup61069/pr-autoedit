"""
端到端煙霧測試:用假的轉錄資料,不碰 GPU/ffmpeg/外部套件,
驗證 決策 -> 重映射 -> 字幕 -> 報告 -> v1 timeline 這條主幹跑得通。
執行:python -m tests.test_e2e_smoke
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Word, Timeline
from core.decision import build_segments
from core.remap import RemapTable
from modules.subtitles import write_srt
from modules.report import generate as gen_report
from modules.premiere_xml import build_v1_timeline
import config.settings as cfg

# 鎖回預設參數,不受使用者 settings_local 覆寫影響(理由見 test_decision)
cfg.SILENCE_ACTION = "speed"
cfg.SILENCE_THRESHOLD_SEC = 1.2
cfg.SILENCE_PADDING_SEC = 0.15
cfg.SILENCE_SPEED_FACTOR = 6.0
cfg.FILLERS_ALWAYS = ["嗯", "呃", "啊", "欸", "唉", "痾", "喔"]
cfg.FILLERS_CONDITIONAL = ["就是", "然後", "那個", "這個", "所以說", "對對對"]
cfg.SUBTITLE_MAX_CHARS = 18


def fake_transcript():
    """模擬一段有冗詞、有靜音的口白"""
    return [
        Word("大家好", 0.0, 0.8),
        Word("嗯", 0.95, 1.1),              # 必刪冗詞(前後各停約 0.15 秒)
        Word("今天", 1.25, 1.7),
        Word("要", 1.7, 1.9),
        Word("教", 1.9, 2.2),
        Word("Premiere", 2.2, 3.0),
        Word("的", 3.0, 3.2),
        Word("剪輯", 3.2, 3.8),
        # --- 5 秒靜音 ---
        Word("然後", 8.8, 9.1),             # 句首孤立,應刪
        Word("我們", 9.1, 9.5),
        Word("打開", 9.5, 10.0),
        Word("設定", 10.0, 10.6),
    ]


def main():
    fps = 30.0
    words = fake_transcript()
    total_frames = int(11.0 * fps)

    segs = build_segments(words, fps, total_frames)
    timeline = Timeline(fps=fps, source="/fake/01_clean_av.mp4", segments=segs)
    table = RemapTable(segs, fps)

    out = os.path.join(os.path.dirname(__file__), "..", "output", "_smoke")
    os.makedirs(out, exist_ok=True)

    # 產出所有審閱檔案
    timeline.to_json(os.path.join(out, "03_timeline.json"))
    build_v1_timeline(timeline, os.path.join(out, "03_timeline.v1.json"))
    subs = table.build_subtitles(words, max_chars=cfg.SUBTITLE_MAX_CHARS,
                                max_gap_frames=15)
    write_srt(subs, fps, os.path.join(out, "04_subtitles.srt"))
    gen_report(timeline, words, table, os.path.join(out, "04_report.html"))

    # 驗證
    print("\n--- 驗證 ---")
    srt_text = open(os.path.join(out, "04_subtitles.srt"), encoding="utf-8").read()
    assert "嗯" not in srt_text, "冗詞不該出現在字幕"
    assert "Premiere" in srt_text, "正常詞應保留"
    assert "然後" not in srt_text, "句首孤立的『然後』應被刪"
    print("  ✓ 字幕內容正確(冗詞剔除、正常詞保留)")

    speeds = [s for s in segs if s.action == "speed"]
    assert len(speeds) >= 1, "5秒靜音應產生快轉段"
    print(f"  ✓ 靜音轉快轉({len(speeds)} 段)")

    report = open(os.path.join(out, "04_report.html"), encoding="utf-8").read()
    assert "審閱報告" in report and "<table" in report
    print("  ✓ 審閱報告 HTML 產出正常")

    print("\n端到端主幹跑通 ✓  產物在 output/_smoke/")


def test_prompt_always_demonstrates_punctuation():
    """提示詞一定要帶標點示範句。

    Whisper 會模仿提示詞的書寫風格:提示詞沒標點,它就吐出一整片沒標點的字,
    字幕斷行只能靠停頓硬切、句子被切得很怪。以前是靠詞彙表那串「A、B、C。」
    間接示範,詞彙表一清空就破功(實測 0 個句號)。這個測試守住基底提示詞。"""
    from modules.transcribe import _build_initial_prompt
    old_vocab = cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB, cfg.WHISPER_INITIAL_PROMPT
    cfg.WHISPER_INITIAL_PROMPT = None
    for cats, custom in [([], []), (["剪輯"], ["我的頻道"])]:
        cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB = cats, custom
        p = _build_initial_prompt()
        assert p.count("。") >= 2 and ("," in p or "," in p),             f"提示詞缺少標點示範:{p}"
    cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB, cfg.WHISPER_INITIAL_PROMPT = old_vocab
    print("  ✓ 提示詞帶標點示範句(有無詞彙表都是)")


def test_prompt_fits_token_budget():
    """提示詞不能超過 Whisper 的長度上限,而且示範句一定要在最尾巴。

    Whisper 只保留提示詞的「最後」223 個 token,超過就從開頭砍掉。
    所以示範句若排在前面,詞彙表一長就會把整句標點示範砍光——
    標點會全部消失而且毫無徵兆(就是之前那個字幕斷行變爛的 bug)。
    這個測試守住兩件事:順序不能被改回去、每一類單選都塞得下。"""
    import io, contextlib
    from modules.transcribe import (_build_initial_prompt, _est_tokens,
                                    _PROMPT_TOKEN_BUDGET)
    old = (cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB, cfg.WHISPER_INITIAL_PROMPT,
           cfg.VOCAB_PRESETS)
    cfg.WHISPER_INITIAL_PROMPT = None
    # 只測「內建」詞庫。使用者在面板加的類型合併在 VOCAB_PRESETS 裡,
    # 他想塞多長是他的自由(面板會即時告訴他快超標了),
    # 不該讓他的個人詞庫決定專案測試過不過。
    cfg.VOCAB_PRESETS = cfg.DEFAULTS["VOCAB_PRESETS"]
    tail = "你可以自己調整看看。"

    # 每一類單選都要「完整放得下」,而且還要留得下幾個個人術語 ——
    # 光是選一類就跳截斷警告的話,警告會變成狼來了,你就不會再看它了。
    for cat in cfg.VOCAB_PRESETS:
        cfg.VOCAB_CATEGORIES = [cat]
        cfg.CUSTOM_VOCAB = ["我的頻道名", "常用術語", "某某老師"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p = _build_initial_prompt()
        assert "⚠" not in buf.getvalue(), \
            f"教學類型「{cat}」的詞庫太長,選一類再加幾個個人術語就被砍了。請精簡它。"
        assert p.endswith(tail), f"「{cat}」的提示詞結尾不是標點示範句"

    # 最壞情況:全選 + 個人術語,仍不可超過上限,示範句仍在尾巴
    cfg.VOCAB_CATEGORIES = list(cfg.VOCAB_PRESETS)
    cfg.CUSTOM_VOCAB = ["我的頻道名", "自訂術語"]
    with contextlib.redirect_stdout(io.StringIO()):
        p = _build_initial_prompt()
    assert _est_tokens(p) <= _PROMPT_TOKEN_BUDGET, "全選時提示詞超出長度上限"
    assert p.endswith(tail), "全選時標點示範句沒有留在最尾巴"
    assert "我的頻道名" in p, "個人術語被砍掉了(它應該排最前面、最不該犧牲)"

    (cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB, cfg.WHISPER_INITIAL_PROMPT,
     cfg.VOCAB_PRESETS) = old
    print("  ✓ 提示詞不超長、示範句在尾巴、個人術語不被犧牲")


def test_workspace_tidies_output():
    """output 資料夾最外層只留「你會打開的」,中繼檔收進 _work/。

    重點是搬移不能弄丟轉錄快取 —— 那是最花時間的一步,
    弄丟就要重跑好幾分鐘的語音辨識。"""
    import tempfile, shutil
    from modules.workspace import prepare, wpath, INTERNAL_DIR

    d = tempfile.mkdtemp(prefix="ws_test_")
    try:
        # 模擬舊版:所有檔案平鋪在最外層
        old_files = ["01_clean_av.mp4", "04_report.html", "04_project.xml",
                     "04_subtitles.srt", "01_raw.wav", "02_transcript.json",
                     "03_timeline.json", "01_mux_info.txt"]
        for n in old_files:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("x" if n != "02_transcript.json" else "快取內容")

        prepare(d)

        outer = sorted(f for f in os.listdir(d) if f != INTERNAL_DIR)
        assert outer == ["01_clean_av.mp4", "04_project.xml",
                         "04_report.html", "04_subtitles.srt"], \
            f"最外層應該只剩四個給人用的檔,實際是 {outer}"

        # 轉錄快取要跟著搬進去,不能不見
        cache = wpath(d, "02_transcript.json")
        assert os.path.exists(cache), "轉錄快取在搬移過程中不見了"
        with open(cache, encoding="utf-8") as f:
            assert f.read() == "快取內容", "轉錄快取內容被弄壞了"

        # 已淘汰的舊檔要清掉
        assert not os.path.exists(os.path.join(d, "01_mux_info.txt"))

        prepare(d)      # 重複執行不能出事(每次跑管線都會呼叫)
        assert os.path.exists(cache)
        print("  ✓ output 最外層只留四個檔、中繼檔歸位、快取沒弄丟")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_vocab_has_no_redundancy():
    """詞庫不能重複收同一串字。

    提示詞的額度是照「字元」算的,而且上限很硬(見 test_prompt_fits_token_budget)。
    重複有兩種,兩種都是在浪費額度、把真正需要的術語擠出去:
      1. 同一個詞出現在兩個類型裡(多選時會被去重,但代表分類沒分乾淨);
      2. 短詞已經整個包在長詞裡面 —— 「遮罩」的字元本來就在「軌道遮罩」中,
         兩個都收等於同一串字付兩次錢。
    這兩件事看程式碼很難發現(詞庫是一大片字),所以用測試守著。

    註:檢查的是「內建」那份(DEFAULTS),不是合併後的。使用者自己在面板
    加的類型存在 config/vocab_local.json,那是他家的事——不該因為他加了
    一個跟內建重複的詞,就讓整個專案的測試變紅。"""
    builtin = cfg.DEFAULTS["VOCAB_PRESETS"]
    seen, dup = {}, []
    for cat, words in builtin.items():
        assert len(words) == len(set(words)), f"「{cat}」自己就有重複的詞"
        for w in words:
            if w in seen:
                dup.append(f"「{w}」同時在 {seen[w]} 和 {cat}")
            seen[w] = cat
    assert not dup, "跨類型重複:" + "、".join(dup)

    contained = [f"「{cat}」的「{a}」已經包在「{b}」裡面"
                 for cat, ws in builtin.items()
                 for a in ws for b in ws if a != b and a in b]
    assert not contained, "重複收了同一串字:" + "、".join(contained)
    print(f"  ✓ {len(builtin)} 類內建詞庫沒有重複、也沒有包含關係的冗詞")


def test_vocab_local_merges_and_keeps_builtin():
    """面板「編輯類型」寫的 vocab_local.json 要能改內建、也能開新類型,
    而且內建那份必須原封不動留著——不然「還原成內建」就回不去了。

    也要確認檔案壞掉時不會把整條管線帶下水:這個檔是使用者會自己去編的,
    少一個逗號就整支程式起不來的話,他只會看到一堆看不懂的錯誤。"""
    import json, importlib, shutil
    p = os.path.join(os.path.dirname(__file__), "..", "config", "vocab_local.json")
    bak = p + ".testbak"
    had = os.path.exists(p)
    if had:
        shutil.copy2(p, bak)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"剪輯": ["我的自訂詞"], "木工": ["榫接", "刨刀"]},
                      f, ensure_ascii=False)
        m = importlib.reload(cfg)
        assert m.VOCAB_PRESETS["剪輯"] == ["我的自訂詞"], "同名應該蓋掉內建那一類"
        assert m.VOCAB_PRESETS["木工"] == ["榫接", "刨刀"], "新名字應該多一類"
        assert "Lumetri" in m.DEFAULTS["VOCAB_PRESETS"]["剪輯"], \
            "內建那份被改掉了,還原成內建會回不去"

        # 檔案壞掉:應該安靜地當作沒有,而不是讓 import 直接爆掉
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 這不是合法的 JSON")
        m = importlib.reload(cfg)
        assert "Lumetri" in m.VOCAB_PRESETS["剪輯"], "檔案壞掉時應退回內建詞庫"
        print("  ✓ 個人詞庫:同名覆蓋、新名新增、內建保留、檔案壞掉不會爆")
    finally:
        if had:
            shutil.move(bak, p)
        elif os.path.exists(p):
            os.remove(p)
        importlib.reload(cfg)


def test_voicefx_detection():
    """自動找 VoiceFX:要找得到、要指到「內層」、沒裝時要乾淨地空著。

    這裡守的是兩個都會靜靜壞掉的情況:
      1. 有人把它「簡化」成回傳外層資料夾 —— VST3 外層只是個殼,
         pedalboard 載不動,但要跑到真的降噪那一步才會爆。
      2. 沒裝外掛的人拿到一條指向不存在檔案的路徑(以前寫死路徑就是這樣,
         別人抓下來永遠是壞的,而且從畫面上看不出來)。"""
    import tempfile, shutil
    from config.settings import _find_voicefx

    d = tempfile.mkdtemp(prefix="vst_test_")
    old = os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")
    try:
        os.environ["PROGRAMFILES"] = d
        os.environ["LOCALAPPDATA"] = d

        # 沒裝:空清單,不能亂猜一條路徑出來
        assert _find_voicefx() == [], "沒裝外掛時應該是空清單"

        # 裝了(收在廠商子資料夾裡,VoiceFX 實際就是這樣裝的)
        bundle = os.path.join(d, "Common Files", "VST3", "TonPlugIns",
                              "VoiceFX.vst3", "Contents", "x86_64-win")
        os.makedirs(bundle)
        inner = os.path.join(bundle, "VoiceFX.vst3")
        open(inner, "wb").close()
        found = _find_voicefx()
        assert found == [inner], f"沒找到內層的 .vst3,拿到的是 {found}"
        assert os.path.isfile(found[0]), "找到的必須是檔案,不是外層資料夾"
        print("  ✓ VoiceFX 自動偵測:找得到、指到內層檔案、沒裝時空著")
    finally:
        for k, v in zip(("PROGRAMFILES", "LOCALAPPDATA"), old):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
    test_prompt_always_demonstrates_punctuation()
    test_prompt_fits_token_budget()
    test_workspace_tidies_output()
    test_vocab_has_no_redundancy()
    test_vocab_local_merges_and_keeps_builtin()
    test_voicefx_detection()
