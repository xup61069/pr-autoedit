"""
Premiere 專案匯出 —— 兩種交付方式(config.DELIVERY_MODE)各走一條路:

  "baked"(預設,直接剪好):用 auto-editor 產生已剪好的 timeline XML,
      再用 lxml 後處理插入審閱 marker、停用快轉段音訊(避免加速尖聲)。
  "live"(活專案):export_live_xml() 自製 FCP7 XML,所有段落切開但全保留,
      用顏色標籤分類,進 Premiere 再批次決定怎麼處理。不依賴 auto-editor。

依賴:pip install auto-editor lxml(live 模式只需要 lxml)
"""

from __future__ import annotations
from core.models import Timeline, Cut
from core.remap import RemapTable
import config.settings as cfg
import json, os, subprocess, sys


# ============================================================
# 「活專案」模式:自製 FCP7 XML(不依賴 auto-editor)
# ============================================================
# 結構完全比照 auto-editor 產出、且已在使用者的 Premiere 實測匯入
# 成功的 XML(見 output/*/04_project_raw.xml):xmeml v5、一條視訊軌、
# 兩條音訊軌(立體聲拆成兩條 exploded track)。
#
# 與烘焙模式的差別:所有段落「切開但都保留」——每段各自成為獨立
# clipitem、start==in / end==out(時間軸=原始影片,零剪輯零變速),
# 用顏色標籤區分種類,讓你在 Premiere 裡隨時決定怎麼處理:
# 在時間軸右鍵選單「標籤 > 選取標籤群組」即可一次選起同色片段,
# 再一起刪除或改速度。

# 段落種類 -> Premiere 標籤色。label2 的值是 Premiere 認的英文色名
# (auto-editor 也是這樣寫 Iris,已驗證匯入沒問題)。
_LABELS = {
    "speech": None,          # 語音:不上標籤,維持預設色
    "silence": "Rose",       # 靜音:粉紅(候選:刪除或快轉)
    "silence_motion": "Lavender",  # 沒講話但畫面在動:薰衣草(示範操作,加速帶過)
    "music": "Caribbean",    # 音樂/音效:青綠(受保護,別剪)
    "noise": "Yellow",       # 短促雜音(咳嗽/滑鼠聲):黃(候選:刪除)
    "filler": "Violet",      # 冗詞:紫(候選:刪除)
    "retake": "Mango",       # 說錯重講的前一次:橘(候選:刪除,務必先確認)
}

_CLIP_NAMES = {
    "silence": "靜音", "silence_motion": "示範", "noise": "雜音",
    "music": "音樂", "filler": "冗詞", "retake": "重講",
}


# 決策引擎的 reason -> 這裡的段落種類。
#
# ⚠️ 這張表要跟 decision.py 產出的 reason 保持同步。以前這裡是一串寫死的
# if,只認得 music/silence/filler/retake 四種;後來決策引擎多出了
# silence_motion(畫面在動的示範段)和 noise(咳嗽),沒有人回來補這裡,
# 於是它們一路掉進 else 被當成「一般語音」——在 Premiere 裡沒有顏色、
# 名字就是影片名,跟真的講話段完全分不出來,「選取標籤群組」也選不到。
# 更糟的是上面 _LABELS 早就寫好了 silence_motion 的顏色,看程式碼會以為有做。
# 改成查表:漏掉新種類時,上面的 _LABELS/_CLIP_NAMES 也會一起缺,比較難只補一半。
_KIND_BY_REASON = {
    "music": "music",
    "silence": "silence",
    "silence_motion": "silence_motion",
    "noise": "noise",
    "filler": "filler",
    "retake": "retake",
}


def _segment_kind(s) -> str:
    """把決策段落歸類成標籤種類;不認得的 reason 一律當成語音(不上標籤)"""
    return _KIND_BY_REASON.get(s.reason, "speech")


