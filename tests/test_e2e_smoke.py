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


if __name__ == "__main__":
    main()
    test_prompt_always_demonstrates_punctuation()
