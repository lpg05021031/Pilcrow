"""Port of test/unit/format.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.fmt import (
    basename,
    bytes_,
    cost,
    dirname,
    duration,
    err_text,
    lang_from_path,
    rel_path,
    set_home,
    short_model,
    tildify,
    time_ago,
    tokens,
    truncate,
)

NOW = 1_700_000_000_000


# --- timeAgo


def test_time_ago_short_and_long_forms():
    assert time_ago(NOW - 10_000, NOW) == "just now"
    assert time_ago(NOW - 10_000, NOW, True) == "now"
    assert time_ago(NOW - 5 * 60_000, NOW) == "5m ago"
    assert time_ago(NOW - 5 * 60_000, NOW, True) == "5m"
    assert time_ago(NOW - 3 * 3600_000, NOW, True) == "3h"
    assert time_ago(NOW - 2 * 86400_000, NOW, True) == "2d"


def test_time_ago_empty_for_no_time():
    assert time_ago(None, NOW) == ""


# --- duration


def test_duration_rounds_once_so_119_6s_is_2m_not_1m_60s():
    assert duration(119_600) == "2m"
    assert duration(0) == "0s"
    assert duration(1234) == "1.2s"
    assert duration(45_000) == "45s"
    assert duration(61_000) == "1m 1s"
    assert duration(3600_000) == "1h"
    assert duration(3660_000) == "1h 1m"


# --- tokens / cost / bytes


def test_tokens():
    assert tokens(0) == "0"
    assert tokens(999) == "999"
    assert tokens(1500) == "1.5k"
    assert tokens(25_000) == "25k"
    assert tokens(1_500_000) == "1.50M"
    assert tokens(12_000_000) == "12.0M"


def test_cost():
    assert cost(0) == "$0.00"
    assert cost(0.001) == "<$0.01"
    assert cost(1.234) == "$1.23"
    assert cost(12.34) == "$12.3"


def test_bytes():
    assert bytes_(10) == "10 B"
    assert bytes_(2048) == "2.0 KB"
    assert bytes_(3 * 1024 * 1024) == "3.0 MB"


# --- paths


def test_basename_and_dirname_posix_and_windows():
    assert basename("/a/b/c.ts") == "c.ts"
    assert basename("C:\\Users\\me\\x.txt") == "x.txt"
    assert basename("/a/b/") == "b"
    assert dirname("/a/b/c.ts") == "/a/b"
    assert dirname("C:/x.txt") == "C:/"
    assert dirname("file") == "/"


def test_tildify_compares_drive_paths_without_case():
    assert tildify("/home/me/p/x", "/home/me") == "~/p/x"
    assert tildify("/home/me", "/home/me") == "~"
    assert tildify("c:/users/me/p", "C:\\Users\\me") == "~/p"
    assert tildify("/other", "/home/me") == "/other"


def test_rel_path_inside_a_base_else_tilde():
    set_home("/home/me")
    try:
        assert rel_path("/home/me/proj/src/a.ts", "/home/me/proj") == "src/a.ts"
        assert rel_path("/home/me/proj", "/home/me/proj") == "."
        assert rel_path("/home/me/other/a.ts", "/home/me/proj") == "~/other/a.ts"
    finally:
        set_home("")


# --- text helpers


def test_truncate_with_an_ellipsis():
    assert truncate("hello world", 6) == "hello…"
    assert truncate("hi", 6) == "hi"


def test_short_model():
    assert short_model("claude-opus-4-1-20250805") == "Opus 4.1"
    assert short_model("claude-sonnet-4-20250514") == "Sonnet 4"
    assert short_model("gpt-x") == "Gpt-x"
    assert short_model(None) == "Default"


def test_lang_from_path():
    assert lang_from_path("a/b/Main.TSX") == "tsx"
    assert lang_from_path("Dockerfile") == "dockerfile"
    assert lang_from_path("top.sv") == "system-verilog"
    assert lang_from_path("noext") == "text"


def test_err_text_reads_any_thrown_shape():
    assert err_text(Exception("boom")) == "boom"
    assert err_text("plain") == "plain"
    assert err_text({"message": "obj"}) == "obj"
    assert err_text(42) == "42"