def export_live_xml(timeline: Timeline, out_xml: str,
                    width: int, height: int,
                    seq_name: str | None = None) -> str:
    """產生「活專案」XML:全部保留、依段落種類切開並上色。

    timeline.segments 的 action(delete/speed)在這個模式下「不執行」,
    只轉譯成標籤顏色與 clip 名稱;真正要不要刪、要不要快轉,
    留到 Premiere 裡(手動批次、或之後的面板套用鈕)再決定。

    seq_name:序列名稱。要帶上影片名(每支片各自獨立),面板才能
    在重跑時只覆蓋「同一支影片的舊序列」,不會誤刪別支片的。"""
    from lxml import etree

    fps = timeline.fps
    timebase = round(fps)
    ntsc = "TRUE" if abs(fps - timebase) > 0.01 else "FALSE"
    total = max((s.end for s in timeline.segments), default=0)
    src = timeline.source.replace("\\", "/")
    stem = os.path.splitext(os.path.basename(src))[0]

    def rate_el(parent):
        r = etree.SubElement(parent, "rate")
        etree.SubElement(r, "timebase").text = str(timebase)
        etree.SubElement(r, "ntsc").text = ntsc
        return r

    root = etree.Element("xmeml", version="5")
    seq = etree.SubElement(root, "sequence", explodedTracks="true")
    etree.SubElement(seq, "name").text = seq_name or f"{stem} 活專案"
    etree.SubElement(seq, "duration").text = str(total)
    rate_el(seq)
    media = etree.SubElement(seq, "media")

    # ---------- 視訊 ----------
    video = etree.SubElement(media, "video")
    vfmt = etree.SubElement(video, "format")
    sc = etree.SubElement(vfmt, "samplecharacteristics")
    etree.SubElement(sc, "width").text = str(width)
    etree.SubElement(sc, "height").text = str(height)
    etree.SubElement(sc, "pixelaspectratio").text = "square"
    rate_el(sc)
    vtrack = etree.SubElement(video, "track")

    segs = timeline.segments
    n = len(segs)

    def file_el(parent, first: bool):
        if not first:
            etree.SubElement(parent, "file", id="file-1")
            return
        f = etree.SubElement(parent, "file", id="file-1")
        etree.SubElement(f, "name").text = stem
        etree.SubElement(f, "pathurl").text = src
        tcode = etree.SubElement(f, "timecode")
        etree.SubElement(tcode, "string").text = "00:00:00:00"
        etree.SubElement(tcode, "displayformat").text = \
            "DF" if ntsc == "TRUE" else "NDF"
        rate_el(tcode)
        rate_el(f)
        etree.SubElement(f, "duration").text = str(total)
        fm = etree.SubElement(f, "media")
        fv = etree.SubElement(fm, "video")
        fsc = etree.SubElement(fv, "samplecharacteristics")
        rate_el(fsc)
        etree.SubElement(fsc, "width").text = str(width)
        etree.SubElement(fsc, "height").text = str(height)
        etree.SubElement(fsc, "pixelaspectratio").text = "square"
        fa = etree.SubElement(fm, "audio")
        asc = etree.SubElement(fa, "samplecharacteristics")
        etree.SubElement(asc, "depth").text = "16"
        etree.SubElement(asc, "samplerate").text = "48000"
        etree.SubElement(fa, "channelcount").text = "2"

    def label_el(parent, kind: str):
        color = _LABELS.get(kind)
        if color:
            labels = etree.SubElement(parent, "labels")
            etree.SubElement(labels, "label2").text = color

    def clip_name(s, kind: str) -> str:
        if kind == "speech":
            return stem
        base = _CLIP_NAMES[kind]
        if kind == "filler" and s.text:
            return f"{base} {s.text}"
        # 長度直接寫在片段名上:這三種都是「要不要留」的判斷題,
        # 而長度是最快的判斷依據,不用點進去看屬性
        if kind in ("silence", "silence_motion", "noise"):
            return f"{base} {s.duration / fps:.1f}s"
        return base

    def link_el(parent, ref: str, mediatype: str, trackindex: int, clipindex: int):
        lk = etree.SubElement(parent, "link")
        etree.SubElement(lk, "linkclipref").text = ref
        etree.SubElement(lk, "mediatype").text = mediatype
        etree.SubElement(lk, "trackindex").text = str(trackindex)
        etree.SubElement(lk, "clipindex").text = str(clipindex)

    # 視訊軌 clipitem:id 1..n;音訊兩軌:n+1..2n、2n+1..3n
    for i, s in enumerate(segs):
        kind = _segment_kind(s)
        c = etree.SubElement(vtrack, "clipitem", id=f"clipitem-{i + 1}")
        etree.SubElement(c, "name").text = clip_name(s, kind)
        etree.SubElement(c, "enabled").text = "TRUE"
        etree.SubElement(c, "start").text = str(s.start)
        etree.SubElement(c, "end").text = str(s.end)
        etree.SubElement(c, "in").text = str(s.start)   # 全保留:時間軸=原片
        etree.SubElement(c, "out").text = str(s.end)
        file_el(c, first=(i == 0))
        etree.SubElement(c, "compositemode").text = "normal"
        link_el(c, f"clipitem-{i + 1}", "video", 1, i + 1)
        link_el(c, f"clipitem-{n + i + 1}", "audio", 1, i + 1)
        link_el(c, f"clipitem-{2 * n + i + 1}", "audio", 2, i + 1)
        label_el(c, kind)

    # ---------- 音訊(立體聲拆兩條軌)----------
    audio = etree.SubElement(media, "audio")
    etree.SubElement(audio, "numOutputChannels").text = "2"
    afmt = etree.SubElement(audio, "format")
    asc = etree.SubElement(afmt, "samplecharacteristics")
    etree.SubElement(asc, "depth").text = "16"
    etree.SubElement(asc, "samplerate").text = "48000"

    for ch in (0, 1):
        atrack = etree.SubElement(
            audio, "track", totalExplodedTrackCount="2",
            premiereTrackType="Stereo", currentExplodedTrackIndex=str(ch))
        etree.SubElement(atrack, "outputchannelindex").text = str(ch + 1)
        for i, s in enumerate(segs):
            kind = _segment_kind(s)
            cid = (n if ch == 0 else 2 * n) + i + 1
            c = etree.SubElement(atrack, "clipitem", id=f"clipitem-{cid}",
                                 premiereChannelType="stereo")
            etree.SubElement(c, "name").text = clip_name(s, kind)
            etree.SubElement(c, "enabled").text = "TRUE"
            etree.SubElement(c, "start").text = str(s.start)
            etree.SubElement(c, "end").text = str(s.end)
            etree.SubElement(c, "in").text = str(s.start)
            etree.SubElement(c, "out").text = str(s.end)
            etree.SubElement(c, "file", id="file-1")
            st = etree.SubElement(c, "sourcetrack")
            etree.SubElement(st, "mediatype").text = "audio"
            etree.SubElement(st, "trackindex").text = "1"
            label_el(c, kind)

    # ---------- Marker(時間軸=原片,位置直接用原始幀)----------
    n_marks = 0
    for s in segs:
        kind = _segment_kind(s)
        if kind == "music":
            name, comment = "[音樂] 確認這段是不是要保留的音樂/音效", \
                f"長度 {s.duration / fps:.1f} 秒"
        elif kind == "retake":
            name, comment = f"[重講] {s.text}", \
                f"疑似說錯重來的前一次,信心 {s.confidence:.2f},請確認再刪"
        elif kind == "filler" and s.confidence < cfg.MARKER_MAX_CONFIDENCE \
                and s.duration / fps * 1000 >= cfg.MARKER_MIN_DURATION_MS:
            name, comment = f"[冗詞] {s.text}", \
                f"信心 {s.confidence:.2f},建議刪除"
        else:
            continue                 # 靜音段靠標籤色就夠,不下 marker
        m = etree.SubElement(seq, "marker")
        etree.SubElement(m, "name").text = name
        etree.SubElement(m, "comment").text = comment
        etree.SubElement(m, "in").text = str(s.start)
        etree.SubElement(m, "out").text = "-1"
        n_marks += 1

    etree.ElementTree(root).write(out_xml, encoding="UTF-8",
                                  xml_declaration=True, pretty_print=True)
    n_by = {}
    for s in segs:
        k = _segment_kind(s)
        n_by[k] = n_by.get(k, 0) + 1
    # 每一種都報,包含數量為 0 的:看到「示範 0」你才知道是真的沒有,
    # 而不是這個種類又被漏掉了(以前示範跟雜音就是這樣消失在語音裡的)
    bits = "、".join(f"{label} {n_by.get(kind, 0)}" for kind, label in (
        ("speech", "語音"), ("silence", "靜音"), ("silence_motion", "示範"),
        ("music", "音樂"), ("noise", "雜音"), ("filler", "冗詞"),
        ("retake", "重講")))
    print(f"  活專案 XML:{len(segs)} 個片段({bits})、"
          f"{n_marks} 個 marker -> {out_xml}")
    return out_xml


