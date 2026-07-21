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
    # 面板實際畫得出來的型別。刻意不含「可打字下拉」——它得靠 <datalist>,
    # 那個元素在 CEP 的舊瀏覽器核心不可靠,下拉會是空的而且看不出原因。
    known = {"select", "number", "bool", "list", "category", "vstlist"}
    for f in data["fields"]:
        assert f["type"] in known, f"欄位「{f['key']}」的型別 {f['type']} 面板不認得"
        assert f["key"] in data["values"], f"欄位「{f['key']}」沒有對應的值"
        if f["type"] == "select":
            v = data["values"][f["key"]]
            assert v in f["options"], \
                f"「{f['key']}」目前的值 {v!r} 不在選項 {f['options']} 裡,下拉會空白"
    print(f"  ✓ 面板設定 JSON 正常({len(data['fields'])} 個欄位)")


def test_progress_lines_are_throttled_and_parsable():
    """進度行的格式與節流。

    格式要跟面板認得的一致(tests/test_panel_progress.js 會拿 Python 真的
    印出來的行去餵面板),而節流是必要的:不節流的話,轉錄一支長片會吐出
    好幾千行進度,把真正該看的訊息整個淹掉。"""
    import io, contextlib, re
    from modules.progress import Reporter, PREFIX

    # 節流:同一個百分比不重複印
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = Reporter("測試步驟", 100.0, unit="分", scale=1 / 60)
        for i in range(500):          # 連續灌 500 次
            r.update(i / 10.0)
        r.done()
    lines = [l for l in buf.getvalue().splitlines() if PREFIX in l]
    assert len(lines) < 60, f"沒有節流,印了 {len(lines)} 行"
    assert lines, "完全沒有印出進度"

    # 格式:面板的正規表示式要吃得下
    pat = re.compile(r"^\s*\[進度\]\s+(.+?)\s+(\d+)%\s*(.*)$")
    for line in lines:
        m = pat.match(line)
        assert m, f"面板解析不了這一行:{line!r}"
        assert m.group(1) == "測試步驟", m.group(1)
        assert 0 <= int(m.group(2)) <= 100, m.group(2)
    assert pat.match(lines[-1]).group(2) == "100", \
        f"最後一行應該是 100%,實際 {lines[-1]!r}"

    # 百分比必須遞增,不能跳來跳去
    pcts = [int(pat.match(l).group(2)) for l in lines]
    assert pcts == sorted(pcts), f"百分比不是遞增的:{pcts}"

    # 算不出總長度時要安靜(不能印出 0% 或除以零)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        r2 = Reporter("未知長度", 0.0)
        r2.update(5.0)
        r2.done()
    assert PREFIX not in buf2.getvalue(), \
        "算不出總長度時不該印進度(會變成假的 0%)"
    print(f"  ✓ 進度行格式正確且有節流(500 次更新 -> {len(lines)} 行)")


