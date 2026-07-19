"""
打開一個 VST 外掛的介面讓你調參數,關掉視窗後把參數存起來。
之後每次剪輯就會自動套用你調好的參數。

  python vst_tool.py open "C:\\...\\VoiceFX.vst3\\Contents\\x86_64-win\\VoiceFX.vst3"

面板的「開啟調整」按鈕會呼叫這支。需要有 GUI 桌面環境。
"""
from __future__ import annotations
import sys, os

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from modules.audio_clean import vst_state_path, _suppress_native_output


def open_editor(vst_path: str) -> int:
    if not os.path.exists(vst_path):
        print(f"找不到外掛檔:{vst_path}")
        return 1

    try:
        from pedalboard import load_plugin
    except ImportError:
        print("尚未安裝 pedalboard(pip install pedalboard)")
        return 1

    print(f"載入外掛:{vst_path}")
    try:
        plugin = load_plugin(vst_path)
    except Exception as e:
        print(f"外掛載入失敗:{e}")
        return 1

    # 若已有調好的狀態,先套上去,讓你接續調整
    sp = vst_state_path(vst_path)
    if os.path.exists(sp):
        try:
            with open(sp, "rb") as f:
                plugin.raw_state = f.read()
            print("已載入上次調好的參數。")
        except Exception:
            pass

    if not getattr(plugin, "has_editor", False):
        print("這個外掛沒有可視介面,無法用視窗調整。")
        return 2

    print("開啟外掛介面…調整完直接關閉那個視窗,就會自動儲存。")
    try:
        plugin.show_editor()          # 阻塞,直到使用者關閉視窗
    except Exception as e:
        print(f"開啟介面失敗:{e}")
        return 1

    try:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "wb") as f:
            f.write(plugin.raw_state)
        print("✓ 已儲存外掛參數,下次剪輯會自動套用。")
    except Exception as e:
        print(f"儲存參數失敗:{e}")
        return 1
    return 0


def caps(vst_path: str) -> int:
    """回報這個外掛的能力給面板(單行 JSON):有沒有可視介面、名稱。
    面板用這個決定要不要顯示『調整』按鈕——沒有視窗介面的外掛(例如 VoiceFX)
    就不顯示,改用面板上的降噪滑條。"""
    import json
    info = {"has_editor": False, "name": None, "ok": False}
    if os.path.exists(vst_path):
        try:
            from pedalboard import load_plugin
            import gc
            # 載入/回收外掛時底層會狂吐除錯訊息,整段壓掉才不會污染要給面板讀的 JSON
            with _suppress_native_output():
                pl = load_plugin(vst_path)
                info["has_editor"] = bool(getattr(pl, "has_editor", False))
                info["name"] = getattr(pl, "name", None)
                info["ok"] = True
                del pl
                gc.collect()
        except Exception:
            pass
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "open":
        sys.exit(open_editor(sys.argv[2]))
    if len(sys.argv) >= 3 and sys.argv[1] == "caps":
        sys.exit(caps(sys.argv[2]))
    print('用法:python vst_tool.py open|caps "<.vst3 完整路徑>"', file=sys.stderr)
    sys.exit(1)
