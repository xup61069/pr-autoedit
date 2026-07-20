"""
主程式 —— 串接整條管線。

用法:
    python pipeline.py 你的影片.mp4
    python pipeline.py 你的影片.mp4 --fps 29.97
    python pipeline.py 你的影片.mp4 --skip-audio   (音訊已清理過,只重跑後段)

產物(全部在 output/ 底下,以影片檔名分資料夾):
    01_clean_av.mp4      音訊清理後、混回影片
    02_transcript.json   詞級轉錄(快取;調剪輯參數免重轉,改辨識設定會自動重轉)
    03_timeline.json     決策引擎輸出的段落清單
    04_project.xml       帶 marker 的 Premiere 專案(匯入這個)
    04_subtitles.srt     重映射後的繁體字幕(拖進字幕軌)
    04_report.html       審閱報告(進 PR 前先掃這個)
"""

from __future__ import annotations
import argparse, datetime, hashlib, json, os, sys, subprocess

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


def get_dimensions(video_path: str) -> tuple[int, int]:
    """讀出影片的寬高(活專案 XML 需要)"""
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def get_total_frames(video_path: str, fps: float) -> int:
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True, check=True).stdout.strip()
    return int(float(out) * fps)


def audio_fingerprint() -> str:
    """把「會影響音檔內容」的設定壓成一個指紋。

    「重算剪輯」為了快,預設跳過音訊清理直接沿用上次清好的音檔。
    但如果你改的正好是聲音類設定(降噪要不要烘進去、響度、外掛…),
    沿用舊音檔就等於你的修改根本沒生效,而且畫面上完全看不出來。
    指紋一變就強制重跑那一步。"""
    parts = [str(cfg.AUDIO_MODE), str(cfg.TARGET_LUFS), str(cfg.TARGET_TRUE_PEAK)]
    if cfg.AUDIO_MODE == "vst":
        bake = bool(getattr(cfg, "VST_BAKE", True))
        parts.append(str(bake))
        # 降噪不烘進音檔時,外掛與它的參數對音檔沒有任何影響,不必列入
        if bake:
            parts += [str(cfg.VST_CHAIN),
                      str(getattr(cfg, "VOICEFX_MODE", "")),
                      str(getattr(cfg, "VOICEFX_INTENSITY", ""))]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="輸入影片路徑")
    ap.add_argument("--fps", type=float, default=None,
                    help="覆寫幀率(預設自動偵測)")
    ap.add_argument("--skip-audio", action="store_true",
                    help="跳過音訊清理(已有 01_clean_av.mp4)")
    ap.add_argument("--mode", choices=["live", "baked"], default=None,
                    help="覆寫交付方式(面板「重算剪輯」按鈕用,不動設定檔)")
    ap.add_argument("--stamp", action="store_true",
                    help="序列名稱加上時間(重算用:多條序列才分得出誰是誰)")
    args = ap.parse_args()

    if args.mode:
        cfg.DELIVERY_MODE = args.mode

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
    audio_info = os.path.join(work, "01_audio_info.json")
    audio_fp = audio_fingerprint()
    skip = args.skip_audio and (os.path.exists(norm_wav) or os.path.exists(raw_wav))
    if skip:
        old_fp = None
        try:
            with open(audio_info, "r", encoding="utf-8") as f:
                old_fp = json.load(f).get("fingerprint")
        except (ValueError, OSError):
            pass
        if old_fp != audio_fp:
            skip = False
            print("[1/5] 聲音設定已變更,重新處理音訊(這一步比較久,請稍候)")

    if skip:
        print("[1/5] 跳過音訊清理(用現有乾淨音訊)")
        clean_wav = norm_wav if os.path.exists(norm_wav) else raw_wav
    else:
        print("[1/5] 音訊清理")
        from modules.audio_clean import clean_audio
        clean_wav = clean_audio(args.video, work)
        with open(audio_info, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": audio_fp}, f)

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

    # --- 2.5 音訊能量分析(用「原始」音訊,降噪後的檔音樂可能已被削掉)---
    #   audible = 有聲音的地方 -> 拿來「保護」音樂/音效段
    #   quiet   = 沒聲音的地方 -> 拿來「剪掉」講話段裡面的小停頓
    probe_wav = raw_wav if os.path.exists(raw_wav) else clean_wav
    has_probe = os.path.exists(probe_wav)
    audible = []
    if cfg.MUSIC_DETECT and has_probe:
        from modules.audio_probe import detect_audible_regions
        audible = detect_audible_regions(probe_wav, fps)

    quiet = []
    if getattr(cfg, "MICRO_TRIM", False) and has_probe:
        from modules.audio_probe import detect_quiet_regions
        quiet = detect_quiet_regions(probe_wav, fps)

    # --- 3. 決策引擎 ---
    print("[3/5] 決策引擎")
    segments = build_segments(words, fps, total_frames, audible=audible)

    # 重講偵測:砍掉「說錯重來」的前一次(預設關閉,見 settings 的說明)
    if getattr(cfg, "RETAKE_DETECT", False):
        from core.decision import find_retakes, drop_retakes
        retakes = find_retakes(words, fps)
        if retakes:
            segments = drop_retakes(segments, retakes, fps)
            secs = sum(b - a for a, b, _ in retakes) / fps
            print(f"  重講偵測:砍掉 {len(retakes)} 處說錯重來,共 {secs:.1f} 秒"
                  f"(信心低,全部會下 marker,請看報告確認)")

    if quiet:
        from core.decision import trim_quiet_inside, protect_words
        # 別把整個詞吃掉:輕聲短字(你、它、的)音量低,整個掉在門檻下面時
        # 會連聲音帶字幕一起消失,聽起來像跳針。代價只有幾秒,很划算。
        if getattr(cfg, "MICRO_TRIM_PROTECT_WORDS", True):
            quiet = protect_words(quiet, words, fps)
        before_keep = sum(s.duration for s in segments if s.action == "keep")
        segments = trim_quiet_inside(segments, quiet, fps)
        after_keep = sum(s.duration for s in segments if s.action == "keep")
        saved = (before_keep - after_keep) / fps
        print(f"  能量微剪:再剪掉 {saved / 60:.1f} 分"
              f"(講話段裡面沒聲音的小停頓)")

    n_del = sum(1 for s in segments if s.action == "delete")
    n_spd = sum(1 for s in segments if s.action == "speed")
    n_music = sum(1 for s in segments if s.reason == "music")
    print(f"  {len(segments)} 段:刪除 {n_del}、快轉 {n_spd}、音樂保護 {n_music}")

    live = getattr(cfg, "DELIVERY_MODE", "baked") == "live"

    # --- 3.5 混回影片:視需要先把快轉段的聲音抹成無聲,再混音 ---
    from modules.audio_clean import gate_speed_audio, mux_back
    audio_for_mux = clean_wav
    # 活專案模式不預先抹音:快轉還沒真的發生,抹了會毀掉你可能想保留的聲音
    if (not live) and cfg.MUTE_SPEED_AUDIO \
            and any(s.action == "speed" for s in segments):
        audio_for_mux = gate_speed_audio(
            clean_wav, os.path.join(work, "01_clean_gated.wav"), segments, fps)

    # 混音內容的「指紋」= 要混進去的音訊檔內容 + 哪幾段被消音。
    # 指紋跟上次一樣就沿用上次混好的影片:
    #   (a) 重算幾秒完成,不用每次重混(4K 一次要幾十秒);
    #   (b) 不會去覆寫 Premiere 正在使用的檔(蓋不掉會直接失敗)。
    def _file_md5(p: str) -> str:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    gated = "" if audio_for_mux == clean_wav else ",".join(
        f"{s.start}-{s.end}" for s in segments if s.action == "speed")
    fingerprint = f"{_file_md5(audio_for_mux)}|{gated}"
    mux_info = os.path.join(work, "01_mux_info.json")
    actual_mp4 = None
    if os.path.exists(mux_info):
        try:
            with open(mux_info, "r", encoding="utf-8") as f:
                info = json.load(f)
            cand = os.path.join(work, info.get("file", ""))
            if info.get("fingerprint") == fingerprint and os.path.exists(cand):
                actual_mp4 = cand
                print("  沿用上次混好的影片(內容相同,不用重混)")
        except (ValueError, OSError):
            pass
    if actual_mp4 is None:
        print("  混回影片...")
        # 回傳的路徑可能不同於 clean_mp4(被 Premiere 鎖住時自動改名)
        actual_mp4 = mux_back(args.video, audio_for_mux, clean_mp4)
        with open(mux_info, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint,
                       "file": os.path.basename(actual_mp4)}, f)
    clean_mp4 = actual_mp4

    # timeline 要等混音完才能定案:source 必須指向「實際寫出」的影片檔
    timeline = Timeline(fps=fps, source=os.path.abspath(clean_mp4),
                        segments=segments)
    timeline.to_json(os.path.join(work, "03_timeline.json"))

    table = RemapTable(segments, fps)

    # --- 4. 產物:XML + marker + 字幕 + 報告 ---
    print("[4/5] 產生審閱檔案")
    from modules.subtitles import write_srt
    from modules.report import generate as gen_report
    from core.models import Segment

    final_xml = os.path.join(work, "04_project.xml")
    # 先把上一次的剪輯專案刪掉。
    # 為什麼:萬一這次產生失敗,面板不能把「上一次的舊剪輯」當成這次的結果
    # 匯進 Premiere ——那會讓你以為改的設定沒生效,卻看不到任何錯誤訊息。
    # 寧可讓它明明白白地失敗,也不要安靜地給你錯的東西。
    if os.path.exists(final_xml):
        try:
            os.remove(final_xml)
        except OSError:
            pass          # 被 Premiere 鎖住;下面寫入時一樣會失敗並報錯

    # 序列名稱帶上影片名:在 Premiere 專案裡一眼看得出是哪支片,
    # 面板重跑時也才能只覆蓋「這支片的」舊序列。
    # --stamp(重算時)再加上時間:重算會保留舊序列讓你比較,全部同名的話
    # 根本分不出哪條是剛剛那次、想反悔也挑不到。
    seq_name = f"{name} 活專案" if live else f"{name} 自動剪輯"
    if args.stamp:
        seq_name += " " + datetime.datetime.now().strftime("%H:%M")
    if live:
        # 活專案:全保留 + 標籤,自製 XML,不需要 auto-editor
        from modules.premiere_xml import export_live_xml
        w, h = get_dimensions(args.video)
        export_live_xml(timeline, final_xml, w, h, seq_name=seq_name)
        # 時間軸=原片,字幕不需要重映射(用「全保留」恆等映射)
        sub_table = RemapTable([Segment(0, total_frames, "keep")], fps)
    else:
        from modules.premiere_xml import (build_v1_timeline, export_premiere_xml,
                                           insert_markers, mute_speed_audio_in_xml)
        v1 = build_v1_timeline(timeline,
                               os.path.join(work, "03_timeline.v1.json"))
        try:
            raw_xml = export_premiere_xml(
                v1, os.path.join(work, "04_project_raw.xml"))
            insert_markers(raw_xml, table, final_xml, sequence_name=seq_name)
            # 快轉段消音:把帶變速濾鏡的音訊片段停用(最可靠)
            if cfg.MUTE_SPEED_AUDIO and any(s.action == "speed" for s in segments):
                mute_speed_audio_in_xml(final_xml, final_xml)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            # 這裡以前只印一行「略過 XML」就當作成功結束,結果面板照樣去匯入
            # 上一次留下的舊檔 —— 看起來一切正常,實際上你這次的設定完全沒生效。
            # 現在改成直接失敗,讓你當場就知道出事了。
            sys.exit(
                "\n剪輯引擎(auto-editor)沒有跑成功,這次沒有產生剪輯專案。\n"
                f"  技術原因:{e}\n"
                "  最常見的原因是它沒裝好。請在命令列執行:\n"
                "      pip install auto-editor\n"
                "  如果早就裝過,把上面「技術原因」那一整行複製下來回報。")
        sub_table = table

    subs = sub_table.build_subtitles(
        words,
        max_chars=cfg.SUBTITLE_MAX_CHARS,
        max_gap_frames=round(cfg.SUBTITLE_MAX_GAP_SEC * fps),
        max_chars_no_punct=getattr(cfg, "SUBTITLE_MAX_CHARS_NO_PUNCT", None),
    )
    write_srt(subs, fps, os.path.join(work, "04_subtitles.srt"))
    gen_report(timeline, words, table, os.path.join(work, "04_report.html"),
               live=live)

    # --- 5. 完成 ---
    print(f"\n[5/5] 完成 ✓  產物在 {work}/")
    print("  下一步:先開 04_report.html 掃一遍,再把 04_project.xml 匯入 Premiere")
    if live:
        print("  活專案:片段全保留,顏色=粉紅靜音/青綠音樂/紫冗詞。"
              "時間軸右鍵『標籤 > 選取標籤群組』可一次選同色片段批次刪除或改速度")


if __name__ == "__main__":
    main()