def test_merge_sources():
    """多檔合併:順序、命名、指紋、相容性檢查。

    這些都是「錯了要等到匯進 Premiere 才發現」的東西——接錯順序會得到一支
    前後顛倒的片,命名對不上會讓面板說「找不到報告」,指紋不對會沿用
    上一批影片算出來的畫面判定。"""
    import tempfile, shutil
    from modules import sources

    d = tempfile.mkdtemp(prefix="merge_test_")
    try:
        made = []
        for n in ("part1.mp4", "part2.mp4", "part10.mp4"):
            p = os.path.join(d, n)
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            made.append(p)

        # 自然排序:part2 要排在 part10 前面(字串排序會反過來)
        src = sources.from_args([made[2], made[0], made[1]])
        assert [os.path.basename(p) for p in src.paths] == \
            ["part1.mp4", "part2.mp4", "part10.mp4"], \
            f"排序不對:{[os.path.basename(p) for p in src.paths]}"

        # 命名:面板要靠這個名字去找產物,規則不能悄悄改
        assert src.name == "part1_合併3支", src.name
        assert sources.VideoSource([made[0]]).name == "part1", "單檔不該加後綴"

        # 指紋:少選一個檔、或某個檔內容變了,都要算出不同的指紋
        fp_all = src.fingerprint()
        assert sources.VideoSource(made[:2]).fingerprint() != fp_all, \
            "少一個檔卻算出相同的指紋 -> 會沿用上一批的畫面判定"
        with open(made[0], "wb") as f:
            f.write(b"y" * 200)
        assert src.fingerprint() != fp_all, "檔案內容變了,指紋卻沒變"

        # 單檔不產生 concat 清單,多檔才產生
        one = sources.VideoSource([made[0]], list_dir=d)
        assert one.input_args() == ["-i", made[0]], one.input_args()
        assert not one.multi
        args = sources.VideoSource(made, list_dir=d).input_args()
        assert args[:4] == ["-f", "concat", "-safe", "0"], args
        listed = open(args[-1], encoding="utf-8").read().strip().splitlines()
        assert len(listed) == 3 and all(l.startswith("file '") for l in listed), \
            f"concat 清單格式不對:{listed}"
        # 清單裡的順序就是接合的順序
        assert listed[0].endswith("part1.mp4'") and listed[2].endswith("part10.mp4'")
        print("  ✓ 多檔合併:排序、命名、指紋、concat 清單都正確")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_source_accepts_a_plain_path():
    """吃影片來源的函式,也要收得下「一個路徑字串」。

    這些函式的參數從路徑字串改成 VideoSource 之後,只要有任何一個呼叫端
    沒跟著改(或程式跑到一半被更新、新舊模組兜在一起),那個物件就會一路
    被塞進 ffmpeg 的參數清單,最後在 subprocess 深處炸出
        TypeError: expected str, bytes or os.PathLike object, not VideoSource
    ——對使用者完全沒有意義,而且那時候語音辨識已經跑完好幾分鐘了。
    實際發生過。兩邊都收就沒有這個破口。"""
    import inspect
    from modules import sources, audio_clean, video_probe

    one = sources.coerce("C:\\v\\a.mp4")
    assert one.paths == ["C:\\v\\a.mp4"] and not one.multi
    assert one.input_args() == ["-i", "C:\\v\\a.mp4"]

    many = sources.coerce(["C:\\v\\a.mp4", "C:\\v\\b.mp4"])
    assert many.multi and len(many.paths) == 2

    # 本來就是 VideoSource 的話原樣回傳(不要多包一層)
    vs = sources.VideoSource(["C:\\v\\a.mp4"])
    assert sources.coerce(vs) is vs

    # 真的傳了看不懂的東西,要在這裡當場講清楚,不是等 subprocess 才爆
    try:
        sources.coerce(12345)
    except TypeError as e:
        assert "VideoSource" in str(e) and "int" in str(e), str(e)
    else:
        raise AssertionError("傳數字進去居然沒有報錯")

    # 每個吃來源的入口都要先 coerce 一次
    for fn in (audio_clean.extract_audio, audio_clean.mux_back,
               audio_clean.clean_audio, video_probe._sample_frames,
               video_probe.frame_diffs):
        src_txt = inspect.getsource(fn)
        assert "coerce(source)" in src_txt, \
            f"{fn.__name__} 沒有先 coerce,傳到路徑字串就會在 ffmpeg 深處爆掉"
    print("  ✓ 影片來源給路徑字串或 VideoSource 都收得下")


def test_merge_rejects_mismatched_specs():
    """規格不一樣的檔要當場擋下來,而且要說清楚哪裡不一樣。

    concat 是「不重新編碼」的接合,規格不同時 ffmpeg 不會報錯,而是產生
    前半段正常、後半段畫面錯亂的檔——要等到剪完進 Premiere 才發現。
    這種「安靜地產出壞東西」正是這個專案最不能接受的失敗方式。"""
    from modules import sources

    class FakeSource(sources.VideoSource):
        def __init__(self, paths, infos):
            super().__init__(paths)
            self._infos = infos

    same = {"width": "3840", "height": "2160",
            "r_frame_rate": "30/1", "codec_name": "hevc", "duration": 60.0}
    diff = dict(same, width="1920", height="1080")

    ok_src = FakeSource(["a.mp4", "b.mp4"], [same, dict(same)])
    assert ok_src.incompatibility() is None, "規格相同卻被擋下來"

    bad = FakeSource(["a.mp4", "b.mp4"], [same, diff]).incompatibility()
    assert bad, "規格不同卻沒被擋下來"
    for expect in ("3840x2160", "1920x1080", "a.mp4", "b.mp4"):
        assert expect in bad, f"錯誤訊息沒說出「{expect}」:{bad}"
    assert "解法" in bad, "錯誤訊息要告訴使用者下一步做什麼"

    # 單檔永遠不用檢查
    assert FakeSource(["a.mp4"], [same]).incompatibility() is None
    print("  ✓ 規格不一致會擋下來,並指出是哪兩個檔、差在哪")


