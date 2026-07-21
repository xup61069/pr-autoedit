"""
音訊清理模組 —— 三條路,用 config.AUDIO_MODE 切換:

  "vst"        : pedalboard 載入你的 VST3 鏈。預設 VST_BAKE=False——
                 降噪「不」烘進音檔(只做響度標準化),交給 Premiere
                 掛效果隨時可調;VST_BAKE=True 才離線處理進音檔(舊做法)。
  "opensource" : DeepFilterNet 降噪 + ffmpeg loudnorm 響度標準化
  "none"       : 完全不處理,直接用原始音訊(測流程最快)

依賴:
  pip install pedalboard soundfile        (VST 路線)
  pip install deepfilternet torch soundfile  (開源路線)
  另外需要系統安裝 ffmpeg 並加入 PATH

輸出:清理後的 WAV;混回影片(mux_back)由 pipeline 在決策後呼叫。
"""

from __future__ import annotations
import subprocess, os, sys, contextlib, hashlib
import config.settings as cfg
from modules.workspace import wpath


# VoiceFX 的「消除模式」白話 → 外掛實際吃的英文值。中英都收,容錯。
_VOICEFX_MODE_MAP = {
    "消噪音": "Noise", "消回音": "Echo", "兩者都消": "Both",
    "Noise": "Noise", "Echo": "Echo", "Both": "Both",
}


def apply_voicefx_params(plugin) -> None:
    """把面板調的降噪參數(模式 + 強度)套到外掛上。

    只在外掛真的有 mode_removal / intensity 這兩個參數時才動手(就是 VoiceFX
    這類 NVIDIA 降噪外掛);其他外掛沒有這兩個參數,整段自動略過、不影響。
    這樣就不必開那個 VoiceFX 開不出來的 GUI 視窗。"""
    params = getattr(plugin, "parameters", {}) or {}
    if "mode_removal" in params:
        mode = _VOICEFX_MODE_MAP.get(getattr(cfg, "VOICEFX_MODE", "消噪音"))
        if mode:
            try:
                plugin.mode_removal = mode
            except Exception:
                pass
    if "intensity" in params:
        try:
            val = float(getattr(cfg, "VOICEFX_INTENSITY", 100.0))
            plugin.intensity = max(0.0, min(100.0, val))
        except Exception:
            pass


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


