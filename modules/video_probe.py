"""畫面變化偵測 —— 找出「螢幕正在動」的段落。

為什麼需要:決策引擎只聽聲音。你默默示範操作的時候——拉推桿、開選單、
比對前後差異——沒有講話,音樂偵測也保護不到(它是靠聲音判斷的),
於是整段被當成停頓剪掉。內容真的消失了,而且不容易發現。

有了畫面資訊,沒有講話的段落就能分成兩種來處理:
    畫面在動   -> 你在示範東西,加速帶過(看得到,但不拖時間)
    畫面靜止   -> 純粹的空檔,直接剪掉

做法:
  1. 用 ffmpeg 把畫面縮到很小的灰階圖、每秒只抽幾張(4K 長片也只要幾十秒)。
  2. 比較相鄰兩張的平均差異,超過門檻就算「這一刻畫面在動」。
  3. 把連續在動的時刻連成區間,縫合短間隔、丟掉太短的碎片。

縮圖是刻意的:滑鼠游標移動、影片播放、拖曳推桿這種「有意義的動作」在縮圖上
仍然看得出來;而畫面編碼雜訊、極輕微的抖動會被縮圖平均掉,不會誤判成在動。
"""

from __future__ import annotations
import json
import os
import subprocess
import threading
import numpy as np
import config.settings as cfg

PROBE_W, PROBE_H = 160, 90   # 分析用的縮圖尺寸(比例失真無所謂,只看變化量)
MERGE_GAP_SEC = 0.8          # 兩段動作間隔小於這個秒數就縫成一段


def _stream_diffs(source, sample_fps: float, total_sec: float) -> np.ndarray:
    """一邊解碼一邊算「相鄰兩張縮圖的差異」,回傳每個取樣點的變化量。

    以前是先把整支影片的縮圖全部收進記憶體(subprocess 的 capture_output),
    再一次算差異。那有兩個問題:
      1. 記憶體:一張縮圖 14400 bytes,90 分鐘的片 @4fps 就是 21600 張、
         311MB,而且是在 Whisper 剛跑完、記憶體正吃緊的時候;
      2. 沒有進度:掃 4K 長片要跑上一分鐘,畫面上完全沒有動靜。
    改成串流之後,任何時候記憶體裡只有「前一張」和「這一張」兩張縮圖
    (約 29KB),而且每讀到一張就能回報進度。
    """
    from modules.progress import Reporter

    cmd = [
        "ffmpeg", "-v", "error", *source.input_args(),
        "-vf", f"fps={sample_fps},scale={PROBE_W}:{PROBE_H},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    frame_bytes = PROBE_W * PROBE_H
    rep = Reporter("分析畫面活動", total_sec, unit="分", scale=1 / 60)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("找不到 ffmpeg,無法分析畫面") from None

    # stderr 必須「同時」讀走,不能等 stdout 讀完再讀。
    # 管線緩衝區只有幾十 KB,塞滿之後 ffmpeg 會卡在寫 stderr 上不動,
    # 而我們還在等畫面資料 —— 兩邊互等,整支程式就這樣停住。
    # 這不是理論風險:某些螢幕錄影檔(bandicam 的 4K HEVC)會對每個封包
    # 噴一次「SPS 0 does not exist」,長片累積下來遠超過緩衝區。
    # (舊版用 subprocess.run(capture_output=True) 剛好沒事——它內部是
    #  communicate(),本來就同時處理兩條管線;改成串流才浮出這個問題。)
    _err: list[bytes] = []

    def _drain():
        try:
            _err.append(proc.stderr.read())
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain, daemon=True)
    err_thread.start()

    diffs: list[float] = []
    prev = None
    buf = b""
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes * 8)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= frame_bytes:
                cur = np.frombuffer(buf[:frame_bytes], dtype=np.uint8)
                buf = buf[frame_bytes:]
                if prev is not None:
                    diffs.append(float(np.abs(
                        cur.astype(np.int16) - prev).mean()))
                prev = cur.astype(np.int16)
                rep.update(len(diffs) / sample_fps)
    finally:
        proc.stdout.close()
        proc.wait()
        err_thread.join(timeout=5)
        try:
            proc.stderr.close()
        except Exception:
            pass
    rep.done()

    if proc.returncode != 0:
        msg = b"".join(_err).decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"ffmpeg 讀不了這支影片的畫面:{msg or '(沒有錯誤訊息)'}")
    return np.asarray(diffs, dtype=np.float64)