def test_report_stays_usable_on_a_long_video():
    """長片的報告要「生得快」也要「開得動」。

    報告的用途是一分鐘掃過去看有沒有大面積誤判。剪得兇的長片有兩三千個
    切點,整份印出來的 HTML 會大到瀏覽器捲起來都頓,而沒有人會逐列看完
    三千列——真正要看的只有低信心那幾十列。

    另外前後文查找以前是「每個切點掃過全部的詞」(切點數 × 詞數),
    2500 × 12000 實測要 5.2 秒才生得出一份報告。

    ⚠️ 收斂的是「表格」不是「統計」:上面那排數字仍要照全部切點算,
    少算了會讓人誤判這支片被剪掉多少。"""
    import time, tempfile
    from core.models import Timeline, Segment
    from core.remap import RemapTable
    from modules.report import generate, MAX_CUT_ROWS

    segs, pos = [], 0
    for i in range(6000):
        segs.append(Segment(pos, pos + 15, "keep" if i % 2 else "delete",
                            reason="silence" if i % 2 == 0 else "",
                            confidence=0.95 if i % 4 else 0.6))
        pos += 15
    words = [Word("字", i * 0.17, i * 0.17 + 0.15) for i in range(12000)]
    tl = Timeline(fps=30.0, source="/fake/x.mp4", segments=segs)
    table = RemapTable(segs, 30.0)
    out = os.path.join(tempfile.gettempdir(), "long_report_test.html")

    t0 = time.time()
    generate(tl, words, table, out)
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"長片的報告生了 {elapsed:.1f} 秒,太久了"

    html = open(out, encoding="utf-8").read()
    n_rows = html.count("<tr>") - html.count("<th>")
    assert n_rows <= MAX_CUT_ROWS + 50, f"表格列數沒有收斂:{n_rows} 列"
    assert len(html) < 2_000_000, f"HTML {len(html)/1e6:.1f}MB,瀏覽器會頓"

    # 統計必須照「全部」切點算,不能被表格的收斂影響
    all_cuts = table.cuts_for_markers()
    n_review = sum(1 for c in all_cuts if c.confidence < cfg.MARKER_MAX_CONFIDENCE)
    assert f"共 {len(all_cuts)} 個切點" in html, "總數應該報全部切點"
    assert f"<b>{n_review}</b> 個標為「需審閱」" in html, \
        "需審閱的數量應該照全部切點算"
    assert "表格只列出" in html, "收斂表格時要告訴使用者為什麼只看到一部分"
    os.remove(out)
    print(f"  ✓ 長片報告 {elapsed:.2f} 秒生成、{n_rows} 列、"
          f"{len(html)/1e6:.1f}MB,統計仍照全部 {len(all_cuts)} 個切點算")


def test_short_report_is_not_truncated():
    """切點少的時候要完整列出,不能也被收斂掉"""
    import tempfile
    from core.models import Timeline, Segment
    from core.remap import RemapTable
    from modules.report import generate

    segs = [Segment(0, 100, "keep"),
            Segment(100, 110, "delete", reason="filler", text="嗯",
                    confidence=0.6),
            Segment(110, 300, "keep")]
    table = RemapTable(segs, 30.0)
    out = os.path.join(tempfile.gettempdir(), "short_report_test.html")
    generate(Timeline(fps=30.0, source="/f.mp4", segments=segs),
             [Word("大家好", 0, 1), Word("嗯", 3.3, 3.6), Word("今天", 3.7, 4.5)],
             table, out)
    html = open(out, encoding="utf-8").read()
    assert "表格只列出" not in html, "切點很少時不該出現截斷說明"
    assert "嗯" in html, "小份報告要完整列出切點"
    os.remove(out)
    print("  ✓ 切點少的報告完整列出,不受收斂影響")