def extract_audio(source, out_wav: str) -> str:
    """從影片抽出音軌成 WAV(48kHz 單聲道,適合後續處理)。

    source 是 modules.sources.VideoSource:可能是一個檔,也可能是好幾個檔
    要接成一支。input_args() 會給出對應的 ffmpeg 輸入參數,這裡不必分辨。"""
    subprocess.run([
        "ffmpeg", "-y", *source.input_args(),
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
            "你勾了「降噪烘進音檔」,但沒有指定降噪外掛,所以沒東西可以處理。\n"
            "  三選一:\n"
            "    1. 面板 > 進階 > VST 外掛路徑,選你的 .vst3 檔\n"
            "       (VST3 的外層是資料夾殼,要點進去選裡面那顆 .vst3)\n"
            "    2. 取消勾選「降噪烘進音檔」,改在 Premiere 的音軌混音器\n"
            "       對 A1 軌掛降噪(建議做法,隨時可調可關)\n"
            "    3. 把「聲音處理方式」改成 none,先不做降噪")

    print(f"  載入 {len(cfg.VST_CHAIN)} 個 VST 外掛並處理...")
    # 外掛載入與處理都包在 _suppress_native_output 裡,壓掉底層除錯訊息
    with _suppress_native_output():
        plugins = []
        for p in cfg.VST_CHAIN:
            pl = load_plugin(p)
            # 若使用者在面板用 GUI 調過這個外掛,先套用存下來的整體狀態
            sp = vst_state_path(p)
            if os.path.exists(sp):
                try:
                    with open(sp, "rb") as _f:
                        pl.raw_state = _f.read()
                except Exception:
                    pass
            # 再套用面板滑條調的降噪參數(VoiceFX);排在 raw_state 之後,面板值優先
            apply_voicefx_params(pl)
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


def _writable_target(out_mp4: str) -> str:
    """挑一個寫得進去的輸出檔名。

    之前產的 01_clean_av.mp4 若已匯入 Premiere,Windows 會鎖住它,
    ffmpeg 蓋不掉會直接失敗(權限被拒)。遇到這種情況自動改存
    _2、_3… 的新檔名:舊序列繼續用舊檔,新序列用新檔,互不干擾。"""
    base, ext = os.path.splitext(out_mp4)
    cand = out_mp4
    i = 2
    while os.path.exists(cand):
        try:
            with open(cand, "r+b"):
                return cand              # 檔案存在但沒被鎖 -> 直接覆蓋
        except OSError:
            cand = f"{base}_{i}{ext}"    # 被鎖(多半是 Premiere)-> 換名字
            i += 1
    return cand


# 重新編碼時的候選順序:越前面越省空間。
# 每一個都是 (編碼器, 給人看的名字, 額外參數)
_ENCODERS = [
    ("av1_nvenc",  "顯卡 AV1(最省空間)",   ["-preset", "p5"]),
    ("hevc_nvenc", "顯卡 H.265",            ["-preset", "p5"]),
    ("h264_nvenc", "顯卡 H.264",            ["-preset", "p5"]),
    ("libx265",    "CPU H.265(較慢)",      ["-preset", "medium"]),
    ("libx264",    "CPU H.264(較慢)",      ["-preset", "medium"]),
]


def _encoder_ladder() -> list[tuple[str, str, list[str]]]:
    """要依序嘗試哪些編碼器。

    指定了特定編碼器就把它排第一,其餘當退路 —— 指定的那個在這台機器上
    不能用時(例如顯卡太舊、或 ffmpeg 版本不認得),仍然編得出來,
    只是會換一個並在訊息裡說明,不會讓整條管線失敗。"""
    want = getattr(cfg, "VIDEO_ENCODER", "auto")
    if want in ("auto", "", None):
        return list(_ENCODERS)
    first = [e for e in _ENCODERS if e[0] == want]
    rest = [e for e in _ENCODERS if e[0] != want]
    return first + rest


# 各編碼器的畫質數字換算。
#
# 同一個「23」在不同編碼器代表的畫質完全不同,不能直接照抄 ——
# 實測同一支 4K 片(以 H.265 cq23 為基準,SSIM 0.9922、3620 KB):
#     AV1 cq23  ->  5327 KB(反而大了 47%!)
#     AV1 cq30  ->  3253 KB(小 10%),SSIM 0.9915 幾乎一樣
# 也就是說,AV1 要把數字調高才會發揮它省空間的優勢。
# 沒有這個換算的話,「選 AV1 比較省空間」會變成一句錯的話。
_QUALITY_OFFSET = {
    "av1_nvenc": +7,     # AV1 的量化尺度不同,要往上加才對得上
    "h264_nvenc": -2,    # H.264 效率較差,同樣數字會比較糊,往下補一點
    "libx264": -2,
}


def _quality_args(encoder: str) -> list[str]:
    """畫質參數。顯卡編碼器用 -cq、CPU 編碼器用 -crf,兩者不通用。"""
    base = int(getattr(cfg, "VIDEO_QUALITY", 23))
    q = str(max(0, min(51, base + _QUALITY_OFFSET.get(encoder, 0))))
    return ["-cq", q] if encoder.endswith("_nvenc") else ["-crf", q]


def mux_back(source, clean_wav: str, out_mp4: str) -> str:
    """把清理後的音訊混回影片。回傳實際寫出的檔案路徑(被 Premiere
    鎖住時會自動改名,呼叫端要用回傳值,不要用傳入的 out_mp4)。
    這個檔案就是 Premiere XML 要引用的來源 —— 時間軸上聽到的直接是乾淨聲音。

    先試「無損複製」視訊串流(快、不掉畫質);少數影片複製後會壞,
    自動改用 GPU(hevc_nvenc)重新編碼,GPU 不可用再退回 CPU。

    source 給多個檔時,接合就發生在這一步 —— 不會先產生一份合併後的大檔。
    4K 長片一份就十幾 GB,那份中繼檔用完即丟,純粹浪費硬碟跟時間;
    這裡寫出來的 01_clean_av.mp4 本來就是完整的一支。"""
    picked = _writable_target(out_mp4)
    if picked != out_mp4:
        print(f"  ({os.path.basename(out_mp4)} 正被 Premiere 使用中,"
              f"改存 {os.path.basename(picked)})")
    out_mp4 = picked
    in_args = source.input_args()
    # 路線 1:無損複製(絕大多數影片幾秒完成)
    subprocess.run([
        "ffmpeg", "-y", *in_args, "-i", clean_wav,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-map", "0:v", "-map", "1:a", "-shortest", out_mp4,
    ], check=True, capture_output=True)
    if _video_stream_playable(out_mp4):
        return out_mp4

    # 路線 2:複製後的檔壞了,只好重新編碼。
    # 依序試候選編碼器,第一個「編得出來而且真的能播」的就用它。
    # 不預先偵測硬體支不支援 —— 直接試最準:實測過某些機器
    # 「編碼器清單裡有 AV1、顯卡也支援,但 ffmpeg 版本太舊就是編不出來」。
    print("  (此影片無法無損複製,需要重新編碼)")
    tried = []
    for encoder, label, extra in _encoder_ladder():
        print(f"  嘗試 {label}…")
        try:
            subprocess.run([
                "ffmpeg", "-y", *in_args, "-i", clean_wav,
                "-map", "0:v", "-map", "1:a",
                "-c:v", encoder, *extra, *_quality_args(encoder),
                "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                "-shortest", out_mp4,
            ], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            tried.append(label)
            continue
        if not _video_stream_playable(out_mp4):
            tried.append(label + "(編出來但讀不動)")
            continue
        if tried:
            print(f"  改用「{label}」完成"
                  f"(前面這些在這台機器上不能用:{'、'.join(tried)})")
        else:
            print(f"  用「{label}」完成")
        return out_mp4

    raise RuntimeError(
        "所有影片編碼器都失敗了,沒辦法把聲音混回影片。\n"
        f"  試過:{'、'.join(tried)}\n"
        "  這通常代表 ffmpeg 安裝不完整。可以試著重裝:\n"
        "      winget upgrade Gyan.FFmpeg")


def clean_audio(source, work_dir: str) -> str:
    """清理音訊:抽音軌 →(降噪)→(響度標準化)。回傳乾淨的 WAV 路徑。

    注意:這一步『不』混回影片。混音留到決策引擎算完靜音段之後,
    才能一併把快轉段的聲音抹掉(見 gate_speed_audio 與 pipeline)。"""
    raw_wav = wpath(work_dir, "01_raw.wav")
    clean_wav = wpath(work_dir, "01_clean.wav")
    norm_wav = wpath(work_dir, "01_clean_norm.wav")

    extract_audio(source, raw_wav)

    # "none":不做降噪與標準化,直接用原始音軌(適合快速測試整條管線)
    if cfg.AUDIO_MODE == "none":
        print("  跳過聲音處理(AUDIO_MODE=none),使用原始音訊")
        return raw_wav

    if cfg.AUDIO_MODE == "vst":
        if getattr(cfg, "VST_BAKE", True):
            clean_vst(raw_wav, clean_wav)
        else:
            # 活專案理念:降噪不烘死,交給 Premiere 掛效果隨時調
            print("  降噪不烘進音檔(VST_BAKE=False),交給 Premiere 掛效果;"
                  "只做響度標準化")
            clean_wav = raw_wav
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
    # float32 而不是預設的 float64:整支影片的音訊會一次讀進記憶體,
    # 34 分鐘的片這一項就差 390MB。抹靜音只是把樣本設成 0,不做運算,
    # 而寫出去仍然是 16-bit PCM——已實測兩者寫出的檔案位元組完全相同。
    audio, sr = sf.read(in_wav, dtype="float32")
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
