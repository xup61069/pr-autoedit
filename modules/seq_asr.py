"""以「Premiere 目前序列」重新辨識,產生字幕 —— 任何序列都能用。

跟隔壁的 `live_subs` 差在哪(兩個都叫「依目前序列產字幕」,但骨子裡不同):

  live_subs  拿**當初處理這支片時**的轉錄快取,依序列版面重新對位。
             快(幾秒),但只能用在本工具處理過、而且素材沒換過的序列。
  seq_asr    對序列**現在實際的口白**重新做一次語音辨識。
             慢(要跑 Whisper),但**任何序列都能用** —— 手動剪的、
             以前的舊專案、別人給的素材,只要 V1 上的片段找得到來源檔就行。

做法上有一個很關鍵的性質:我們**照時間軸的順序**把口白重建成一條音訊
(片段之間的空隙補靜音),所以辨識出來的時間戳**本身就是時間軸時間**,
不需要再做任何對位 —— 也就整個繞開了「Premiere 對變速片段回報錯誤來源
時間點」那個雷(見 seq_layout)。字幕天生就對準你剪完當下的樣子。

只取 V1 視訊片段自帶的聲音(你的口白),不管別的音樂軌:
背景音樂與示範音效丟進辨識只會生出奇怪的字,對字幕有害無益。

用法(面板按鈕呼叫;也可手動):
    python -m modules.seq_asr <layout.json> <output資料夾>
輸出:<output資料夾>/05_subtitles_asr.srt
"""

from __future__ import annotations
import hashlib, os, subprocess, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np

from core.models import Segment
from core.remap import RemapTable
from modules.seq_layout import load_clips, speed_is_trustworthy
from modules.subtitles import write_srt
from modules.workspace import wpath
import config.settings as cfg

# 重建的音訊只拿去做語音辨識,不會給人聽,所以直接用 Whisper 內部的取樣率。
# 48k 存了也是白存:它進 Whisper 第一件事就是降到 16k,而記憶體要多吃三倍
# (一個半小時的片:48k 是 1.0GB,16k 只要 346MB)。
ASR_SR = 16000

# 沒有指定幀率時的退路。layout 會帶 fps 過來,這只是防舊版 layout。
_FALLBACK_FPS = 30.0


def _probe_duration(path: str) -> float:
    """影片長度(秒)。問不到就回 0(進度條會自己安靜)。"""
    try:
        out = subprocess.run([
            "ffprobe", "-v", "0", "-show_entries", "format=duration",
            "-of", "csv=p=0", path,
        ], capture_output=True, text=True).stdout.strip()
        return float(out)
    except (ValueError, OSError):
        return 0.0


