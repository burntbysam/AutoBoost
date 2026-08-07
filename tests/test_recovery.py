"""Tests for the recovery dialog-button policy (0.7.19).

When a part fails mid-cycle, closing its unsaved Design/Cut window makes Boost
pop a "Save changes?" prompt. The old recovery ignored it; it was left up and
blocked the next part, cascading a whole run (the 0.7.18 logs). Recovery now
dismisses stray prompts, but must only ever click a SAFE button -- never one
that would save a bad part, confirm ("Yes"), or exit Boost.

_choose_dialog_button is the pure decision behind that: given the button labels
on a dialog it returns the label to click, or None to fall back to Esc. It needs
no live window, so we can lock the policy down here.

Runnable with pytest or directly:  python -m tests.test_recovery
"""

from __future__ import annotations

from autoboost.navigator.boost_uia import _button_is_safe, _choose_dialog_button

# Words that must never appear in a button we click (save a bad part / confirm /
# close Boost), with the sole exception of "Don't Save" / "Do not save".
_FORBIDDEN = ("save", "yes", "exit", "quit")


def _clicked_is_never_dangerous(labels):
    """The chosen label (if any) is never a dangerous one."""
    choice = _choose_dialog_button(labels)
    if choice is None:
        return True
    if choice.startswith("don't") or choice.startswith("do not"):
        return True
    return not any(bad in choice for bad in _FORBIDDEN)


def test_unsaved_close_prompt_discards():
    # The exact cascade case: closing an unsaved part -> Save changes?
    assert _choose_dialog_button(["Save", "Don't Save", "Cancel"]) == "don't save"


def test_yes_no_prompt_picks_no():
    assert _choose_dialog_button(["Yes", "No"]) == "no"
    assert _choose_dialog_button(["Yes", "No", "Cancel"]) == "no"


def test_error_box_ok():
    assert _choose_dialog_button(["OK"]) == "ok"


def test_discard_preferred_over_cancel():
    assert _choose_dialog_button(["Discard", "Cancel"]) == "discard"


def test_save_and_close_trap_is_avoided():
    # "Save and Close" ends in "close" but WOULD SAVE -- must not be clicked; the
    # safe Cancel is taken instead.
    assert _choose_dialog_button(["Save and Close", "Cancel"]) == "cancel"


def test_only_dangerous_buttons_falls_through_to_esc():
    # Nothing safe on offer -> None, so recovery uses Esc.
    assert _choose_dialog_button(["Save", "Yes"]) is None
    assert _choose_dialog_button(["Exit"]) is None


def test_exit_prompt_never_exits():
    assert _choose_dialog_button(["Exit", "Cancel"]) == "cancel"
    # A quit/exit-only dialog must never be auto-confirmed.
    assert _choose_dialog_button(["Quit"]) is None


def test_normalization_case_and_dots():
    assert _choose_dialog_button(["OK..."]) == "ok"
    assert _choose_dialog_button(["  CANCEL  "]) == "cancel"
    assert _choose_dialog_button(["DO NOT SAVE", "Cancel"]) == "do not save"


def test_empty_and_blank():
    assert _choose_dialog_button([]) is None
    assert _choose_dialog_button(["", "   "]) is None


def test_button_is_safe_unit():
    assert _button_is_safe("don't save")
    assert _button_is_safe("do not save")
    assert _button_is_safe("no")
    assert _button_is_safe("cancel")
    assert _button_is_safe("ok")
    assert not _button_is_safe("save")
    assert not _button_is_safe("save and close")
    assert not _button_is_safe("yes")
    assert not _button_is_safe("exit")
    assert not _button_is_safe("quit")


def test_never_clicks_anything_dangerous_across_cases():
    cases = [
        ["Save", "Don't Save", "Cancel"],
        ["Yes", "No", "Cancel"],
        ["Save and Close", "Cancel"],
        ["Save", "Yes"],
        ["Exit"],
        ["Quit", "Cancel"],
        ["OK"],
        [],
    ]
    for labels in cases:
        assert _clicked_is_never_dangerous(labels), labels


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