def test_docs_list_every_test_suite():
    """教人「改動前先跑測試」的文件,必須列滿全部九套。

    這個專案的 bug 有一整類是這樣活下來的:文件只列三到六套,
    漏掉的正好是 test_music(守著畫面判定與雜音剪除)。照文件跑 -> 全綠 ->
    交付,而 live 模式的標籤 bug 完全沒被碰到。文件漏列跟程式有 bug
    一樣危險,因為它決定了「你以為自己驗過了」。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tests_dir = os.path.join(root, "tests")
    py = sorted(f[:-3] for f in os.listdir(tests_dir)
                if f.startswith("test_") and f.endswith(".py"))
    js = sorted(f for f in os.listdir(tests_dir)
                if f.startswith("test_") and f.endswith(".js"))

    for doc in ("CONTRIBUTING.md", "AGENTS.md", "SETUP.md"):
        text = open(os.path.join(root, doc), encoding="utf-8").read()
        missing = [n for n in py if f"tests.{n}" not in text]
        missing += [n for n in js if n not in text]
        assert not missing, (
            f"{doc} 的測試清單漏了:{'、'.join(missing)}。"
            "照這份文件跑測試的人會以為自己驗過了,其實沒有。")

    # 別的地方報的套數也要對得上
    readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()
    assert f"{_cn_num(len(py))}套 Python" in readme, \
        f"README 說的 Python 測試套數跟實際({len(py)} 套)對不上"
    assert f"{_cn_num(len(js))}套面板" in readme, \
        f"README 說的面板測試套數跟實際({len(js)} 套)對不上"
    print(f"  ✓ 三份文件都列滿 {len(py)} 套 Python + {len(js)} 套面板測試")


def _cn_num(n: int) -> str:
    return "零一二三四五六七八九十"[n] if n <= 10 else str(n)


def test_docs_dont_reference_missing_files():
    """文件裡指到的專案檔案要真的存在。

    改檔名、搬資料夾之後,文件裡的路徑會靜靜地變成死連結。對零程式基礎的
    使用者來說,「照著做卻找不到那個檔」跟程式壞掉沒有兩樣。"""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = [f for f in os.listdir(root) if f.endswith(".md")]
    docs += [os.path.join("premiere-panel", "README.md")]

    # 專案裡實際存在的檔名(含不帶資料夾的簡稱:文件常寫「remap.py」
    # 而不是「core/remap.py」,那不算錯,讀的人找得到)
    present = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       (".git", "__pycache__", "output", "venv", ".venv", "dist")]
        rel = os.path.relpath(dirpath, root).replace("\\", "/").lstrip("./")
        for fn in filenames:
            present.add(fn)
            present.add(f"{rel}/{fn}" if rel not in (".", "") else fn)

    # 這些不是專案檔案,是「跑過之後才會產生的產物」或使用者的個人設定,
    # 版控裡本來就不會有,不能拿來當死連結
    def is_runtime_artifact(ref: str) -> bool:
        base = ref.rsplit("/", 1)[-1]
        return (base[:1].isdigit()                    # 04_report.html 之類的產物
                or ref.startswith(("output/", "_work/"))
                or "/output/" in ref or "/_work/" in ref
                or "_local." in base or base == "panel.json")

    pat = re.compile(
        r"`([\w\-./\\]+\.(?:py|js|jsx|json|md|html|css|bat|ps1|xml|reg|txt))`")
    bad = []
    for doc in docs:
        p = os.path.join(root, doc)
        if not os.path.exists(p):
            continue
        for m in pat.finditer(open(p, encoding="utf-8").read()):
            ref = m.group(1).replace("\\", "/").lstrip("./")
            if ref.startswith("http") or "*" in ref or is_runtime_artifact(ref):
                continue
            if ref in present or ref.rsplit("/", 1)[-1] in present:
                continue
            bad.append(f"{doc} -> {m.group(1)}")
    assert not bad, "文件指到不存在的檔案:" + "、".join(bad)
    print(f"  ✓ {len(docs)} 份文件裡提到的專案檔案都存在")


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
    test_progress_lines_are_throttled_and_parsable()
    test_merge_sources()
    test_source_accepts_a_plain_path()
    test_merge_rejects_mismatched_specs()
    test_report_stays_usable_on_a_long_video()
    test_short_report_is_not_truncated()
    test_docs_list_every_test_suite()
    test_docs_dont_reference_missing_files()
    test_voicefx_detection()
