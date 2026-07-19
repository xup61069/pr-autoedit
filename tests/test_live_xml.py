"""活專案 XML 產生器測試。執行:python -m tests.test_live_xml"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lxml import etree
from core.models import Timeline, Segment
from modules.premiere_xml import export_live_xml
import config.settings as cfg

# 鎖回預設參數,不受使用者 settings_local 覆寫影響(理由見 test_decision)
cfg.MARKER_MAX_CONFIDENCE = 0.9
cfg.MARKER_MIN_DURATION_MS = 200


def _make_timeline(fps=30.0):
    segs = [
        Segment(0, 100, "keep"),                                       # 語音
        Segment(100, 200, "speed", factor=6.0, reason="silence",
                confidence=0.95),                                      # 靜音
        Segment(200, 350, "keep", reason="music", confidence=0.8),     # 音樂
        Segment(350, 360, "delete", reason="filler", text="嗯",
                confidence=0.6),                                       # 冗詞
        Segment(360, 500, "keep"),                                     # 語音
    ]
    return Timeline(fps=fps, source=r"C:\pr-autoedit\output\x\01_clean_av.mp4",
                    segments=segs)


def _export(fps=30.0):
    tl = _make_timeline(fps)
    out = os.path.join(tempfile.gettempdir(), "test_live.xml")
    export_live_xml(tl, out, width=3840, height=2160)
    return etree.parse(out).getroot(), tl


def test_all_clips_uncut():
    """每個片段 start==in、end==out(全保留,零剪輯零變速)"""
    root, tl = _export()
    clips = root.findall(".//video/track/clipitem")
    assert len(clips) == len(tl.segments)
    for c in clips:
        assert c.find("start").text == c.find("in").text
        assert c.find("end").text == c.find("out").text
        assert c.find("enabled").text == "TRUE"
    assert not root.findall(".//effectid"), "活專案不該有任何變速濾鏡"
    print("  ✓ 全部片段原封不動(start==in、end==out、無濾鏡)")


def test_coverage_continuous():
    """視訊軌片段首尾相連、覆蓋整段"""
    root, tl = _export()
    clips = root.findall(".//video/track/clipitem")
    spans = [(int(c.find("start").text), int(c.find("end").text))
             for c in clips]
    assert spans[0][0] == 0 and spans[-1][1] == 500
    for (a, b), (c2, d) in zip(spans, spans[1:]):
        assert b == c2, f"片段斷裂:{b} != {c2}"
    print("  ✓ 視訊軌完整覆蓋,無斷裂")


def test_labels():
    """標籤色:靜音=Rose、音樂=Caribbean、冗詞=Violet、語音無標籤"""
    root, _ = _export()
    clips = root.findall(".//video/track/clipitem")
    def label(c):
        el = c.find("labels/label2")
        return None if el is None else el.text
    assert label(clips[0]) is None
    assert label(clips[1]) == "Rose"
    assert label(clips[2]) == "Caribbean"
    assert label(clips[3]) == "Violet"
    assert label(clips[4]) is None
    # 音訊片段也要有一樣的標籤
    atracks = root.findall(".//media/audio/track")
    assert len(atracks) == 2
    for tr in atracks:
        aclips = tr.findall("clipitem")
        assert len(aclips) == 5
        assert label(aclips[1]) == "Rose" and label(aclips[2]) == "Caribbean"
    print("  ✓ 標籤色正確(視訊軌 + 兩條音訊軌)")


def test_markers():
    """marker:音樂段有、低信心冗詞有、靜音段沒有"""
    root, _ = _export()
    markers = root.findall(".//sequence/marker")
    names = [m.find("name").text for m in markers]
    ins = [int(m.find("in").text) for m in markers]
    assert any("音樂" in n for n in names)
    assert any("冗詞" in n and "嗯" in n for n in names)
    assert not any("靜音" in n for n in names)
    assert 200 in ins and 350 in ins            # 音樂、冗詞的起點
    print("  ✓ marker 正確(音樂+低信心冗詞,靜音不下)")


def test_links_and_file():
    """連結對得上、file 只完整定義一次、路徑用斜線"""
    root, tl = _export()
    n = len(tl.segments)
    vclips = root.findall(".//video/track/clipitem")
    # 第一個視訊片段的連結:自己 + 兩條音訊軌對應片段
    links = vclips[0].findall("link/linkclipref")
    assert [l.text for l in links] == \
        ["clipitem-1", f"clipitem-{n + 1}", f"clipitem-{2 * n + 1}"]
    files = root.findall(".//file")
    full = [f for f in files if f.find("pathurl") is not None]
    assert len(full) == 1
    assert "\\" not in full[0].find("pathurl").text
    print("  ✓ 片段連結與 file 定義正確")


def test_ntsc():
    """29.97fps -> timebase 30 + ntsc TRUE"""
    root, _ = _export(fps=29.97)
    r = root.find(".//sequence/rate")
    assert r.find("timebase").text == "30"
    assert r.find("ntsc").text == "TRUE"
    root30, _ = _export(fps=30.0)
    assert root30.find(".//sequence/rate/ntsc").text == "FALSE"
    print("  ✓ NTSC 幀率正確處理")


if __name__ == "__main__":
    print("執行活專案 XML 測試...")
    test_all_clips_uncut()
    test_coverage_continuous()
    test_labels()
    test_markers()
    test_links_and_file()
    test_ntsc()
    print("\n全部通過 ✓  活專案 XML 產生器正確。")