def _sample_frames(source, sample_fps: float) -> np.ndarray:
    """用 ffmpeg 抽出縮小的灰階畫面,回傳 shape=(張數, 高, 寬) 的陣列。

    失敗時丟出帶著 ffmpeg 原始說法的 RuntimeError。以前是直接讓
    subprocess 的 CalledProcessError 冒出去,那個例外的訊息只有
    「returned non-zero exit status 1」加一長串指令 —— 真正的原因被
    capture_output 收在 stderr 裡沒人看得到,連面板的錯誤翻譯表都對不上。

    source 可以是 VideoSource,也可以直接給路徑字串(見 sources.coerce)。"""
    from modules.sources import coerce
    source = coerce(source)
    cmd = [
        "ffmpeg", "-v", "error", *source.input_args(),
        "-vf", f"fps={sample_fps},scale={PROBE_W}:{PROBE_H},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError:
        raise RuntimeError("找不到 ffmpeg,無法分析畫面") from None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"ffmpeg 讀不了這支影片的畫面:{err or '(沒有錯誤訊息)'}"
        ) from None
    out = r.stdout
    frame_bytes = PROBE_W * PROBE_H
    n = len(out) // frame_bytes
    if n == 0:
        return np.empty((0, PROBE_H, PROBE_W), dtype=np.uint8)
    buf = np.frombuffer(out[: n * frame_bytes], dtype=np.uint8)
    return buf.reshape(n, PROBE_H, PROBE_W)


def frame_diffs(source, sample_fps: float,
                cache_json: str | None = None) -> np.ndarray:
    """每個取樣點的「畫面變化量」(相鄰縮圖的平均亮度差,0~255)。

    掃一支 17 分鐘的 4K 片要 27 秒,所以結果會快取。
    刻意快取「變化量」而不是「判定結果」:調靈敏度時不必重新解碼影片,
    直接拿同一串數字換個門檻算一遍,零秒完成。"""
    # 「這是不是同一批影片」。快取以前只記 sample_fps,不記影片本身:
    # 把同一支片重新輸出一份(或多選/少選了一個檔),快取會被當成有效的
    # 沿用,畫面判定就是照舊那批算的——完全沒有徵兆。
    from modules.sources import coerce
    source = coerce(source)
    fingerprint = source.fingerprint()
    if cache_json and os.path.exists(cache_json):
        try:
            with open(cache_json, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if (raw.get("sample_fps") == sample_fps
                    and raw.get("video") == fingerprint):
                return np.asarray(raw.get("diffs", []), dtype=np.float64)
        except (ValueError, OSError):
            pass

    total_sec = 0.0
    try:
        total_sec = float(source.duration())
    except Exception:
        pass
    diff = _stream_diffs(source, sample_fps, total_sec)

    if cache_json:
        try:
            with open(cache_json, "w", encoding="utf-8") as f:
                json.dump({"sample_fps": sample_fps, "video": fingerprint,
                           "diffs": [round(float(d), 3) for d in diff]}, f)
        except OSError:
            pass
    return diff


def motion_regions_from_diffs(diff: np.ndarray, sample_fps: float,
                              fps: float) -> list[tuple[int, int]]:
    """核心邏輯(不碰檔案,方便測試):回傳畫面在動的區間 [(起始幀, 結束幀), ...]"""
    if len(diff) < 1:
        return []

    thr = float(getattr(cfg, "MOTION_SENSITIVITY", 0.5))
    moving = diff >= thr
    step = 1.0 / sample_fps

    # 第 i 個差值代表「第 i 張到第 i+1 張之間」這段時間
    regions: list[list[float]] = []
    start = None
    for i, on in enumerate(moving):
        if on and start is None:
            start = i * step
        elif not on and start is not None:
            regions.append([start, i * step])
            start = None
    if start is not None:
        regions.append([start, len(moving) * step])

    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] < MERGE_GAP_SEC:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    min_sec = float(getattr(cfg, "MOTION_MIN_SEC", 0.5))
    return [(round(a * fps), round(b * fps))
            for a, b in merged if b - a >= min_sec]


def detect_motion_regions(source, fps: float,
                          cache_json: str | None = None
                          ) -> list[tuple[int, int]]:
    """讀影片並回傳畫面在動的區間(幀)。給 pipeline 呼叫的入口。

    source 是 modules.sources.VideoSource(可能是一個檔,也可能是好幾個檔
    接成一支)。多檔時掃的是「接起來之後」的畫面,所以跨檔交界的位置
    跟決策引擎算出來的時間軸是對得上的。"""
    sample_fps = float(getattr(cfg, "MOTION_SAMPLE_FPS", 4.0))
    diff = frame_diffs(source, sample_fps, cache_json)
    return motion_regions_from_diffs(diff, sample_fps, fps)