def build_v1_timeline(timeline: Timeline, out_json: str) -> str:
    """
    把段落清單轉成 auto-editor 的 v1 timeline JSON。
    chunks 格式:[起始幀, 結束幀, 速度],速度 99999 = 剪掉。
    """
    chunks = []
    for s in timeline.segments:
        if s.action == "delete":
            speed = 99999.0
        elif s.action == "speed":
            speed = s.factor
        else:
            speed = 1.0
        chunks.append([s.start, s.end, speed])

    data = {
        "version": "1",
        "source": timeline.source,
        "chunks": chunks,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_json


def export_premiere_xml(v1_json: str, out_xml: str) -> str:
    """呼叫 auto-editor 把 v1 timeline 轉成 Premiere XML"""
    print("  auto-editor 產生 Premiere XML...")
    # 用「同一個 python 以模組方式」叫 auto-editor,不要用裸命令名 "auto-editor"。
    # 為什麼:面板是「直接跑 venv 的 python.exe」(沒有 activate),venv 的
    # Scripts\ 不會在子行程的 PATH 上,裸命令 "auto-editor" 會 WinError 2
    # 找不到檔案 —— 明明裝了卻說沒裝。開發機用 conda base(Scripts 在 PATH)
    # 剛好沒事,是典型的「在我機器上好好的」。sys.executable -m auto_editor
    # 用的就是正在跑的這個 python,它裝了 auto_editor 就一定找得到。
    subprocess.run([
        sys.executable, "-m", "auto_editor", v1_json,
        "--export", "premiere",
        "-o", out_xml,
    ], check=True, capture_output=True)
    return out_xml


def insert_markers(xml_path: str, table: RemapTable, out_xml: str,
                sequence_name: str | None = None) -> str:
    """
    在 Premiere XML 的每個切點插入 marker。
    只插入「需要人工審閱」的切點(低信心、夠長),
    高信心的必刪冗詞不插,免得 marker 太多。

    sequence_name:順便改掉序列名稱。auto-editor 一律叫
    "Auto-Editor Media Group",每支影片都同名,在 Premiere 專案裡
    分不出誰是誰、面板也無從判斷該覆蓋哪一條;改成帶影片名的名稱後,
    重跑同一支片才能安全地覆蓋掉自己的舊序列。
    """
    from lxml import etree

    tree = etree.parse(xml_path)
    root = tree.getroot()
    seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("XML 裡找不到 <sequence>,auto-editor 輸出格式可能改了")

    if sequence_name:
        name_el = seq.find("name")
        if name_el is None:
            name_el = etree.SubElement(seq, "name")
        name_el.text = sequence_name

    cuts = table.cuts_for_markers(
        min_duration_ms=cfg.MARKER_MIN_DURATION_MS,
        max_confidence=cfg.MARKER_MAX_CONFIDENCE,
    )

    for c in cuts:
        m = etree.SubElement(seq, "marker")
        name = etree.SubElement(m, "name")
        name.text = f"[{c.reason}] {c.text}" if c.text else f"[{c.reason}]"
        comment = etree.SubElement(m, "comment")
        comment.text = f"原始幀 {c.orig_frame},刪除 {c.duration_ms}ms,信心 {c.confidence:.2f}"
        in_el = etree.SubElement(m, "in")
        in_el.text = str(c.timeline_frame)
        out_el = etree.SubElement(m, "out")
        out_el.text = "-1"

    tree.write(out_xml, encoding="UTF-8", xml_declaration=True)
    print(f"  插入 {len(cuts)} 個審閱 marker -> {out_xml}")
    return out_xml


def mute_speed_audio_in_xml(xml_path: str, out_xml: str) -> str:
    """把「快轉段」的音訊片段停用(enabled=FALSE),達到快轉時無聲。

    為什麼這樣做:auto-editor 匯出的 XML 會在快轉片段上加「變速濾鏡」(timeremap),
    Premiere 對聲音套變速會讓音調升高(花栗鼠聲)。與其靠抹掉來源聲音(在 Premiere
    裡不一定蓋得準),不如直接把「帶變速濾鏡的音訊片段」關掉——沒有聲音片段就
    絕對不會有聲音,且片段仍在(只是停用),你想聽回原聲隨時可重新啟用。

    做法:凡是位於音軌、且自己含 timeremap 濾鏡的 clipitem,把它的 <enabled> 設為
    FALSE。影片片段不動,所以畫面照樣快轉。"""
    from lxml import etree

    tree = etree.parse(xml_path)
    root = tree.getroot()

    muted = 0
    for audio in root.findall(".//media/audio"):
        for clip in audio.findall(".//clipitem"):
            # 這個音訊片段自己有沒有掛變速濾鏡?
            has_timeremap = any(
                (eid.text or "").strip() == "timeremap"
                for eid in clip.findall("./filter/effect/effectid")
            )
            if not has_timeremap:
                continue
            en = clip.find("enabled")
            if en is None:
                en = etree.SubElement(clip, "enabled")
            en.text = "FALSE"
            muted += 1

    tree.write(out_xml, encoding="UTF-8", xml_declaration=True)
    print(f"  快轉段消音:停用 {muted} 個快轉音訊片段 -> {out_xml}")
    return out_xml