def _file_md5(path: str, chunk: int = 1 << 20) -> str:
    """檔案內容的短雜湊(給快取檔名用)。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _audio_cache_path(work_dir: str, src: str) -> str:
    """某個來源檔抽出來的音軌要存哪。

    用「路徑雜湊」當檔名:序列裡可能混用好幾支不同資料夾的影片,
    照原檔名存會撞名(常常就是 0001.mp4、0002.mp4 這種)。
    """
    h = hashlib.md5(os.path.abspath(src).encode("utf-8")).hexdigest()[:12]
    return wpath(work_dir, f"05_src_{h}.wav")


def _ensure_dir(for_file: str) -> None:
    """確保這個檔案的資料夾存在。

    中繼檔是放在 _work/ 裡的,而這個功能可能在「從來沒被本工具處理過」的
    資料夾上執行 —— 那裡連 _work/ 都還沒有。少了這一步,ffmpeg 會因為
    寫不出檔案而失敗,錯誤訊息卻只說「ffmpeg 回非 0」,看不出是資料夾的事。
    """
    d = os.path.dirname(for_file)
    if d:
        os.makedirs(d, exist_ok=True)


def _extract_mono(src: str, out_wav: str) -> str:
    """把來源檔的聲音抽成 16k 單聲道 WAV(抽過就沿用)。"""
    if os.path.exists(out_wav) and os.path.getsize(out_wav) > 0:
        return out_wav
    _ensure_dir(out_wav)
    from modules.progress import run_ffmpeg
    run_ffmpeg([
        "ffmpeg", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", str(ASR_SR),
        "-c:a", "pcm_s16le", out_wav,
    ], "抽出音軌", _probe_duration(src))
    return out_wav


def _time_scale(seg: np.ndarray, speed: float) -> np.ndarray:
    """把一段聲音壓成「加速後」的長度。

    用線性內插重採樣 —— 會變調(花栗鼠音),但這裡的目的只是讓後面的
    時間對得準,而本工具加速的都是「沒在講話的停頓」,那裡沒有字可以辨識。
    (要不變調就得做 atempo,那得對每個片段各叫一次 ffmpeg;剪得兇的
     教學片一條序列有上千個片段,那樣會慢到不能用。)
    """
    n_out = max(1, int(round(len(seg) / speed)))
    if n_out == len(seg) or len(seg) < 2:
        return seg
    idx = np.linspace(0, len(seg) - 1, n_out)
    return np.interp(idx, np.arange(len(seg), dtype=np.float64),
                     seg.astype(np.float64)).astype(np.float32)


def build_sequence_audio(clips: list[dict], work_dir: str) -> tuple[str, float]:
    """照時間軸把 V1 片段的口白接成一條音訊。回傳(WAV 路徑, 總長秒數)。

    片段之間的空隙、以及「來源時間點不可信」的變速片段,一律補等長的靜音
    —— 對齊比內容重要:少了那段聲音頂多少幾個字,長度錯了會讓**後面
    整片字幕**都偏掉。
    """
    import soundfile as sf

    total_sec = max((c["end"] for c in clips), default=0.0)
    if total_sec <= 0:
        raise SystemExit("序列的長度是 0,沒有東西可以辨識。")

    # 先把用到的來源檔各抽一次音軌(同一支片在序列裡被切成上千段是常態,
    # 每段都去抽一次會慢到不能看)
    sources = []
    for c in clips:
        if c["path"] and c["path"] not in sources:
            sources.append(c["path"])
    if not sources:
        raise SystemExit(
            "序列版面裡沒有來源檔路徑,沒辦法重新辨識。\n"
            "  這通常代表面板是舊版的。請更新到最新版再試一次。")

    missing = [p for p in sources if not os.path.exists(p)]
    usable = [p for p in sources if os.path.exists(p)]
    if not usable:
        raise SystemExit(
            "序列裡的素材檔案一個都找不到,沒辦法重新辨識。\n"
            "  找不到的是:\n    " + "\n    ".join(missing[:5]) +
            "\n  素材被搬走或改名的話,先在 Premiere 重新連結素材再試。")
    if missing:
        print(f"  ⚠ 有 {len(missing)} 個素材檔找不到,那些片段會當成沒聲音:")
        for p in missing[:3]:
            print(f"      {os.path.basename(p)}")

    cache: dict[str, np.ndarray] = {}
    for p in usable:
        wav = _extract_mono(p, _audio_cache_path(work_dir, p))
        data, _sr = sf.read(wav, dtype="float32")
        cache[p] = np.asarray(data)

    out = np.zeros(int(round(total_sec * ASR_SR)) + 1, dtype=np.float32)
    n_silent = 0
    for c in clips:
        at = int(round(c["start"] * ASR_SR))
        tl_len = max(0, int(round((c["end"] - c["start"]) * ASR_SR)))
        if tl_len <= 0:
            continue
        audio = cache.get(c["path"])
        ok = audio is not None and speed_is_trustworthy(
            c["in"], c["out"], c["start"], c["end"], c["speed"])
        if not ok:
            n_silent += 1          # 留白:out 本來就是 0,不用做事
            continue
        a = max(0, int(round(c["in"] * ASR_SR)))
        b = min(len(audio), int(round(c["out"] * ASR_SR)))
        if b <= a:
            n_silent += 1
            continue
        seg = audio[a:b]
        if c["speed"] != 1.0:
            seg = _time_scale(seg, c["speed"])
        # 對齊優先:多的截掉、少的留白,絕不讓它推移後面的片段
        seg = seg[:tl_len]
        end = min(len(out), at + len(seg))
        if end > at:
            out[at:end] = seg[:end - at]

    if n_silent:
        print(f"  {n_silent} 個片段當成沒聲音處理(素材找不到、或 Premiere "
              f"回報的來源時間點對不上)。\n"
              f"    這些多半是被加速帶過的停頓,本來就沒有字。")

    out_wav = wpath(work_dir, "05_seq_audio.wav")
    _ensure_dir(out_wav)
    sf.write(out_wav, out, ASR_SR)
    print(f"  已依時間軸重建口白音訊:{total_sec / 60:.1f} 分")
    return out_wav, total_sec


def build_from_layout(layout_json: str, work_dir: str) -> str:
    """layout JSON -> 重新辨識 -> SRT。回傳輸出路徑。"""
    clips, layout = load_clips(layout_json)
    if not clips:
        raise SystemExit("序列版面是空的(時間軸上沒有片段),無法產字幕。")
    fps = float(layout.get("fps") or 0) or _FALLBACK_FPS

    from modules import progress
    progress.begin_run(["抽出音軌", "語音轉錄"])

    wav, total_sec = build_sequence_audio(clips, work_dir)

    print("  重新辨識目前序列的口白…")
    from modules.transcribe import transcribe
    # ⚠️ 轉錄快取只認「辨識設定」,不認音訊內容 —— 在主流程那沒問題(快取
    # 就住在那支影片自己的資料夾裡),但這個功能可能對很多條不同的序列跑,
    # 共用一個資料夾。不把內容綁進檔名的話,換一條序列會直接讀到上一條的
    # 轉錄結果,產出一份「完全對不上、卻不會報錯」的字幕。
    # 綁的是重建音訊的雜湊(那正是餵給辨識的東西),所以同一條序列重跑
    # 仍然吃得到快取:只想改字幕行長之類的設定時,幾秒就重生完。
    cache = wpath(work_dir, f"05_transcript_asr_{_file_md5(wav)}.json")
    words = transcribe(wav, cache_json=cache)
    if not words:
        raise SystemExit(
            "這條序列裡幾乎沒有聽到語音,產不出字幕。\n"
            "  可能是:V1 的片段本身沒有聲音(口白在另一條音軌上)、\n"
            "  或者辨識語言設錯了(⚙ 設定 > 辨識 > 辨識語言)。")

    # 重建的音訊本身就是時間軸,所以辨識出來的時間戳直接可用 —— 用「全保留」
    # 的恆等映射產字幕就好,不必也不該再對位一次。
    total_frames = int(round(total_sec * fps))
    table = RemapTable([Segment(0, total_frames, "keep")], fps)
    subs = table.build_subtitles(
        words,
        max_chars=cfg.SUBTITLE_MAX_CHARS,
        max_gap_frames=round(cfg.SUBTITLE_MAX_GAP_SEC * fps),
        max_chars_no_punct=getattr(cfg, "SUBTITLE_MAX_CHARS_NO_PUNCT", None),
        min_chars=getattr(cfg, "SUBTITLE_MIN_CHARS", 0),
        hard_gap_frames=round(getattr(cfg, "SUBTITLE_HARD_GAP_SEC", 4.0) * fps),
    )
    progress.finish_run()

    out_srt = wpath(work_dir, "05_subtitles_asr.srt")
    write_srt(subs, fps, out_srt)
    print(f"  字幕:{len(subs)} 行、{len(words)} 個詞")
    return out_srt


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法:python -m modules.seq_asr <layout.json> <output資料夾>",
              file=sys.stderr)
        sys.exit(1)
    path = build_from_layout(sys.argv[1], sys.argv[2])
    print(f"完成 ✓ {path}")
