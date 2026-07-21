"""
主程式 —— 串接整條管線。

用法:
    python pipeline.py 你的影片.mp4
    python pipeline.py 你的影片.mp4 --fps 29.97
    python pipeline.py 你的影片.mp4 --skip-audio   (音訊已清理過,只重跑後段)

產物(全部在 output/影片名/ 底下)。最外層只放你會用到的四個:
    04_report.html       審閱報告(先開這個掃一遍)
    04_project.xml       帶 marker 的 Premiere 專案(匯入這個)
    04_subtitles.srt     重映射後的繁體字幕
    01_clean_av.mp4      混好聲音的影片(專案引用的素材,別搬走)

其餘全是程式自用的中繼檔,收在 _work/ 子資料夾,不用理它
(音軌、轉錄快取、決策結果…;整個刪掉也沒關係,只是下次要重跑辨識)。
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
from modules.workspace import wpath, prepare as prepare_workspace, tidy
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
    # 建好中繼檔資料夾,順便把舊版平鋪在外層的中繼檔搬進去
    prepare_workspace(work)

    fps = args.fps or get_fps(args.video)
    print(f"\n=== 處理 {name}(fps={fps})===\n")

    # --- 1. 音訊清理(只清理,先不混回影片)---
    clean_mp4 = wpath(work, "01_clean_av.mp4")
    norm_wav = wpath(work, "01_clean_norm.wav")
    raw_wav = wpath(work, "01_raw.wav")
    audio_info = wpath(work, "01_audio_info.json")
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
    cache = wpath(work, "02_transcript.json")
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
    want_audible = cfg.MUSIC_DETECT and has_probe
    want_quiet = getattr(cfg, "MICRO_TRIM", False) and has_probe

    audible, quiet = [], []
    if want_audible or want_quiet:
        # 音檔只讀一次分給兩個偵測。以前兩個入口各自 sf.read 一次同一個檔,
        # 等於整支影片的音訊被完整讀進記憶體兩遍——長片上這是最貴的一筆
        # (一個半小時的片,光是多讀那一次就是 1GB 和好幾秒)。
        from modules.audio_probe import (read_audio, audible_regions_from_array,
                                         quiet_regions_from_array)
        probe_audio, probe_sr = read_audio(probe_wav)
        if want_audible:
            audible = audible_regions_from_array(probe_audio, probe_sr, fps)
        if want_quiet:
            quiet = quiet_regions_from_array(probe_audio, probe_sr, fps)
        del probe_audio      # 後面還要混音、產 XML,不必一路佔著這塊記憶體

    # 畫面活動:沒講話的段落,靠畫面決定要加速(有在示範)還是剪掉(純空檔)。
    # 用「原始影片」而不是混音後的檔——混音後的檔這時還沒產生。
    # 只有停頓處理方式選「看畫面決定」才需要掃畫面 —— 選一律快轉或一律剪掉時
    # 畫面資訊派不上用場,掃了只是白白多花時間。
    #
    # 這一步「失敗」和「找不到東西」都必須講出來。停頓處理選 auto 時,
    # 沒有畫面資訊就等於全部停頓都只快轉、一秒都不剪 —— 產出會跟你的預期
    # 差非常多,而報告上只會看到「畫面在動改加速 0 段」,你分不出那是
    # 「真的沒有活動」還是「這一步根本沒跑成功」。
    motion = []
    motion_failed = False
    if cfg.SILENCE_ACTION == "auto":
        from modules.video_probe import detect_motion_regions
        print("  分析畫面活動…(第一次要掃過整支影片,之後會沿用)")
        try:
            motion = detect_motion_regions(
                args.video, fps, cache_json=wpath(work, "02_motion.json"))
        except Exception as e:
            # 掃畫面只是加分項,不該讓整支片一個產物都拿不到。
            # 但也不能安靜地降級——降級後的結果跟你選的設定不一樣。
            motion_failed = True
            print(f"  ⚠ 畫面分析失敗,這次改成「一律快轉」處理停頓"
                  f"(不會剪掉任何停頓)。\n"
                  f"    技術原因:{type(e).__name__}: {e}\n"
                  f"    影片能正常播放的話,把「停頓處理方式」改成"
                  f"「一律剪掉」或「一律快轉」就能避開這一步。")

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

    micro_trimmed = None
    if quiet:
        from core.decision import trim_quiet_inside, protect_words
        # 別把整個詞吃掉:輕聲短字(你、它、的)音量低,整個掉在門檻下面時
        # 會連聲音帶字幕一起消失,聽起來像跳針。代價只有幾秒,很划算。
        if getattr(cfg, "MICRO_TRIM_PROTECT_WORDS", True):
            quiet = protect_words(quiet, words, fps)
        before_keep = sum(s.duration for s in segments if s.action == "keep")
        segments = trim_quiet_inside(segments, quiet, fps)
        after_keep = sum(s.duration for s in segments if s.action == "keep")
        # 先記著,等畫面判定跑完再印。畫面判定可能把其中一部分改回快轉
        # (見下面),那時候這個數字就不是「剪掉」多少了。
        micro_trimmed = before_keep - after_keep

    if motion:
        from core.decision import apply_motion
        segments = apply_motion(segments, motion, fps)
        n_moving = sum(1 for s in segments if s.reason == "silence_motion")
        moving_sec = sum(s.duration for s in segments
                         if s.reason == "silence_motion") / fps
        print(f"  畫面活動:{n_moving} 段沒講話但畫面在動,改為加速不剪掉"
              f"(共 {moving_sec / 60:.1f} 分)")
    elif cfg.SILENCE_ACTION == "auto" and not motion_failed:
        # 掃描成功但一段活動都沒有。整支片畫面很靜(純投影片、固定攝影機)、
        # 或靈敏度調得太高都會這樣。此時 apply_motion 根本不會被呼叫,
        # 所有停頓維持快轉 —— 也就是「一秒都沒剪掉」,必須講出來。
        print("  ⚠ 畫面活動:整支片沒有偵測到任何畫面變化,"
              "所以這次的停頓全部用快轉帶過、沒有剪掉任何一段。\n"
              "    這才是你要的就不用理它;想把靜止的停頓剪掉,"
              "把進階設定的「畫面活動靈敏度」調小再按重算剪輯。")

    # 微剪的數字要等畫面判定跑完才算得準。
    #
    # 微剪挖出來的安靜區是 delete + reason="silence",而畫面判定只看
    # reason 和長度 —— 所以任何長度超過 MOTION_MIN_SEC(0.5 秒)的微剪段,
    # 只要當下畫面在動,就會被翻回 speed。教學片講到一半停 0.6 秒拉推桿
    # 正是這種情況,一點都不罕見。在畫面判定之前印,報出來的「剪掉多少」
    # 會比實際多,而那個數字正是使用者判斷「微剪值不值得開」的依據。
    if micro_trimmed is not None:
        still_cut = sum(s.duration for s in segments
                        if s.action == "delete" and s.reason == "silence")
        kept_by_motion = max(0, micro_trimmed - still_cut)
        msg = f"  能量微剪:剪掉 {micro_trimmed / fps / 60:.1f} 分" \
              "(講話段裡面沒聲音的小停頓)"
        if kept_by_motion > 0:
            msg += (f",其中 {kept_by_motion / fps / 60:.1f} 分因為畫面在動"
                    "改成快轉保留下來")
        print(msg)

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
            clean_wav, wpath(work, "01_clean_gated.wav"), segments, fps)

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
    mux_info = wpath(work, "01_mux_info.json")
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
    timeline.to_json(wpath(work, "03_timeline.json"))

    table = RemapTable(segments, fps)

    # --- 4. 產物:XML + marker + 字幕 + 報告 ---
    print("[4/5] 產生審閱檔案")
    from modules.subtitles import write_srt
    from modules.report import generate as gen_report
    from core.models import Segment

    final_xml = wpath(work, "04_project.xml")
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
                               wpath(work, "03_timeline.v1.json"))
        try:
            raw_xml = export_premiere_xml(
                v1, wpath(work, "04_project_raw.xml"))
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
    write_srt(subs, fps, wpath(work, "04_subtitles.srt"))
    gen_report(timeline, words, table, wpath(work, "04_report.html"),
               live=live)

    # --- 5. 完成 ---
    tidy(work)          # 清掉純中繼的半成品音檔,省硬碟
    print(f"\n[5/5] 完成 ✓  產物在 {work}/")
    print("  下一步:先開 04_report.html 掃一遍,再把 04_project.xml 匯入 Premiere")
    if live:
        print("  活專案:片段全保留,顏色=粉紅靜音/青綠音樂/紫冗詞。"
              "時間軸右鍵『標籤 > 選取標籤群組』可一次選同色片段批次刪除或改速度")


if __name__ == "__main__":
    main()
