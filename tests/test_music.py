"""音樂/音效保護測試。執行:python -m tests.test_music"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from core.models import Word
from core.decision import build_segments, _split_gap
from modules.audio_probe import audible_regions_from_array
import config.settings as cfg

# 鎖回預設參數,不受使用者 settings_local 覆寫影響(理由見 test_decision)
cfg.SILENCE_ACTION = "speed"
cfg.SILENCE_THRESHOLD_SEC = 1.2
cfg.SILENCE_PADDING_SEC = 0.15
cfg.MUSIC_DB_ABOVE_FLOOR = 12.0
cfg.MUSIC_MIN_SEC = 0.4


def test_music_gap_protected():
    """兩句話中間的長空隙,若有聲音(音樂)應保留而非快轉"""
    words = [
        Word("第一句", 0.0, 1.0),
        Word("第二句", 9.0, 10.0),     # 中間 8 秒空白
    ]
    fps = 30
    # 假設 3~7 秒(90~210 幀)偵測到音樂
    segs = build_segments(words, fps, total_frames=300, audible=[(90, 210)])
    music = [s for s in segs if s.reason == "music"]
    assert len(music) == 1
    assert music[0].action == "keep"
    assert music[0].start == 90 and music[0].end == 210
    # 音樂前後夠長的空隙仍要被處理(快轉或刪除)
    assert any(s.action in ("speed", "delete") for s in segs)
    print("  ✓ 空隙中的音樂段被保護,前後靜音照常處理")


def test_no_audible_same_as_before():
    """沒偵測到聲音時,行為應與原本完全相同(整段快轉)"""
    words = [Word("第一句", 0.0, 1.0), Word("第二句", 4.0, 5.0)]
    old = build_segments(words, fps=30, total_frames=180)
    new = build_segments(words, fps=30, total_frames=180, audible=[])
    assert [(s.start, s.end, s.action) for s in old] == \
           [(s.start, s.end, s.action) for s in new]
    print("  ✓ 無音樂時行為與原本一致")


def test_gap_fully_music():
    """整個空隙都是音樂 -> 完全不剪不快轉"""
    words = [Word("第一句", 0.0, 1.0), Word("第二句", 9.0, 10.0)]
    segs = build_segments(words, fps=30, total_frames=300,
                          audible=[(0, 300)])
    assert not any(s.action in ("speed", "delete") and s.reason == "silence"
                   for s in segs)
    print("  ✓ 整段音樂的空隙完全不被剪")


def test_coverage_with_music():
    """加入音樂切分後,段落仍須首尾相連、完整覆蓋"""
    words = [Word("第一句", 0.0, 1.0), Word("第二句", 9.0, 10.0)]
    total = 300
    segs = build_segments(words, fps=30, total_frames=total,
                          audible=[(90, 150), (180, 220)])
    assert segs[0].start == 0 and segs[-1].end == total
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start, f"段落有斷裂:{a.end} != {b.start}"
    print("  ✓ 音樂切分後段落仍完整覆蓋,無斷裂")


def test_split_gap_edges():
    """_split_gap:有聲區間超出空隙範圍時要正確裁切"""
    pieces = _split_gap(100, 200, [(50, 120), (180, 250)])
    assert pieces == [(100, 120, "music"), (120, 180, "silence"),
                      (180, 200, "music")]
    print("  ✓ _split_gap 邊界裁切正確")


def test_probe_detects_tone():
    """能量偵測:靜音+正弦波+靜音 -> 應抓到中間那段"""
    sr, fps = 48000, 30
    t = np.linspace(0, 1, sr, endpoint=False)
    tone = 0.3 * np.sin(2 * np.pi * 440 * t)
    quiet = np.random.randn(sr) * 1e-4          # 近乎無聲的底噪
    audio = np.concatenate([quiet, tone, quiet])  # 0~1 靜、1~2 響、2~3 靜
    regions = audible_regions_from_array(audio, sr, fps)
    assert len(regions) == 1
    a, b = regions[0]
    assert abs(a - 1 * fps) <= 3 and abs(b - 2 * fps) <= 3   # 誤差 <0.1 秒
    print("  ✓ 能量偵測正確抓到有聲區間")


def test_probe_short_blip_ignored():
    """能量偵測:太短的聲響(滑鼠喀一聲)不算音樂"""
    sr, fps = 48000, 30
    quiet = np.random.randn(sr) * 1e-4
    blip = 0.3 * np.sin(2 * np.pi * 440 *
                        np.linspace(0, 0.1, int(sr * 0.1), endpoint=False))
    audio = np.concatenate([quiet, blip, quiet])
    regions = audible_regions_from_array(audio, sr, fps)
    assert regions == []
    print("  ✓ 過短的聲響被忽略")


def test_probe_constant_bgm_floor():
    """整片都有背景音樂時,底噪基準會自動抬高 -> 不會把所有停頓都當音樂
    (否則靜音剪輯功能會整個失效)"""
    sr, fps = 48000, 30
    t = np.linspace(0, 3, sr * 3, endpoint=False)
    bgm = 0.05 * np.sin(2 * np.pi * 220 * t)     # 全程等音量的背景音樂
    regions = audible_regions_from_array(bgm, sr, fps)
    assert regions == []                          # 沒有比底噪更突出的段落
    print("  ✓ 全程等音量背景音不會癱瘓靜音剪輯(自適應底噪)")


if __name__ == "__main__":
    print("執行音樂/音效保護測試...")
    test_music_gap_protected()
    test_no_audible_same_as_before()
    test_gap_fully_music()
    test_coverage_with_music()
    test_split_gap_edges()
    test_probe_detects_tone()
    test_probe_short_blip_ignored()
    test_probe_constant_bgm_floor()
    print("\n全部通過 ✓  音樂/音效保護邏輯正確。")
