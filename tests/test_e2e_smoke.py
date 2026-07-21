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
    少一個逗號就整支程式起不來的話,他只會看到一堆看不懂的錯誤。

    ⚠️ 這個測試「不」碰使用者真正的 config/vocab_local.json。
    以前它是直接對那個檔寫入測試資料(中間還故意寫一段壞掉的 JSON),
    靠 finally 還原。只要跑到一半被 Ctrl-C、被面板的停止鈕收掉、或當機,
    使用者的個人詞庫就留在「測試資料」或「壞掉的 JSON」狀態,
    而備份是固定檔名 .testbak,跑第二次就被自己蓋掉,救都救不回來。
    現在改成整個 config 資料夾複製到暫存區,對副本操作,永遠碰不到本尊。"""
    import json, importlib, shutil, tempfile, sys as _sys

    real_cfg_dir = os.path.join(os.path.dirname(__file__), "..", "config")
    sandbox = tempfile.mkdtemp(prefix="vocab_test_")
    try:
        # 複製一份可拋棄的 config 套件(settings.py 會去讀自己旁邊的
        # vocab_local.json,所以要連 settings.py 一起搬過去)
        pkg = os.path.join(sandbox, "config")
        os.makedirs(pkg)
        shutil.copy2(os.path.join(real_cfg_dir, "settings.py"), pkg)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        vocab_file = os.path.join(pkg, "vocab_local.json")

        def load(vocab_text):
            """用 sandbox 裡的 config 重新載入一次 settings,回傳那個模組"""
            with open(vocab_file, "w", encoding="utf-8") as f:
                f.write(vocab_text)
            saved_path, saved_mods = list(_sys.path), {}
            for name in ("config", "config.settings"):
                saved_mods[name] = _sys.modules.pop(name, None)
            _sys.path.insert(0, sandbox)
            try:
                import config.settings as sandboxed
                return importlib.reload(sandboxed)
            finally:
                _sys.path[:] = saved_path
                for name, mod in saved_mods.items():
                    if mod is not None:
                        _sys.modules[name] = mod
                    else:
                        _sys.modules.pop(name, None)

        m = load(json.dumps({"剪輯": ["我的自訂詞"], "木工": ["榫接", "刨刀"]},
                            ensure_ascii=False))
        assert m.VOCAB_PRESETS["剪輯"] == ["我的自訂詞"], "同名應該蓋掉內建那一類"
        assert m.VOCAB_PRESETS["木工"] == ["榫接", "刨刀"], "新名字應該多一類"
        assert "Lumetri" in m.DEFAULTS["VOCAB_PRESETS"]["剪輯"], \
            "內建那份被改掉了,還原成內建會回不去"

        # 檔案壞掉:應該安靜地當作沒有,而不是讓 import 直接爆掉
        m = load("{ 這不是合法的 JSON")
        assert "Lumetri" in m.VOCAB_PRESETS["剪輯"], "檔案壞掉時應退回內建詞庫"
        print("  ✓ 個人詞庫:同名覆蓋、新名新增、內建保留、檔案壞掉不會爆")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        importlib.reload(cfg)      # 確保後面的測試拿到的是真正的設定


def test_tests_never_touch_personal_config():
    """測試不准動使用者的個人設定檔。

    settings_local.json(面板存的設定)與 vocab_local.json(自訂詞庫)
    是使用者的東西,測試跑一跑把它們改掉或弄壞是不能接受的——
    尤其測試可能被 Ctrl-C 或面板的停止鈕從中間砍斷,finally 不一定跑得到。
    這裡直接對整個 tests/ 資料夾做字串檢查,擋住「以後有人又這樣寫」。"""
    import re
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    personal = ("settings_local.json", "vocab_local.json", "presets_local.json")
    # 只揪「寫入」:讀取個人設定來確認測試前提是可以的
    write_hint = re.compile(r'open\([^)]*["\']w|shutil\.(copy|move)\(')
    offenders = []
    for fn in sorted(os.listdir(tests_dir)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(tests_dir, fn), encoding="utf-8") as f:
            src = f.read()
        for line_no, line in enumerate(src.splitlines(), 1):
            if any(p in line for p in personal) and write_hint.search(line):
                offenders.append(f"{fn}:{line_no}")
        # 組出路徑再寫入的寫法(變數繞一手),用「真的 config 資料夾」當線索
        if 'real_cfg_dir' not in src:
            for p in personal:
                if f'"config", "{p}"' in src or f"'config', '{p}'" in src:
                    offenders.append(f"{fn}(組出個人設定檔路徑)")
    assert not offenders, \
        "測試不該寫入使用者的個人設定檔,請改用暫存資料夾:" + "、".join(offenders)
    print("  ✓ 沒有任何測試會寫到你的個人設定檔")


def test_every_setting_is_reachable_or_explained():
    """每一個設定都必須「面板調得到」或「寫明為什麼不放進面板」。

    這個專案的設定加在 config/settings.py 太容易,忘了回來加進 FIELDS 也
    太容易——結果是「程式在用、但面板上找不到」,使用者只能去手改
    settings_local.json,而且從畫面上完全看不出來有這個東西存在。
    曾經一次累積到十個才被發現(其中 WHISPER_DEVICE 最諷刺:面板自己的
    錯誤說明就在教使用者去手改那個 JSON)。

    這種漏掉沒有任何症狀,只能靠測試守。三條路選一條:
      放進 FIELDS               -> 面板長出控制項
      放進 PANEL_EXTRA_KEYS     -> 不做控制項,但面板讀得到(給程式邏輯用)
      放進 PANEL_OMITTED_KEYS   -> 刻意不放,而且要寫理由
    """
    from ui_settings import FIELDS, PANEL_EXTRA_KEYS, PANEL_OMITTED_KEYS
    covered = {f["key"] for f in FIELDS} | set(PANEL_EXTRA_KEYS)
    unexplained = sorted(k for k in cfg.DEFAULTS
                         if k not in covered and k not in PANEL_OMITTED_KEYS)
    assert not unexplained, (
        "這些設定程式在用、面板卻碰不到,而且沒說明為什麼:"
        + "、".join(unexplained)
        + "。請加進 ui_settings.py 的 FIELDS,或加進 PANEL_OMITTED_KEYS 並寫理由。")

    # 反過來:面板讀得到、但設定裡根本沒這個東西(改名或刪掉時最容易發生)
    ghosts = sorted(k for k in covered if not hasattr(cfg, k))
    assert not ghosts, f"面板指向不存在的設定:{'、'.join(ghosts)}"

    # 每個「刻意不放」都要有像樣的理由,不能寫「無」敷衍過去
    lazy = sorted(k for k, why in PANEL_OMITTED_KEYS.items() if len(why) < 8)
    assert not lazy, f"這些「刻意不放進面板」的理由太敷衍:{'、'.join(lazy)}"

    # 表本身也不能爛掉:列了理由卻早就不存在的設定要清掉
    stale = sorted(k for k in PANEL_OMITTED_KEYS if not hasattr(cfg, k))
    assert not stale, f"PANEL_OMITTED_KEYS 裡有已經不存在的設定:{'、'.join(stale)}"

    print(f"  ✓ {len(cfg.DEFAULTS)} 個設定:面板可調 {len(covered)}、"
          f"刻意不放 {len(PANEL_OMITTED_KEYS)}、沒說明的 0")


def test_panel_dump_is_valid():
    """ui_settings.py dump 要吐得出面板吃得下的 JSON。

    面板整個設定表單都靠這份 JSON 長出來,它壞掉的話面板只會顯示
    「設定格式解析失敗」六個字,查不出是哪個欄位害的。"""
    import json, subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "ui_settings.py", "dump"],
                       cwd=root, capture_output=True)
    assert r.returncode == 0, \
        f"dump 失敗:{r.stderr.decode('utf-8', 'replace')[:500]}"
    data = json.loads(r.stdout.decode("utf-8"))
    for key in ("fields", "values", "defaults", "presets", "preset_keys",
                "vocab_presets", "builtin_vocab", "vocab_budget"):
        assert key in data, f"dump 少了「{key}」,面板會拿不到東西"
    # 每個欄位都要有面板畫得出來的型別,而且值要真的存在
    known = {"select", "number", "bool", "list", "category", "vstlist", "combo"}
    for f in data["fields"]:
        assert f["type"] in known, f"欄位「{f['key']}」的型別 {f['type']} 面板不認得"
        assert f["key"] in data["values"], f"欄位「{f['key']}」沒有對應的值"
        if f["type"] == "select":
            v = data["values"][f["key"]]
            assert v in f["options"], \
                f"「{f['key']}」目前的值 {v!r} 不在選項 {f['options']} 裡,下拉會空白"
    print(f"  ✓ 面板設定 JSON 正常({len(data['fields'])} 個欄位)")


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
    test_tests_never_touch_personal_config()
    test_every_setting_is_reachable_or_explained()
    test_panel_dump_is_valid()
    test_voicefx_detection()
