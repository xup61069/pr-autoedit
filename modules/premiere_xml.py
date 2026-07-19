"""
Premiere 專案匯出 —— 審閱模式的核心產物。

策略:不自己從零寫 FCP7 XML(格式老又雷),
先用 auto-editor 產生基本 timeline XML,再用 lxml 後處理插入 marker。

依賴:pip install auto-editor lxml

若你不想裝 auto-editor,本檔也提供一個 build_v1_timeline() 產生
auto-editor 的 v1 timeline JSON,你在命令列跑:
    auto-editor timeline.v1.json --export premiere -o project.xml
"""

from __future__ import annotations
from core.models import Timeline, Cut
from core.remap import RemapTable
import config.settings as cfg
import json, subprocess


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
    subprocess.run([
        "auto-editor", v1_json,
        "--export", "premiere",
        "-o", out_xml,
    ], check=True, capture_output=True)
    return out_xml


def insert_markers(xml_path: str, table: RemapTable, out_xml: str) -> str:
    """
    在 Premiere XML 的每個切點插入 marker。
    只插入「需要人工審閱」的切點(低信心、夠長),
    高信心的必刪冗詞不插,免得 marker 太多。
    """
    from lxml import etree

    tree = etree.parse(xml_path)
    root = tree.getroot()
    seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("XML 裡找不到 <sequence>,auto-editor 輸出格式可能改了")

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
