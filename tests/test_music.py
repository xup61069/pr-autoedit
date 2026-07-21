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


def test_cough_is_cut_not_protected():
    """咳嗽 = 沒有詞、又短的聲響 -> 剪掉,不能當成示範音樂保護。

    為什麼會被誤保護:咳嗽幾乎都緊接在講完一句話之後(先講完才咳),
    離前面的說話不到 MERGE_GAP_SEC,能量偵測會把咳嗽跟那段講話縫成
    同一個有聲區間;縫完長度當然超過 MUSIC_MIN_SEC,於是咳嗽跟著被保護、
    不管停頓剪得多兇都剪不掉。所以判斷長度時要看「落在這個停頓裡的部分」,
    不能看能量偵測給的整個區間 —— 這個測試就是釘住這件事。"""
    words = [Word("講完一句", 0.0, 1.0), Word("繼續講", 9.0, 10.0)]
    fps = 30
    # 有聲區間 0~39 幀,含著講話(0~30)+ 緊接的咳嗽(30~39)。
    # 整個區間 1.3 秒 > MUSIC_MIN_SEC,但落在停頓裡的只有 9 幀 = 0.3 秒,
    # 短於 MUSIC_MIN_SEC(本檔頂端設 0.4)-> 判為雜音。
    segs = build_segments(words, fps, total_frames=300, audible=[(0, 39)])
    noise = [s for s in segs if s.reason == "noise"]
    assert len(noise) == 1, f"應該有一段被判為雜音,實際 {[s.reason for s in segs]}"
    assert noise[0].action == "delete", "雜音要剪掉,不是保留"
    assert not any(s.reason == "music" for s in segs), "不該被當成音樂保護"
    print("  ✓ 緊接在講話後的咳嗽被剪掉(不再誤判為示範音樂)")


def test_real_music_still_protected():
    """改成會剪雜音之後,夠長的示範音樂仍然要原封不動保住。

    這是上一個測試的反面。只驗「咳嗽剪掉了」而不驗這個,
    等於用破壞音樂保護的方式換到剪咳嗽——那是把核心功能弄壞了。"""
    words = [Word("聽這段", 0.0, 1.0), Word("如何", 12.0, 13.0)]
    fps = 30
    segs = build_segments(words, fps, total_frames=400, audible=[(60, 300)])
    music = [s for s in segs if s.reason == "music"]
    assert len(music) == 1 and music[0].action == "keep", "示範音樂被剪掉了"
    assert music[0].start == 60 and music[0].end == 300
    assert not any(s.reason == "noise" for s in segs)
    print("  ✓ 夠長的示範音樂仍然完整保護")


def test_noise_trim_off_restores_old_behaviour():
    """關掉「剪掉短促雜音」要回到舊行為(短聲響照樣當音樂保護)。
    使用者關掉一個開關,就該真的關掉,不能還留著一半。"""
    words = [Word("講完一句", 0.0, 1.0), Word("繼續講", 9.0, 10.0)]
    old = cfg.NOISE_TRIM
    cfg.NOISE_TRIM = False
    try:
        segs = build_segments(words, 30, total_frames=300, audible=[(0, 39)])
        assert any(s.reason == "music" and s.action == "keep" for s in segs)
        assert not any(s.reason == "noise" for s in segs)
    finally:
        cfg.NOISE_TRIM = old
    print("  ✓ 關掉雜音剪除後,回到「短聲響也當音樂保護」的舊行為")


def test_noise_keeps_full_coverage():
    """剪掉雜音之後,段落仍要首尾相連、完整覆蓋整支影片。
    這個專案的 bug 幾乎都是時間軸破洞,任何新增的段落型別都要驗這件事。"""
    words = [Word("測試", 0.0, 1.0), Word("結束", 14.0, 15.0)]
    fps = 30
    # 咳嗽(30~51)+ 音樂(180~330)混在同一個空隙裡
    segs = build_segments(words, fps, total_frames=450,
                          audible=[(0, 39), (180, 330)])
    assert segs[0].start == 0 and segs[-1].end == 450
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start, f"時間軸破洞:{a.end} != {b.start}"
    assert any(s.reason == "noise" for s in segs)
    assert any(s.reason == "music" for s in segs)
    print("  ✓ 咳嗽與音樂混在同一空隙時,覆蓋仍完整無斷裂")


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


