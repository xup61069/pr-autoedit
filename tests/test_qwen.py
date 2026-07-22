"""Qwen3-ASR 引擎的可測部分。執行:python -m tests.test_qwen

⚠️ 這裡「不」載入真的模型(開發環境沒裝)。真正會出錯的地方 —— 長片分段、
時間戳偏移、毫秒/簡繁換算 —— 都抽成純函式,這裡就是釘住那幾個。
「呼叫模型」那一段沒辦法在這裡跑到,靠使用者拿短片實測(見 transcribe 的說明)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Word
from modules.transcribe import (_qwen_chunk_plan, _qwen_words_from_stamps,
                                _qwen_language, _find_silences)
import config.settings as cfg


class _Stamp:
    """模仿 Qwen 回傳的時間戳物件(.text/.start_time/.end_time,毫秒)"""
    def __init__(self, text, start_ms, end_ms):
        self.text, self.start_time, self.end_time = text, start_ms, end_ms


def test_chunk_plan_covers_and_never_exceeds():
    """分段要:完整覆蓋 0~total、首尾相連、每段都不超過上限。

    超過上限那一段會被對齊模型拒絕或截斷,字幕就從那裡開始整段對不上 ——
    而且是長片才會踩到,最難事後查。"""
    total = 34 * 60.0                       # 34 分鐘,就是使用者的片長量級
    sils = [(t, t + 0.5) for t in range(50, int(total), 60)]   # 每分鐘一個靜音
    plan = _qwen_chunk_plan(total, sils, max_sec=240)

    assert plan[0][0] == 0.0, "沒有從頭開始"
    assert abs(plan[-1][1] - total) < 1e-6, "沒有覆蓋到結尾"
    for (a, b), (c, d) in zip(plan, plan[1:]):
        assert abs(b - c) < 1e-6, f"段落有斷裂:{b} != {c}"
    for a, b in plan:
        assert b > a, f"出現空段或倒退:{(a, b)}"
        assert b - a <= 240 + 1e-6, f"這一段 {b - a:.1f} 秒超過上限"
    print(f"  ✓ 34 分鐘切成 {len(plan)} 段,完整覆蓋、無斷裂、都不超過上限")


def test_chunk_plan_cuts_at_silence_when_possible():
    """有靜音就切在靜音中點,不要切到詞中間。"""
    # 200 秒處有一個明顯的靜音,上限 240 -> 應該切在 ~200 而不是硬切在 240
    plan = _qwen_chunk_plan(400.0, [(199.0, 201.0)], max_sec=240)
    assert abs(plan[0][1] - 200.0) < 1e-6, f"沒有切在靜音中點:{plan[0]}"
    print("  ✓ 有靜音時切在靜音中點")


def test_chunk_plan_hard_cuts_without_silence():
    """完全沒有靜音也不能無限迴圈,硬切在上限處。"""
    plan = _qwen_chunk_plan(600.0, [], max_sec=240)
    assert plan[0] == (0.0, 240.0) and plan[1][0] == 240.0
    assert abs(plan[-1][1] - 600.0) < 1e-6
    assert all(b - a <= 240 + 1e-6 for a, b in plan)
    print("  ✓ 沒有靜音時硬切在上限,不會卡死")


def test_short_audio_is_one_chunk():
    """短片(短於上限)就一整段,不要多切。"""
    assert _qwen_chunk_plan(120.0, [], max_sec=240) == [(0.0, 120.0)]
    print("  ✓ 短片維持單段")


def test_stamps_get_chunk_offset_and_ms_conversion():
    """把每段的時間戳接回整片:要加上這段的起點偏移,而且毫秒轉秒。

    忘了加偏移的話,第二段之後的字幕會全部跑回影片開頭,整份時間軸亂掉 ——
    這正是分段對齊最容易錯的地方。"""
    stamps = [_Stamp("你", 0, 300), _Stamp("好", 300, 800)]
    # 這一段從整片的第 240 秒開始
    words = _qwen_words_from_stamps(stamps, offset_sec=240.0, convert=lambda s: s)
    assert len(words) == 2
    assert abs(words[0].start - 240.0) < 1e-9      # 0ms + 240s
    assert abs(words[0].end - 240.3) < 1e-9        # 300ms -> 0.3s
    assert abs(words[1].start - 240.3) < 1e-9
    assert abs(words[1].end - 240.8) < 1e-9
    print("  ✓ 時間戳有加上分段偏移,且毫秒正確換算成秒")


def test_stamps_simplified_to_traditional():
    """中文簡轉繁,才對得上決策引擎的繁體冗詞清單與字幕。"""
    try:
        from opencc import OpenCC
        convert = OpenCC("s2tw").convert
    except ImportError:
        print("  (未裝 opencc,略過簡繁測試)")
        return
    stamps = [_Stamp("这个", 0, 500)]      # 简体
    words = _qwen_words_from_stamps(stamps, 0.0, convert)
    assert words[0].text == "這個", f"沒有轉成繁體:{words[0].text}"
    print("  ✓ 簡體輸出轉成繁體")


def test_stamps_skip_empty_and_malformed():
    """空字、缺時間的項目要跳過,不能產出壞的 Word。"""
    stamps = [_Stamp("", 0, 100), _Stamp("好", None, 200),
              {"text": "字", "start_time": 100, "end_time": 400}]   # 字典形式也接
    words = _qwen_words_from_stamps(stamps, 0.0, convert=lambda s: s)
    assert len(words) == 1 and words[0].text == "字"
    assert abs(words[0].start - 0.1) < 1e-9 and abs(words[0].end - 0.4) < 1e-9
    print("  ✓ 空字/缺時間的項目跳過,字典形式也讀得到")


def test_language_mapping():
    """WHISPER_LANGUAGE 的碼要正確翻成 Qwen 的語言名稱;auto -> None。"""
    old = getattr(cfg, "WHISPER_LANGUAGE", "zh")
    try:
        cfg.WHISPER_LANGUAGE = "zh"
        assert _qwen_language() == "Chinese"
        cfg.WHISPER_LANGUAGE = "en"
        assert _qwen_language() == "English"
        cfg.WHISPER_LANGUAGE = "auto"
        assert _qwen_language() is None
        cfg.WHISPER_LANGUAGE = "xyz"          # 認不得的交給自動偵測
        assert _qwen_language() is None
    finally:
        cfg.WHISPER_LANGUAGE = old
    print("  ✓ 語言碼正確對應到 Qwen 的語言名稱")


def test_find_silences_on_synthetic_audio():
    """靜音偵測:講話—靜音—講話,要抓到中間那段當切點候選。"""
    import numpy as np
    sr = 16000
    loud = 0.2 * np.sin(2 * np.pi * 200 * np.linspace(0, 2, sr * 2, endpoint=False))
    quiet = np.zeros(sr)                      # 中間 1 秒靜音
    audio = np.concatenate([loud, quiet, loud]).astype(np.float32)
    sils = _find_silences(audio, sr)
    # 靜音在 2~3 秒(中點 2.5);抓到「大致落在中間那段」即可
    assert any(a >= 1.8 and b <= 3.2 for a, b in sils), \
        f"沒抓到中間那段靜音:{sils}"
    print("  ✓ 靜音偵測抓到講話中間的空檔")


def test_fingerprint_distinguishes_qwen():
    """快取指紋:引擎切到 qwen 要跟 whisper 不同,否則會拿舊結果充數。

    這是踩過的坑(切了引擎但字幕沒變),新引擎一定要守。"""
    from modules.transcribe import _asr_fingerprint
    old = getattr(cfg, "ASR_ENGINE", "faster-whisper")
    try:
        cfg.ASR_ENGINE = "faster-whisper"
        fp_w = _asr_fingerprint()
        cfg.ASR_ENGINE = "qwen"
        fp_q = _asr_fingerprint()
        assert fp_w != fp_q, "whisper 與 qwen 的指紋一樣,切引擎不會重轉"
        assert fp_q.get("engine") == "qwen" and "aligner" in fp_q
    finally:
        cfg.ASR_ENGINE = old
    print("  ✓ qwen 的辨識指紋跟其他引擎不同,切了會自動重轉")


if __name__ == "__main__":
    print("執行 Qwen3-ASR 引擎測試...")
    test_chunk_plan_covers_and_never_exceeds()
    test_chunk_plan_cuts_at_silence_when_possible()
    test_chunk_plan_hard_cuts_without_silence()
    test_short_audio_is_one_chunk()
    test_stamps_get_chunk_offset_and_ms_conversion()
    test_stamps_simplified_to_traditional()
    test_stamps_skip_empty_and_malformed()
    test_language_mapping()
    test_find_silences_on_synthetic_audio()
    test_fingerprint_distinguishes_qwen()
    print("\n全部通過 ✓  Qwen3-ASR 的分段/偏移/換算邏輯正確"
          "(模型呼叫本身待實機驗證)。")
