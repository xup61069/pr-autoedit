"""「以目前序列生成辨識字幕」的測試。執行:python -m tests.test_seq_asr

守的是這個功能的命脈:**對齊**。

字幕對不對,九成取決於「重建出來的口白音訊,有沒有落在時間軸該在的位置」。
辨識準不準是 Whisper 的事,我們管不著;但只要重建的時間軸歪掉一點,
後面**整片字幕**就全偏了 —— 而且產出看起來很正常,不會有任何錯誤訊息。
所以這裡不跑 Whisper(慢、且結果會浮動),改用「在已知時間放已知的聲音」,
重建完再檢查聲音有沒有出現在正確的位置。

涵蓋的情境都是真的會發生的:片段被重新排序、中間有空隙、素材檔不見、
以及 Premiere 對變速片段回報錯誤來源時間點(見 seq_layout)。
"""

from __future__ import annotations
import sys, os, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
import soundfile as sf

from modules.seq_asr import build_sequence_audio, ASR_SR
from modules.seq_layout import speed_is_trustworthy, load_clips

SR = ASR_SR


def _make_source(path: str, tones: list[tuple[float, float]],
                 total_sec: float = 10.0) -> None:
    """做一支測試用音檔:在指定的時間區段放 440Hz 的聲音,其餘全靜音。"""
    n = int(total_sec * SR)
    audio = np.zeros(n, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / SR
    for a, b in tones:
        i, j = int(a * SR), int(b * SR)
        audio[i:j] = 0.5 * np.sin(2 * np.pi * 440.0 * t[i:j])
    sf.write(path, audio, SR)


def _loud_at(audio: np.ndarray, sec: float, win: float = 0.2) -> float:
    """某個時間點附近的音量(RMS)。用來問「這裡到底有沒有聲音」。"""
    i = int(sec * SR)
    j = min(len(audio), i + int(win * SR))
    if j <= i:
        return 0.0
    return float(np.sqrt(np.mean(audio[i:j].astype(np.float64) ** 2)))


def _build(clips: list[dict], work: str) -> np.ndarray:
    layout = os.path.join(work, "layout.json")
    with open(layout, "w", encoding="utf-8") as f:
        json.dump({"fps": 30.0, "clips": clips}, f)
    parsed, _ = load_clips(layout)
    wav, _total = build_sequence_audio(parsed, work)
    audio, sr = sf.read(wav, dtype="float32")
    assert sr == SR, sr
    return np.asarray(audio)


def test_reordered_clips_land_on_timeline_positions():
    """片段被重新排序 + 中間有空隙時,聲音要落在「時間軸」的位置。

    這是整個功能的核心:重建出來的音訊本身就是時間軸,辨識出的時間戳
    才能直接拿來當字幕時間。搬錯位置的話,字幕會對到別句話上。
    """
    work = tempfile.mkdtemp(prefix="seqasr_")
    try:
        src = os.path.join(work, "src.wav")
        # 來源:1~2 秒有聲音(A),5~6 秒有聲音(B),其餘靜音
        _make_source(src, [(1.0, 2.0), (5.0, 6.0)])

        # 序列:把 B 擺到最前面,A 擺到第 2 秒,中間留一秒空隙
        audio = _build([
            {"start": 0.0, "end": 1.0, "in": 5.0, "out": 6.0, "speed": 1.0,
             "path": src},
            {"start": 2.0, "end": 3.0, "in": 1.0, "out": 2.0, "speed": 1.0,
             "path": src},
        ], work)

        assert _loud_at(audio, 0.3) > 0.1, "時間軸 0 秒該有聲音(來源的 B 段)"
        assert _loud_at(audio, 1.3) < 0.01, "時間軸 1~2 秒是空隙,必須是靜音"
        assert _loud_at(audio, 2.3) > 0.1, "時間軸 2 秒該有聲音(來源的 A 段)"
        print("  ✓ 重新排序、有空隙時,聲音都落在時間軸正確的位置")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_untrusted_speed_clip_becomes_silence_without_shifting_others():
    """Premiere 回報錯誤來源時間的變速片段 -> 補等長靜音,不可推移後面的片段。

    這是最陰險的一種壞法:如果那段直接「跳過不放」,後面所有片段就會
    整體往前挪,字幕從那一刻起全部提早 —— 而且愈到後面差愈多。
    """
    work = tempfile.mkdtemp(prefix="seqasr_")
    try:
        src = os.path.join(work, "src.wav")
        _make_source(src, [(1.0, 2.0), (5.0, 6.0)])

        # 中間那個是「Premiere 沒換算」的變速片段:speed=6 但來源長度回報成
        # 跟時間軸長度一樣(1 秒),正確的話應該是 6 秒 -> 判定不可信
        audio = _build([
            {"start": 0.0, "end": 1.0, "in": 5.0, "out": 6.0, "speed": 1.0,
             "path": src},
            {"start": 1.0, "end": 2.0, "in": 0.0, "out": 1.0, "speed": 6.0,
             "path": src},
            {"start": 2.0, "end": 3.0, "in": 1.0, "out": 2.0, "speed": 1.0,
             "path": src},
        ], work)

        assert _loud_at(audio, 0.3) > 0.1, "第一段的聲音還在"
        assert _loud_at(audio, 1.3) < 0.01, "不可信的變速片段要變成靜音"
        # 關鍵:第三段仍然在第 2 秒,沒有因為中間變靜音就被往前拉
        assert _loud_at(audio, 2.3) > 0.1, "後面的片段沒有被推移(仍在第 2 秒)"
        print("  ✓ 不可信的變速片段補靜音,後面的片段位置不受影響")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_missing_source_file_does_not_break_alignment():
    """素材檔不見時,那段當成沒聲音,但時間軸長度要守住。"""
    work = tempfile.mkdtemp(prefix="seqasr_")
    try:
        src = os.path.join(work, "src.wav")
        _make_source(src, [(1.0, 2.0)])
        gone = os.path.join(work, "不存在的素材.mp4")

        audio = _build([
            {"start": 0.0, "end": 1.0, "in": 0.0, "out": 1.0, "speed": 1.0,
             "path": gone},
            {"start": 1.0, "end": 2.0, "in": 1.0, "out": 2.0, "speed": 1.0,
             "path": src},
        ], work)

        assert _loud_at(audio, 0.3) < 0.01, "找不到的素材當成沒聲音"
        assert _loud_at(audio, 1.3) > 0.1, "找得到的那段仍在正確位置"
        print("  ✓ 素材檔不見時不會壞掉,也不會推移其他片段")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_speed_clip_is_compressed_to_timeline_length():
    """來源時間點可信的變速片段,要壓成時間軸該有的長度(不可溢出)。"""
    work = tempfile.mkdtemp(prefix="seqasr_")
    try:
        src = os.path.join(work, "src.wav")
        _make_source(src, [(0.0, 4.0)])       # 前 4 秒都有聲音

        # 4 秒的來源用 4 倍速放,時間軸上只佔 1 秒(來源長度 == 時間軸 × 倍率,
        # 所以這是「可信」的那種)
        audio = _build([
            {"start": 0.0, "end": 1.0, "in": 0.0, "out": 4.0, "speed": 4.0,
             "path": src},
            {"start": 2.0, "end": 3.0, "in": 0.0, "out": 1.0, "speed": 1.0,
             "path": src},
        ], work)

        assert _loud_at(audio, 0.3) > 0.1, "加速後的那一秒有聲音"
        assert _loud_at(audio, 1.3) < 0.01, \
            "加速的聲音不可以溢出到時間軸第 1 秒之後(會蓋掉後面的內容)"
        assert _loud_at(audio, 2.3) > 0.1, "後面的片段不受影響"
        print("  ✓ 可信的變速片段被壓成時間軸長度,不會溢出")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_trust_rule_is_shared_with_live_subs():
    """兩個功能必須用同一份「可不可信」的判斷。

    各寫一套的話,同一條序列會產出兩份彼此對不起來的字幕,
    而且沒有任何錯誤訊息 —— 使用者只會覺得「這兩顆按鈕結果怎麼不一樣」。
    """
    import inspect
    from modules import live_subs, seq_asr
    for mod in (live_subs, seq_asr):
        src = inspect.getsource(mod)
        assert "speed_is_trustworthy" in src, \
            f"{mod.__name__} 沒有用共用的判斷,可能又自己寫了一套"

    # 判斷本身:沒換算的要擋掉,有換算的要放行,一般片段一律放行
    assert speed_is_trustworthy(0.0, 1.0, 0.0, 1.0, 1.0), "一般片段要可信"
    assert not speed_is_trustworthy(0.0, 1.0, 0.0, 1.0, 6.0), \
        "來源長度 == 時間軸長度(沒換算)-> 不可信"
    assert speed_is_trustworthy(0.0, 6.0, 0.0, 1.0, 6.0), \
        "來源長度 == 時間軸 × 倍率(有換算)-> 可信"
    print("  ✓ 兩個功能共用同一份變速可信度判斷")


def test_layout_without_paths_says_so_clearly():
    """舊版面板產的 layout 沒有來源檔路徑 —— 要講清楚,不能丟看不懂的例外。"""
    work = tempfile.mkdtemp(prefix="seqasr_")
    try:
        layout = os.path.join(work, "layout.json")
        with open(layout, "w", encoding="utf-8") as f:
            json.dump({"clips": [
                {"start": 0.0, "end": 1.0, "in": 0.0, "out": 1.0, "speed": 1.0}
            ]}, f)
        clips, _ = load_clips(layout)
        try:
            build_sequence_audio(clips, work)
            assert False, "沒有來源檔路徑時應該要停下來"
        except SystemExit as e:
            assert "路徑" in str(e), str(e)
            assert "更新" in str(e), "要告訴使用者怎麼辦(更新面板)"
        print("  ✓ 舊版 layout(沒有來源檔路徑)會講人話,不是丟例外")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print("執行「依序列重新辨識」測試...")
    test_reordered_clips_land_on_timeline_positions()
    test_untrusted_speed_clip_becomes_silence_without_shifting_others()
    test_missing_source_file_does_not_break_alignment()
    test_speed_clip_is_compressed_to_timeline_length()
    test_trust_rule_is_shared_with_live_subs()
    test_layout_without_paths_says_so_clearly()
    print("\n全部通過 ✓  重建的口白音訊對得準時間軸。")