def test_micro_trim_segments_can_be_rescued_by_motion():
    """微剪挖出來的停頓,夠長又剛好畫面在動時會被翻回快轉。

    這件事本身是對的(你講到一半停 0.6 秒去拉推桿,那半秒的畫面有內容),
    但它有兩個後果必須釘住:
      1. 短於 MOTION_MIN_SEC 的微剪段永遠維持刪除 —— 那是擋住
         「Premiere 產生大量細碎變速片段」這個效能地雷的閘門;
      2. 所以「能量微剪剪掉幾分鐘」這個數字,在畫面判定跑完之前是虛報的。
    trim_quiet_inside 的註解以前寫「一律刪除、不做快轉」,跟實際行為相反,
    害下一個看這段程式的人會相信錯的前提。"""
    from core.decision import trim_quiet_inside, apply_motion
    from core.models import Segment
    cfg.MOTION_MIN_SEC = 0.5
    cfg.SILENCE_SPEED_FACTOR = 6.0
    fps = 30.0
    # 一整段講話(0~300),中間挖出兩個安靜區:一個 0.9 秒、一個 0.3 秒
    segs = [Segment(0, 300, "keep")]
    segs = trim_quiet_inside(segs, [(60, 87), (150, 159)], fps)
    cut = [s for s in segs if s.action == "delete"]
    assert len(cut) == 2 and all(s.reason == "silence" for s in cut), \
        f"微剪這一步應該全部標成刪除,實際 {[(s.start, s.action) for s in segs]}"

    # 畫面全程在動
    out = apply_motion(segs, [(0, 300)], fps)
    long_seg = [s for s in out if s.start == 60][0]
    short_seg = [s for s in out if s.start == 150][0]
    assert long_seg.action == "speed" and long_seg.reason == "silence_motion", \
        "夠長又有畫面活動的微剪段應該被救回來變快轉"
    assert short_seg.action == "delete", \
        "短於 MOTION_MIN_SEC 的微剪段必須維持刪除(細碎變速是效能地雷)"
    print("  ✓ 微剪段:夠長的會被畫面判定救回快轉,太短的維持刪除")


def test_micro_trim_number_is_reported_after_motion():
    """「能量微剪剪掉幾分」要等畫面判定跑完才印,而且要說有多少被救回來。

    使用者是拿這個數字判斷「微剪值不值得開」的。在畫面判定之前印,
    報出來的比實際剪掉的多,等於給了他一個錯的依據。"""
    import pipeline, inspect
    src = inspect.getsource(pipeline.main)
    micro_at = src.index("micro_trimmed = before_keep")
    motion_at = src.index("segments = apply_motion")
    print_at = src.index("能量微剪:剪掉")
    assert micro_at < motion_at < print_at, \
        "微剪的統計必須在 apply_motion 之後才印,否則數字是虛報的"
    assert "kept_by_motion" in src, "要說明有多少微剪段被畫面判定救回快轉"
    print("  ✓ 微剪的省時數字在畫面判定之後才算,且會說明救回多少")


def test_motion_failure_and_emptiness_are_announced():
    """畫面偵測「失敗」與「一段活動都沒有」都必須講出來。

    這兩種情況下 apply_motion 根本不會被呼叫,所有停頓維持快轉 ——
    等於一秒都沒剪掉。使用者選的是「看畫面決定」,拿到的卻是「一律快轉」,
    而報告上只會顯示「畫面在動改加速 0 段」,分不出是真的沒活動、
    還是這一步壓根沒跑成功。安靜降級 = 使用者以為程式壞了。"""
    import pipeline, inspect
    src = inspect.getsource(pipeline.main)
    assert "motion_failed" in src, "畫面偵測失敗要能被辨識出來"
    assert "except Exception" in src, \
        "畫面偵測失敗不該讓整條管線爆掉(它只是加分項)"
    assert "elif cfg.SILENCE_ACTION == \"auto\" and not motion_failed" in src, \
        "掃描成功但零活動時,要另外告訴使用者這次沒有剪掉任何停頓"
    print("  ✓ 畫面偵測失敗或零活動時都會明說,不會安靜降級")


def test_motion_probe_error_is_readable():
    """ffmpeg 讀不了畫面時,錯誤訊息要帶著 ffmpeg 自己的說法。

    以前直接讓 CalledProcessError 冒出去,訊息只有「returned non-zero
    exit status 1」加一長串指令,真正的原因被 capture_output 吃在 stderr 裡。
    對零程式基礎的人那等於沒有訊息,面板的錯誤翻譯表也對不上任何一條。"""
    from modules.video_probe import _sample_frames
    try:
        _sample_frames(os.path.join(os.path.dirname(__file__),
                                    "_this_file_does_not_exist.mp4"), 4.0)
    except RuntimeError as e:
        msg = str(e)
        assert "ffmpeg" in msg, f"訊息沒提到是誰失敗的:{msg}"
        assert len(msg) > 30, f"訊息太空洞,等於沒說:{msg}"
        print("  ✓ 畫面偵測失敗時給得出看得懂的原因")
        return
    except Exception as e:
        raise AssertionError(f"應該丟 RuntimeError,實際丟 {type(e).__name__}")
    raise AssertionError("讀不存在的檔案居然沒有失敗")


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
    test_cough_is_cut_not_protected()
    test_real_music_still_protected()
    test_noise_trim_off_restores_old_behaviour()
    test_noise_keeps_full_coverage()
    test_motion_turns_silence_into_speed()
    test_motion_static_stays_deleted()
    test_speed_mode_never_deletes()
    test_auto_mode_only_scans_motion_when_needed()
    test_motion_ignores_short_segments()
    test_motion_never_touches_music()
    test_micro_trim_segments_can_be_rescued_by_motion()
    test_micro_trim_number_is_reported_after_motion()
    test_motion_failure_and_emptiness_are_announced()
    test_motion_probe_error_is_readable()
    test_motion_regions_from_diffs()
    print("\n全部通過 ✓  音樂保護、能量微剪、畫面活動判定皆正確。")
