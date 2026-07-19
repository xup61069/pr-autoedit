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

from modules.audio_clean import vst_state_path


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


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "open":
        sys.exit(open_editor(sys.argv[2]))
    print('用法:python vst_tool.py open "<.vst3 完整路徑>"', file=sys.stderr)
    sys.exit(1)
