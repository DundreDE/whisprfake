from whisprfake.config import Shortcuts
from whisprfake.input.fsm import Action, HotkeyFSM


def fsm():
    return HotkeyFSM.from_config(Shortcuts(hands_free=["CTRL+SUPER+SPACE"]))


def acts(emits):
    return [e.action for e in emits]


def press(f, keys, t):
    out = []
    for k in keys:
        out += f.key(k, True, t)
    return out


def release(f, keys, t):
    out = []
    for k in keys:
        out += f.key(k, False, t)
    return out


def test_push_to_talk_hold_and_release():
    f = fsm()
    assert acts(f.key("KEY_LEFTCTRL", True, 0.0)) == [Action.PREWARM]
    e = f.key("KEY_LEFTMETA", True, 0.01)
    assert acts(e) == [Action.START] and e[0].mode == "dictate"
    assert acts(f.tick(0.1)) == []
    assert acts(f.tick(0.3)) == [Action.CONFIRM]
    assert acts(release(f, ["KEY_LEFTMETA", "KEY_LEFTCTRL"], 2.0)) == [Action.STOP]


def test_compositor_shortcut_is_discarded_silently():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    assert acts(f.key("KEY_X", True, 0.1)) == [Action.DISCARD]
    # releasing everything must not start or stop anything
    assert acts(release(f, ["KEY_X", "KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.2)) == []
    assert acts(f.tick(1.0)) == []


def test_double_tap_locks_hands_free_then_chord_stops():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    assert acts(release(f, ["KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.1)) == []
    e = press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.3)
    assert Action.LOCK in acts(e)
    assert acts(release(f, ["KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.4)) == []
    # typing while hands-free is ignored
    assert acts(press(f, ["KEY_A"], 1.0) + release(f, ["KEY_A"], 1.1)) == []
    assert acts(press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 5.0)) == [Action.STOP]


def test_single_tap_is_discarded_after_window():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    release(f, ["KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.1)
    assert acts(f.tick(0.2)) == []
    assert acts(f.tick(0.7)) == [Action.DISCARD]


def test_hands_free_chord_from_idle():
    f = fsm()
    e = press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA", "KEY_SPACE"], 0.0)
    assert acts(e)[-1] == Action.LOCK
    release(f, ["KEY_SPACE", "KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.2)
    assert acts(press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 3.0)) == [Action.STOP]


def test_space_while_holding_locks():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    f.tick(0.3)
    assert acts(f.key("KEY_SPACE", True, 0.5)) == [Action.LOCK]
    assert acts(release(f, ["KEY_SPACE", "KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.6)) == []


def test_alt_upgrades_to_command_and_command_chord_starts_command():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    assert acts(f.key("KEY_LEFTALT", True, 0.1)) == [Action.UPGRADE_COMMAND]
    f.tick(0.4)
    assert acts(release(f, ["KEY_LEFTALT", "KEY_LEFTMETA", "KEY_LEFTCTRL"], 1.5)) == [Action.STOP]
    g = fsm()
    e = press(g, ["KEY_LEFTALT", "KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    assert e[-1].action == Action.START and e[-1].mode == "command"


def test_escape_cancels():
    f = fsm()
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    f.tick(0.3)
    assert acts(f.key("KEY_ESC", True, 0.5)) == [Action.CANCEL]
    g = fsm()
    press(g, ["KEY_LEFTCTRL", "KEY_LEFTMETA", "KEY_SPACE"], 0.0)
    release(g, ["KEY_SPACE", "KEY_LEFTMETA", "KEY_LEFTCTRL"], 0.1)
    assert acts(g.key("KEY_ESC", True, 2.0)) == [Action.CANCEL]


def test_paste_last():
    f = fsm()
    e = press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA", "KEY_LEFTALT", "KEY_V"], 0.0)
    assert Action.PASTE_LAST in acts(e)


def test_right_side_modifiers_work():
    f = fsm()
    e = press(f, ["KEY_RIGHTCTRL", "KEY_RIGHTMETA"], 0.0)
    assert acts(e)[-1] == Action.START


def test_default_has_no_space_chord_so_omarchy_background_switcher_is_untouched():
    f = HotkeyFSM.from_config(Shortcuts())
    press(f, ["KEY_LEFTCTRL", "KEY_LEFTMETA"], 0.0)
    assert acts(f.key("KEY_SPACE", True, 0.05)) == [Action.DISCARD]
