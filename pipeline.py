"""
主程式 —— 串接整條管線。

用法:
    python pipeline.py 你的影片.mp4
    python pipeline.py 你的影片.mp4 --fps 29.97
    python pipeline.py 你的影片.mp4 --skip-audio   (音訊已清理過,只重跑後段)

產物(全部在 output/ 底下,以影片檔名分資料夾):
    01_clean_av.mp4      音訊清理後、混回影片
    02_transcript.json   詞級轉錄(快取,改參數重跑時免重轉)
    03_timeline.json     決策引擎輸出的段落清單
    04_project.xml       帶 marker 的 Premiere 專案(匯入這個)
    04_subtitles.srt     重映射後的繁體字幕(拖進字幕軌)
    04_report.html       審閱報告(進 PR 前先掃這個)
"""

from __future__ import annotations
import argparse, os, sys, subprocess

# Windows 中文命令列預設是 cp950 編碼,印中文或 ✓ 之類的符號會變亂碼甚至當掉。
# 強制把訊息輸出改成 UTF-8,一次解決亂碼與當掉兩個問題。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from core.models import Timeline
from core.decision import build_segments
from core.remap import RemapTable
import config.settings as cfg


def has_audio(video_path: str) -> bool:
    """檢查影片有沒有音軌(沒有的話後續轉錄、剪輯都無從做起)"""
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True).stdout.strip()
    return out == "audio"


def get_fps(video_path: str) -> float:
    """用 ffprobe 讀出影片幀率"""
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True, check=True).stdout.strip()
    num, den = out.split("/")
    return round(float(num) / float(den), 3)


def get_total_frames(video_path: str, fps: float) -> int:
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True, check=True).stdout.strip()
    return int(float(out) * fps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="輸入影片路徑")
    ap.add_argument("--fps", type=float, default=None,
                    help="覆寫幀率(預設自動偵測)")
    ap.add_argument("--skip-audio", action="store_true",
                    help="跳過音訊清理(已有 01_clean_av.mp4)")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"找不到檔案:{args.video}")

    if not has_audio(args.video):
        sys.exit("這支影片沒有音軌,無法處理。\n"
                 "本工具需要有聲音才能轉字幕、去冗詞、剪停頓。\n"
                 "請確認你選的是有收音的錄影檔。")

    name = os.path.splitext(os.path.basename(args.video))[0]
    work = os.path.join("output", name)
    os.makedirs(work, exist_ok=True)

    fps = args.fps or get_fps(args.video)
    print(f"\n=== 處理 {name}(fps={fps})===\n")

    # --- 1. 音訊清理(只清理,先不混回影片)---
    clean_mp4 = os.path.join(work, "01_clean_av.mp4")
    norm_wav = os.path.join(work, "01_clean_norm.wav")
    raw_wav = os.path.join(work, "01_raw.wav")
    if args.skip_audio and (os.path.exists(norm_wav) or os.path.exists(raw_wav)):
        print("[1/5] 跳過音訊清理(用現有乾淨音訊)")
        clean_wav = norm_wav if os.path.exists(norm_wav) else raw_wav
    else:
        print("[1/5] 音訊清理")
        from modules.audio_clean import clean_audio
        clean_wav = clean_audio(args.video, work)

    # --- 2. 轉錄 ---
    print("[2/5] 語音轉錄")
    from modules.transcribe import transcribe
    cache = os.path.join(work, "02_transcript.json")
    audio_for_asr = clean_wav if os.path.exists(clean_wav) else args.video
    words = transcribe(audio_for_asr, cache_json=cache)

    if not words:
        print("  ⚠ 幾乎沒偵測到語音。若這是口白影片,可能是聲音太小、"
              "或 config 的 WHISPER_LANGUAGE 設錯;\n"
              "    這種情況下整支片會被當成靜音處理,產出可能不如預期。")

    total_frames = get_total_frames(args.video, fps)

    # --- 2.5 音樂/音效偵測(用「原始」音訊,降噪後的檔音樂可能已被削掉)---
    audible = []
    if cfg.MUSIC_DETECT:
        from modules.audio_probe import detect_audible_regions
        probe_wav = raw_wav if os.path.exists(raw_wav) else clean_wav
        if os.path.exists(probe_wav):
            audible = detect_audible_regions(probe_wav, fps)

    # --- 3. 決策引擎 ---
    print("[3/5] 決策引擎")
    segments = build_segments(words, fps, total_frames, audible=audible)
    timeline = Timeline(fps=fps, source=os.path.abspath(clean_mp4),
                        segments=segments)
    timeline.to_json(os.path.join(work, "03_timeline.json"))
    n_del = sum(1 for s in segments if s.action == "delete")
    n_spd = sum(1 for s in segments if s.action == "speed")
    n_music = sum(1 for s in segments if s.reason == "music")
    print(f"  {len(segments)} 段:刪除 {n_del}、快轉 {n_spd}、音樂保護 {n_music}")

    # --- 3.5 混回影片:視需要先把快轉段的聲音抹成無聲,再混音 ---
    from modules.audio_clean import gate_speed_audio, mux_back
    audio_for_mux = clean_wav
    if cfg.MUTE_SPEED_AUDIO and any(s.action == "speed" for s in segments):
        audio_for_mux = gate_speed_audio(
            clean_wav, os.path.join(work, "01_clean_gated.wav"), segments, fps)
    print("  混回影片...")
    mux_back(args.video, audio_for_mux, clean_mp4)

    table = RemapTable(segments, fps)

    # --- 4. 審閱模式產物:XML + marker + 字幕 + 報告 ---
    print("[4/5] 產生審閱檔案")
    from modules.premiere_xml import (build_v1_timeline, export_premiere_xml,
                                       insert_markers, mute_speed_audio_in_xml)
    from modules.subtitles import write_srt
    from modules.report import generate as gen_report

    v1 = build_v1_timeline(timeline, os.path.join(work, "03_timeline.v1.json"))
    final_xml = os.path.join(work, "04_project.xml")
    try:
        raw_xml = export_premiere_xml(v1, os.path.join(work, "04_project_raw.xml"))
        insert_markers(raw_xml, table, final_xml)
        # 快轉段消音:把帶變速濾鏡的音訊片段停用(最可靠,見 mute_speed_audio_in_xml)
        if cfg.MUTE_SPEED_AUDIO and any(s.action == "speed" for s in segments):
            mute_speed_audio_in_xml(final_xml, final_xml)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"  (auto-editor 尚未安裝或執行失敗,略過 XML:{e})")

    subs = table.build_subtitles(
        words,
        max_chars=cfg.SUBTITLE_MAX_CHARS,
        max_gap_frames=round(cfg.SUBTITLE_MAX_GAP_SEC * fps),
    )
    write_srt(subs, fps, os.path.join(work, "04_subtitles.srt"))
    gen_report(timeline, words, table, os.path.join(work, "04_report.html"))

    # --- 5. 完成 ---
    print(f"\n[5/5] 完成 ✓  產物在 {work}/")
    print("  下一步:先開 04_report.html 掃一遍,再把 04_project.xml 匯入 Premiere")


if __name__ == "__main__":
    main()
