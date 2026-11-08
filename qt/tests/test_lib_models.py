"""lib/models.py: the short name of a model value and the picker's options (lib/models.ts)."""

from __future__ import annotations

from pilcrow_qt.lib.models import ALIASES, model_label, model_options, trim_claude


def test_model_label_and_options():
    gw = [{"id": "claude-opus-5-5", "displayName": "Claude Opus 5.5 (1M context)"}, {"id": "local-x"}]
    assert trim_claude("Claude Opus 5.5") == "Opus 5.5"
    assert model_label("claude-opus-5-5", gw) == "Opus 5.5 (1M context)"
    assert model_label("sonnet", gw) == "Sonnet" and model_label("sonnet", gw, []) == "sonnet"
    assert model_label("claude-haiku-4-5", []) == "Haiku 4.5" and model_label("gpt-x", []) == "gpt-x"
    assert [a["value"] for a in ALIASES] == ["opus", "sonnet", "haiku"]
    opts = model_options(gw)
    assert opts[0]["value"] == "" and opts[0]["label"] == "Default"
    assert opts[1] == {
        "value": "claude-opus-5-5",
        "label": "Opus 5.5 (1M context)",
        "hint": "claude-opus-5-5",
        "mono": True,
    }
    assert opts[2]["label"] == "local-x" and opts[2]["hint"] == ""
