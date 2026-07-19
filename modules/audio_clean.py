"""
音訊清理模組 —— 兩條路都實作,用 config.AUDIO_MODE 切換。

路線 A "vst"        : 用 pedalboard 載入你現有的 VST3 鏈(你在 PR 調好的參數)
路線 B "opensource" : DeepFilterNet 降噪 + ffmpeg loudnorm 響度標準化

依賴:
  pip install pedalboard soundfile        (VST 路線)
  pip install deepfilternet torch soundfile  (開源路線)
  另外需要系統安裝 ffmpeg 並加入 PATH

輸出:清理後的 WAV,以及「混回影片」的 mp4(視訊不重編碼,幾秒完成)。
"""

from __future__ import annotations
import subprocess, os, sys, contextlib, hashlib
import config.settings as cfg


def vst_state_path(vst_path: str) -> str:
    """某個 VST 外掛「調好的參數」存放路徑(用路徑雜湊當檔名)。
    使用者在面板按『開啟調整』調好參數後存這裡,載入外掛時自動套用。"""
    h = hashlib.md5(vst_path.encode("utf-8")).hexdigest()[:16]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "vst_state", h + ".bin")


@contextlib.contextmanager
def _suppress_native_output():
    """暫時關掉作業系統層級的 stdout/stderr。

    有些 VST 外掛(例如 VoiceFX)會從 C++ 底層直接狂印除錯訊息,
    這些不經過 Python 的 print,得在檔案描述元層級攔截才壓得下來。
    處理一支 3 分鐘的片可能吐出好幾 MB,不關掉會刷爆命令列。
    """
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_out, saved_err = os.dup(1), os.dup(2)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(devnull)
        os.close(saved_out)
        os.close(saved_err)


def extract_audio(video_path: str, out_wav: str) -> str:
    """從影片抽出音軌成 WAV(48kHz 單聲道,適合後續處理)"""
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "48000",
        "-c:a", "pcm_s16le", out_wav,
    ], check=True, capture_output=True)
    return out_wav


def clean_vst(in_wav: str, out_wav: str) -> str:
    """路線 A:載入 VST 鏈處理"""
    from pedalboard import Pedalboard, load_plugin
    from pedalboard.io import AudioFile

    if not cfg.VST_CHAIN:
        raise RuntimeError(
            "config.VST_CHAIN 是空的。請在 settings.py 填入你的 .vst3 路徑。")

    print(f"  載入 {len(cfg.VST_CHAIN)} 個 VST 外掛並處理...")
    # 外掛載入與處理都包在 _suppress_native_output 裡,壓掉底層除錯訊息
    with _suppress_native_output():
        plugins = []
        for p in cfg.VST_CHAIN:
            pl = load_plugin(p)
            # 若使用者在面板調過這個外掛的參數,套用存下來的狀態
            sp = vst_state_path(p)
            if os.path.exists(sp):
                try:
                    with open(sp, "rb") as _f:
                        pl.raw_state = _f.read()
                except Exception:
                    pass
            plugins.append(pl)
        board = Pedalboard(plugins)

        with AudioFile(in_wav) as f:
            audio = f.read(f.frames)
            sr = f.samplerate
        processed = board(audio, sr)
        with AudioFile(out_wav, "w", sr, processed.shape[0]) as f:
            f.write(processed)
        # 趁底層輸出還關著,主動回收外掛物件,免得它在程式結束時才吐清理訊息
        import gc
        del board, plugins
        gc.collect()
    return out_wav


def clean_opensource(in_wav: str, out_wav: str) -> str:
    """路線 B:DeepFilterNet 降噪"""
    from df.enhance import enhance, init_df, load_audio, save_audio

    print("  DeepFilterNet 降噪中...")
    model, df_state, _ = init_df()
    audio, _ = load_audio(in_wav, sr=df_state.sr())
    enhanced = enhance(model, df_state, audio)
    save_audio(out_wav, enhanced, df_state.sr())
    return out_wav


