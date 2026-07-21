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
    """涵蓋決策引擎會產出的「每一種」段落。

    ⚠️ 決策引擎新增段落種類時,這裡一定要跟著加。以前這份假資料只有
    語音/靜音/音樂/冗詞四種,所以 silence_motion(畫面在動的示範段)與
    noise(咳嗽)在 premiere_xml 掉進「當成語音」的分支、完全沒有標籤色,
    測試卻一路全綠——漏的是「測試沒餵過的那一種」,不是邏輯。"""
    segs = [
        Segment(0, 100, "keep"),                                       # 語音
        Segment(100, 200, "speed", factor=6.0, reason="silence",
                confidence=0.95),                                      # 靜音
        Segment(200, 260, "speed", factor=6.0, reason="silence_motion",
                confidence=0.95),                    # 沒講話但畫面在動(示範)
        Segment(260, 280, "delete", reason="noise", confidence=0.85),  # 咳嗽
        Segment(280, 350, "keep", reason="music", confidence=0.8),     # 音樂
        Segment(350, 360, "delete", reason="filler", text="嗯",
                confidence=0.6),                                       # 冗詞
        Segment(360, 420, "delete", reason="retake", text="我們按這",
                confidence=0.5),                                       # 重講
        Segment(420, 500, "keep"),                                     # 語音
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
    assert spans[0][0] == 0 and spans[-1][1] == 500, f"覆蓋範圍不對:{spans}"
    for (a, b), (c2, d) in zip(spans, spans[1:]):
        assert b == c2, f"片段斷裂:{b} != {c2}"
    print("  ✓ 視訊軌完整覆蓋,無斷裂")


def _label(c):
    el = c.find("labels/label2")
    return None if el is None else el.text


def test_labels():
    """每一種段落都要有自己的顏色,只有真的語音才沒有標籤。

    「沒有標籤」在 Premiere 裡等於「這是正常講話」,所以任何被誤判成語音的
    段落都會從批次處理裡消失,而且畫面上看不出來——這就是 silence_motion
    與 noise 之前發生的事。這裡逐一釘住每一種的顏色。"""
    root, tl = _export()
    clips = root.findall(".//video/track/clipitem")
    expect = [None, "Rose", "Lavender", "Yellow", "Caribbean", "Violet",
              "Mango", None]
    assert [_label(c) for c in clips] == expect, \
        f"標籤色不對:{[_label(c) for c in clips]}"
    # 音訊片段也要有一樣的標籤(不然只選得到畫面、選不到聲音)
    atracks = root.findall(".//media/audio/track")
    assert len(atracks) == 2
    for tr in atracks:
        aclips = tr.findall("clipitem")
        assert len(aclips) == len(tl.segments)
        assert [_label(c) for c in aclips] == expect
    print("  ✓ 每一種段落的標籤色都正確(視訊軌 + 兩條音訊軌)")


def test_every_reason_has_a_label():
    """決策引擎產得出來的每一個 reason,這裡都要認得。

    這是上面那個測試的「防未來」版本:新增一種 reason 卻忘了在
    premiere_xml 補對應的顏色與名稱時,這裡會當場紅掉,
    而不是等到你在 Premiere 裡發現有一堆片段沒有顏色。"""
    from modules.premiere_xml import _KIND_BY_REASON, _LABELS, _CLIP_NAMES
    # 決策引擎目前會寫進 Segment.reason 的全部值(空字串 = 一般語音)
    produced = {"", "silence", "silence_motion", "noise", "music",
                "filler", "retake"}
    for r in produced:
        if r == "":
            continue
        assert r in _KIND_BY_REASON, f"reason「{r}」沒有對應的段落種類"
        kind = _KIND_BY_REASON[r]
        assert _LABELS.get(kind), f"段落種類「{kind}」沒有標籤色,會被當成語音"
        assert kind in _CLIP_NAMES, f"段落種類「{kind}」沒有中文名,產 XML 會當掉"
    print(f"  ✓ {len(produced) - 1} 種 reason 都有標籤色與中文名")


def test_clip_names_are_distinguishable():
    """片段名稱要看得出是哪一種、以及有多長 —— 那是「要不要留」的判斷依據"""
    root, _ = _export()
    names = [c.find("name").text
             for c in root.findall(".//video/track/clipitem")]
    assert names[2].startswith("示範") and "s" in names[2], f"示範段名稱:{names[2]}"
    assert names[3].startswith("雜音"), f"雜音段名稱:{names[3]}"
    assert names[5] == "冗詞 嗯" and names[6] == "重講"
    print("  ✓ 片段名稱看得出種類與長度")


def test_markers():
    """marker:音樂段有、低信心冗詞有、重講有;靜音/示範/雜音靠標籤色就夠,不下。

    marker 是「請你逐一確認」的意思,下太多就沒人看了。靜音、示範、雜音
    在時間軸上是一整片顏色,掃一眼就看得完,不需要一個一個跳。"""
    root, _ = _export()
    markers = root.findall(".//sequence/marker")
    names = [m.find("name").text for m in markers]
    ins = [int(m.find("in").text) for m in markers]
    assert any("音樂" in n for n in names)
    assert any("冗詞" in n and "嗯" in n for n in names)
    assert any("重講" in n for n in names)
    for skip in ("靜音", "示範", "雜音"):
        assert not any(skip in n for n in names), f"{skip}段不該下 marker"
    assert 280 in ins and 350 in ins and 360 in ins   # 音樂、冗詞、重講的起點
    print("  ✓ marker 正確(音樂+冗詞+重講;靜音/示範/雜音靠標籤色)")


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
    test_every_reason_has_a_label()
    test_clip_names_are_distinguishable()
    test_markers()
    test_links_and_file()
    test_ntsc()
    print("\n全部通過 ✓  活專案 XML 產生器正確。")
