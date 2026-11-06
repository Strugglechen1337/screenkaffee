import pytest
from screenkaffee.hotkeys import parse_hotkey


def test_parse():
    assert parse_hotkey("Print") == (0, 0x2C)
    assert parse_hotkey("Ctrl+Shift+Print") == (6, 0x2C)
    assert parse_hotkey("Alt+F4") == (1, 0x73)
    assert parse_hotkey("Ctrl+S") == (2, ord("S"))
    for bad in ["", "Ctrl+", "A+B", "Ctrl+Blub"]:
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_print_hook_repeat_release_and_modifiers(qt_app):
    from screenkaffee.hotkeys import WindowsHotkeys
    calls = []
    keys = WindowsHotkeys({"region": lambda: calls.append("capture")})
    keys.print_action = "region"
    assert not keys.print_event(0x100, modifiers=2)
    assert keys.print_event(0x100)
    keys.print_event(0x100)
    keys.print_event(0x101)
    keys.deliver_pending()
    assert calls == ["capture"]
    keys.print_event(0x101)
    keys.deliver_pending()
    assert calls == ["capture", "capture"]
    keys.close()
    assert not keys.print_event(0x100)