def loudnorm(in_wav: str, out_wav: str) -> str:
    """ffmpeg 兩段式 loudnorm,精準達到目標 LUFS"""
    print(f"  響度標準化到 {cfg.TARGET_LUFS} LUFS...")
    subprocess.run([
        "ffmpeg", "-y", "-i", in_wav,
        "-af", (f"loudnorm=I={cfg.TARGET_LUFS}:"
                f"TP={cfg.TARGET_TRUE_PEAK}:LRA=11"),
        "-ar", "48000", out_wav,
    ], check=True, capture_output=True)
    return out_wav


def _video_stream_playable(path: str) -> bool:
    """驗證檔案的視訊串流是否真的能解出畫面。
    有些影片(例如某些 bandicam 4K HEVC 螢幕錄影)『複製』串流後
    會產生看似成功、實際上讀不動的檔,必須實際解一幀才驗得出來。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-map", "0:v", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and "no packets" not in (r.stderr or "")


def mux_back(video_path: str, clean_wav: str, out_mp4: str) -> str:
    """把清理後的音訊混回影片。
    這個檔案就是 Premiere XML 要引用的來源 —— 時間軸上聽到的直接是乾淨聲音。

    先試「無損複製」視訊串流(快、不掉畫質);少數影片複製後會壞,
    自動改用 GPU(hevc_nvenc)重新編碼,GPU 不可用再退回 CPU。"""
    # 路線 1:無損複製(絕大多數影片幾秒完成)
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-i", clean_wav,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-map", "0:v", "-map", "1:a", "-shortest", out_mp4,
    ], check=True, capture_output=True)
    if _video_stream_playable(out_mp4):
        return out_mp4

    # 路線 2:複製後的檔壞了,改用 GPU 重新編碼
    print("  (此影片無法無損複製,改用 GPU 重新編碼,約需數十秒...)")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path, "-i", clean_wav,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "23",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", out_mp4,
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # 路線 3:沒有 NVIDIA GPU 或 nvenc 不可用,退回 CPU(較慢)
        print("  (GPU 編碼不可用,改用 CPU 編碼,可能較慢...)")
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path, "-i", clean_wav,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx265", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", out_mp4,
        ], check=True, capture_output=True)
    return out_mp4


def clean_audio(video_path: str, work_dir: str) -> str:
    """清理音訊:抽音軌 →(降噪)→(響度標準化)。回傳乾淨的 WAV 路徑。

    注意:這一步『不』混回影片。混音留到決策引擎算完靜音段之後,
    才能一併把快轉段的聲音抹掉(見 gate_speed_audio 與 pipeline)。"""
    raw_wav = os.path.join(work_dir, "01_raw.wav")
    clean_wav = os.path.join(work_dir, "01_clean.wav")
    norm_wav = os.path.join(work_dir, "01_clean_norm.wav")

    extract_audio(video_path, raw_wav)

    # "none":不做降噪與標準化,直接用原始音軌(適合快速測試整條管線)
    if cfg.AUDIO_MODE == "none":
        print("  跳過聲音處理(AUDIO_MODE=none),使用原始音訊")
        return raw_wav

    if cfg.AUDIO_MODE == "vst":
        clean_vst(raw_wav, clean_wav)
    else:
        clean_opensource(raw_wav, clean_wav)

    loudnorm(clean_wav, norm_wav)
    print(f"  音訊清理完成 -> {norm_wav}")
    return norm_wav


def gate_speed_audio(in_wav: str, out_wav: str, segments, fps: float) -> str:
    """把「快轉(靜音)段」的音訊抹成無聲,其餘原封不動。

    原理:在 Premiere 裡快轉播放時,若那段本來就無聲,加速後仍是無聲,
    就不會有加速造成的尖聲(花栗鼠音)。回傳處理後的 WAV 路徑。"""
    import soundfile as sf
    audio, sr = sf.read(in_wav)
    n = len(audio)
    muted = 0
    for s in segments:
        if s.action == "speed":
            a = max(0, int(s.start / fps * sr))
            b = min(n, int(s.end / fps * sr))
            if b > a:
                audio[a:b] = 0
                muted += 1
    sf.write(out_wav, audio, sr)
    print(f"  已將 {muted} 個快轉段的聲音抹為無聲")
    return out_wav
