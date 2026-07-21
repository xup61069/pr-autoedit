"""音樂/音效保護測試。執行:python -m tests.test_music"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from core.models import Word
from core.decision import build_segments, _split_gap
from modules.audio_probe import (audible_regions_from_array,
                                 quiet_regions_from_array)
import config.settings as cfg

# 鎖回預設參數,不受使用者 settings_local 覆寫影響(理由見 test_decision)
cfg.SILENCE_ACTION = "speed"
cfg.SILENCE_THRESHOLD_SEC = 1.2
cfg.SILENCE_PADDING_SEC = 0.15
cfg.MUSIC_DB_ABOVE_FLOOR = 12.0
cfg.MUSIC_MIN_SEC = 0.4
cfg.MICRO_TRIM_MIN_SEC = 0.25
cfg.MICRO_TRIM_KEEP_SEC = 0.06
cfg.MICRO_TRIM_DB_BELOW_SPEECH = 22.0


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


def test_quiet_detects_gap_between_speech():
    """能量微剪:講話—安靜—講話,中間那段安靜要被抓出來"""
    sr, fps = 48000, 30
    loud = 0.2 * np.sin(2 * np.pi * 300 * np.linspace(0, 1, sr, endpoint=False))
    silence = np.zeros(sr)                        # 中間 1 秒全靜音
    audio = np.concatenate([loud, silence, loud])
    regions = quiet_regions_from_array(audio, sr, fps)
    assert len(regions) == 1, f"應抓到 1 段安靜,實際 {regions}"
    a, b = regions[0]
    # 安靜區是第 1~2 秒(幀 30~60),頭尾各留 MICRO_TRIM_KEEP_SEC 不剪
    assert 30 < a < 36 and 54 < b < 60, f"安靜區位置不對:{regions}"
    print("  ✓ 講話中間的安靜段被抓出來,且頭尾有留緩衝")


def test_quiet_ignores_short_pause():
    """太短的停頓(自然語氣)不該被剪,免得講話被剁碎"""
    sr, fps = 48000, 30
    loud = 0.2 * np.sin(2 * np.pi * 300 * np.linspace(0, 1, sr, endpoint=False))
    audio = np.concatenate([loud, np.zeros(sr // 10), loud])   # 只停 0.1 秒
    assert quiet_regions_from_array(audio, sr, fps) == []
    print("  ✓ 0.1 秒的短停頓不剪(低於 MICRO_TRIM_MIN_SEC)")


# ---------------------------------------------------------------------------
# 畫面活動:沒講話時,靠畫面決定加速還是剪掉
# ---------------------------------------------------------------------------

def _sil(a, b, reason="silence"):
    from core.models import Segment
    return Segment(a, b, "delete", reason=reason, confidence=0.95)


def test_motion_turns_silence_into_speed():
    """沒講話但畫面在動 -> 改成加速(保住默默示範的內容)"""
    from core.decision import apply_motion
    cfg.MOTION_MIN_SEC = 0.5
    cfg.SILENCE_SPEED_FACTOR = 6.0
    segs = [_sil(0, 300)]                      # 10 秒的靜音段
    out = apply_motion(segs, [(100, 200)], 30.0)   # 中間有畫面活動
    assert len(out) == 1, "不該把段落切開(切開會讓片段數暴增)"
    assert out[0].action == "speed" and out[0].reason == "silence_motion"
    print("  ✓ 沒講話但畫面在動 -> 加速保留")


def test_motion_static_stays_deleted():
    """畫面靜止的靜音段 -> 剪掉"""
    from core.decision import apply_motion
    cfg.MOTION_MIN_SEC = 0.5
    out = apply_motion([_sil(0, 300)], [(1000, 1100)], 30.0)   # 活動不重疊
    assert out[0].action == "delete" and out[0].reason == "silence"
    print("  ✓ 畫面靜止 -> 剪掉")


def test_speed_mode_never_deletes():
    """選「一律快轉」時,沒有任何停頓可以被改成剪掉。

    這是以前真的壞掉的地方:畫面判定是獨立開關,不管使用者選什麼,
    畫面靜止的停頓都會被改寫成 delete —— 你設定「什麼都不刪、只壓縮」,
    結果 5 秒的停頓整段消失,而且審閱報告的 reason 一樣是 silence,
    看不出來是畫面判定幹的。現在三選一,speed 就真的只有 speed。

    註:這個測試刻意走完整的 build_segments,而不是直接呼叫 apply_motion
    —— 壞掉的是「兩個設定的交互作用」,只測單一函式永遠測不到。"""
    from core.models import Word
    from core.decision import build_segments
    old = cfg.SILENCE_ACTION, cfg.SILENCE_THRESHOLD_SEC, cfg.SILENCE_PADDING_SEC
    cfg.SILENCE_ACTION = "speed"
    cfg.SILENCE_THRESHOLD_SEC = 1.2
    cfg.SILENCE_PADDING_SEC = 0.15
    fps = 30.0
    # 講一句 -> 停 5 秒 -> 再講一句
    words = [Word("大家好", 0.0, 1.0), Word("接下來", 6.0, 7.0)]
    segs = build_segments(words, fps, int(8.0 * fps))
    cfg.SILENCE_ACTION, cfg.SILENCE_THRESHOLD_SEC, cfg.SILENCE_PADDING_SEC = old

    assert not any(s.action == "delete" and s.reason == "silence" for s in segs), \
        "選一律快轉時,不該有任何停頓被剪掉"
    assert any(s.action == "speed" for s in segs), "5 秒停頓應該要變成快轉"
    print("  ✓ 選「一律快轉」時,沒有停頓被偷偷剪掉")


def test_auto_mode_only_scans_motion_when_needed():
    """畫面掃描只在「看畫面決定」時才做 —— 選另外兩種還去掃整支影片
    是白白多花好幾十秒,而且掃了也用不到。"""
    import pipeline, inspect
    src = inspect.getsource(pipeline.main)
    assert 'cfg.SILENCE_ACTION == "auto"' in src, \
        "畫面掃描的開關應該綁在 SILENCE_ACTION == auto"
    assert "MOTION_DETECT" not in src, "不該再看已經廢掉的 MOTION_DETECT"
    print("  ✓ 只有選「看畫面決定」時才掃描畫面")


def test_motion_ignores_short_segments():
    """微剪挖出的零點幾秒小停頓不套用畫面判定 —— 轉成變速只會產生
    大量細碎的變速片段,是 Premiere 的效能地雷。"""
    from core.decision import apply_motion
    cfg.MOTION_MIN_SEC = 0.5
    short = _sil(0, 9)                          # 0.3 秒,短於門檻
    out = apply_motion([short], [(0, 9)], 30.0)
    assert out[0].action == "delete", "短段落應維持原判"
    print("  ✓ 太短的停頓不套用畫面判定")


def test_motion_never_touches_music():
    """音樂/音效段是刻意保護的 keep,畫面判定絕不能動到它"""
    from core.decision import apply_motion
    from core.models import Segment
    music = Segment(0, 300, "keep", reason="music", confidence=0.8)
    speech = Segment(300, 600, "keep")
    out = apply_motion([music, speech], [(0, 600)], 30.0)
    assert out[0].action == "keep" and out[0].reason == "music"
    assert out[1].action == "keep"
    print("  ✓ 音樂段與語音段不受影響")


def test_motion_regions_from_diffs():
    """變化量 -> 區間:超過門檻的連續時段才算,太短的丟掉"""
    from modules.video_probe import motion_regions_from_diffs
    cfg.MOTION_SENSITIVITY = 0.5
    cfg.MOTION_MIN_SEC = 0.5
    # 每秒 4 個取樣點:前 2 秒靜止、接著 2 秒在動
    diff = np.array([0.0] * 8 + [3.0] * 8)
    regions = motion_regions_from_diffs(diff, 4.0, 30.0)
    assert len(regions) == 1, f"應該只有一段活動,實際 {regions}"
    a, b = regions[0]
    assert abs(a / 30.0 - 2.0) < 0.2, f"起點應在 2 秒附近,實際 {a / 30.0}"
    print("  ✓ 畫面變化量正確轉成活動區間")


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
    test_quiet_detects_gap_between_speech()
    test_quiet_ignores_short_pause()
    test_motion_turns_silence_into_speed()
    test_motion_static_stays_deleted()
    test_speed_mode_never_deletes()
    test_auto_mode_only_scans_motion_when_needed()
    test_motion_ignores_short_segments()
    test_motion_never_touches_music()
    test_motion_regions_from_diffs()
    print("\n全部通過 ✓  音樂保護、能量微剪、畫面活動判定皆正確。")
