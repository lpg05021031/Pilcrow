"""確定性層的 pytest 鎖 —— 承重牆不能只靠系統級 eval 保護。
全部離線、零 LLM、零網路；harvest 的 ADO 薄封裝天生是 stub 點。
跑法：venv/bin/python -m pytest -q tests/
"""
import contextlib
import json
import pathlib
import tempfile

import asyncio
import types

import pytest

from orchestrator.anatomy import parse_diff


# ---------- anatomy ----------

def test_anatomy_line_numbering_and_added_set():
    diff = ("--- a/src/A.java\n+++ b/src/A.java\n@@ -10,4 +10,5 @@\n"
            " ctx\n ctx\n+added\n ctx\n ctx\n")
    ana = parse_diff(diff)
    fc = ana.files[0]
    assert fc.added_linenos == {12}
    assert [(t, n) for h in fc.hunks for t, n, _ in h.lines][2] == ("+", 12)


# ---------- diff 幾何 predicates（住 anatomy，pipeline / knowledge 共用） ----------

def test_match_path_segment_boundary():
    from orchestrator.anatomy import match_path
    assert match_path("src/cache/SessionCache.java", "cache/SessionCache.java")
    assert not match_path("src/cache/SessionCache.java", "Cache.java")


def test_line_near_vs_line_in_view():
    from orchestrator.anatomy import line_in_view, line_near
    diff = ("--- a/src/A.java\n+++ b/src/A.java\n@@ -10,7 +10,8 @@\n"
            " c10\n c11\n+a12\n c13\n c14\n c15\n c16\n c17\n")
    ana = parse_diff(diff)
    assert line_near(ana, "src/A.java", 13)           # added±2
    assert not line_near(ana, "src/A.java", 16)
    assert line_in_view(ana, "src/A.java", 16)         # context 行在視野


def test_slices_budget_reports_skipped(monkeypatch):
    from orchestrator import pipeline as pl
    monkeypatch.setenv("REVIEWER_SLICES_MAX_CHARS", "800")
    diff = "\n".join(
        f"--- /dev/null\n+++ b/src/f{i}.java\n@@ -0,0 +1,6 @@\n"
        + "\n".join(f"+line{k} of file {i} with some padding text" for k in range(6))
        for i in range(6)) + "\n"

    class D:
        files: list = []
        name = "X"
        focus = ""
        hypotheses: list = []

    text, skipped = pl._slices_for(D(), parse_diff(diff))
    assert skipped and "omitted" in text


# ---------- static：MISSING-TEST 保底跨語言 ----------

@pytest.mark.parametrize("fname,decl", [
    ("src/pkg/handler.go", "func HandlePing(w http.ResponseWriter) {"),
    ("src/app/Service.kt", "fun computeTotal(x: Int): Int {"),
    ("src/app/Svc.java", "public int run(int x) {"),
])
def test_missing_test_fires_per_language(fname, decl, monkeypatch):
    monkeypatch.setenv("STATIC_MISSING_TEST", "true")
    from orchestrator.static_pass import _missing_test
    diff = (f"--- /dev/null\n+++ b/{fname}\n@@ -0,0 +1,3 @@\n"
            f"+package x\n+{decl}\n+}}\n")
    finds = _missing_test(parse_diff(diff))
    assert finds and finds[0].rule == "MISSING-TEST"


# ---------- knowledge：K1 閘門 ----------

@pytest.fixture()
def kroot(monkeypatch):
    root = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_BRANCHES", "release/*,master")
    monkeypatch.setenv("KNOWLEDGE_CYCLE_SOURCE", "release/*")
    monkeypatch.setenv("KNOWLEDGE_CYCLE_TARGET", "master")
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "false")   # 測試零 LLM：顧問分析關
    return root


def test_gate_matrix(kroot):
    from orchestrator.knowledge import gate
    ok, _ = gate({"target_branch": "refs/heads/release/2026.09",
                  "source_branch": "refs/heads/feature/x",
                  "title": "fix NPE"}, ["/src/A.java"])
    assert ok
    assert not gate({"target_branch": "refs/heads/feature/y",
                     "source_branch": "refs/heads/z", "title": "t"}, ["/a"])[0]
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/x",
                     "title": "t"}, ["/pom.xml"])[0]
    assert gate({"target_branch": "refs/heads/master",
                 "source_branch": "refs/heads/x",
                 "title": 'Revert "x"'}, ["/pom.xml"])[0]
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/release/2026.09",
                     "title": "Release"}, ["/src/A.java"])[0]   # 驗收 PR 豁免
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/x",
                     "title": "Merge master into release/1.2"},
                    ["/src/A.java"])[0]                          # ADO merge-sync


# ---------- harvest：落地累積、未知段前滾、reject 邊界、watermark ----------

def _draft(entity, **kw):
    d = {"summary": "s", "type": "bugfix", "intent_stated": True,
         "primary_entity": entity, "decisions": [], "invariants": [],
         "affected": [], "supersedes": [], "ledger_line": "l"}
    d.update(kw)
    return d


def test_land_accumulates_and_keeps_unknown_sections(kroot):
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 55,
            _draft("src/a/cache/S.java", invariants=["inv-A"]),
            "cycle/silent", "")
    page = kroot / "repo-a" / "cache" / "S.md"
    page.write_text(page.read_text() + "\n## Ops runbook\n- 手工補的一段\n")
    H._land(kroot, "repo-a", 56,
            _draft("src/a/cache/S.java", invariants=["inv-B"]),
            "cycle/silent", "")
    body = page.read_text()
    assert "inv-A" in body and "inv-B" in body
    assert "Ops runbook" in body and "手工補的一段" in body
    assert "| PR#55 |" in body and "| PR#56 |" in body


def test_apply_rejects_boundary_and_ambiguity(kroot):
    from orchestrator import knowledge_harvest as H
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    for pid, ent in ((70, "src/x/FeeCalc.java"), (71, "src/y/FeeCalc.java")):
        (cyc / f"pr-{pid}.json").write_text(
            json.dumps({"draft": {"primary_entity": ent}}))
    staged = sorted(cyc.glob("*.json"))
    msgs, amb = H._apply_rejects({"eecalc.java"}, staged, "t")
    assert not msgs and not amb                       # 段邊界：不吸子字串
    msgs, amb = H._apply_rejects({"feecalc"}, staged, "t")
    assert amb and len(list(cyc.glob("*.json"))) == 2  # 歧義：全保留
    msgs, _ = H._apply_rejects({"x/feecalc.java"}, staged, "t")
    assert any("丟" in m for m in msgs)


def test_watermark_first_sight_no_backfill(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 90, "title": "ancient",
        "sourceRefName": "refs/heads/f", "targetRefName": "refs/heads/master",
        "closedDate": "2020-01-01T00:00:00Z"}])
    lines = H._watermark_scan(object(), kroot)
    assert any("首見" in x for x in lines)
    assert not (kroot / "repo-a" / "_module" / "unreviewed-ledger.md").exists()
    assert H._watermark_scan(object(), kroot) == []


def test_digest_state_merge_keeps_ambig(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    (cyc / "pr-70.json").write_text(json.dumps({
        "pr_url": "u", "pr_id": 70, "repo": "repo-a",
        "base_url": "https://x/C/P",
        "draft": _draft("src/x/F.java"), "author_note": "",
        "provenance": "silent"}))
    posted = []
    monkeypatch.setattr(H, "client",
                        lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: posted.append(t))
    monkeypatch.setattr(H, "_list_active", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master"}])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    state_f = kroot / "_state" / "cycle.json"
    state_f.write_text(json.dumps({"repo-a#500": {"ambig": ["feecalc"]}}))
    H._cycle_close(object(), kroot)
    st = json.loads(state_f.read_text())["repo-a#500"]
    assert st.get("digest") is True and st.get("ambig") == ["feecalc"]


# ---------- app：快取 commit 語意、meta 盾 ----------

def test_cache_commit_invalidation():
    from orchestrator import app as A
    A._RECENT.clear()
    A._cache_put("u1", "old-review", "sha-A")
    assert A._cache_get("u1", "sha-A") == "old-review"
    assert A._cache_get("u1", "sha-B") is None         # push 新 commit → 失效
    assert A._cache_get("u1", "") == "old-review"      # 無 hint → TTL 語意


def test_meta_shield_not_tripped_by_json_format():
    from orchestrator import app as A
    assert not A._is_meta_task({}, "please give me the json format of X")
    assert A._is_meta_task({"metadata": {"task": "title_generation"}}, "x")


# ---------- 考卷帳目：檔數 ≡ CASES ≡ 文件宣稱（防人腦算術） ----------

def test_fixture_count_matches_cases_and_readme():
    from eval.seed_langfuse import CASES, DISTILL_CASES, KNOWLEDGE_CASES
    fixtures = pathlib.Path(__file__).parent.parent / "eval" / "fixtures"
    diffs = sorted(f.name for f in fixtures.glob("*.diff"))
    assert len(diffs) == len(CASES) == 30
    case_files = {c[0] for c in CASES}
    assert case_files == set(diffs)          # 每題有檔、每檔有題
    readme = (pathlib.Path(__file__).parent.parent / "README.md").read_text(
        encoding="utf-8")
    assert "30 題 golden set" in readme      # 文件跟考卷同一個數

    # 知識依賴題：每題有 diff、有種子帳本目錄（少一個就退化成無知識對照組）
    kdiffs = sorted(f.name for f in (fixtures / "knowledge_cases").glob("*.diff"))
    assert len(kdiffs) == len(KNOWLEDGE_CASES) == 5
    assert {c[0] for c in KNOWLEDGE_CASES} == set(kdiffs)
    for _fname, repo, *_rest in KNOWLEDGE_CASES:
        assert (fixtures / "knowledge" / repo).is_dir()
    assert sum(1 for c in KNOWLEDGE_CASES if c[5]["ktype"] == "single-page") == 3
    assert sum(1 for c in KNOWLEDGE_CASES if c[5]["ktype"] == "distractor") == 2
    assert "5 題知識依賴題" in readme

    # 蒸餾考卷：10 題，其中至少三題是陷阱
    ddiffs = sorted(f.name for f in (fixtures / "distill").glob("*.diff"))
    assert len(ddiffs) == len(DISTILL_CASES) == 10
    assert {c[0] for c in DISTILL_CASES} == set(ddiffs)
    traps = [c for c in DISTILL_CASES if c[5]["focus"].startswith("TRAP")]
    assert len(traps) >= 3
    assert "10 題蒸餾考卷" in readme


def test_cycle_isolation_between_parallel_release_branches(kroot, monkeypatch):
    """release/N 收口只落地自己那批；release/N+1 的暫存稿留在池裡等自己的
    驗收 PR；無 target_branch 標記的舊稿視為萬用（升級不擱淺）。"""
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見（見水位測試）
        json.dumps({"repo-a#1": {"closed": True}}))
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    for pid, ent, tb in ((70, "src/a/A.java", "release/2026.09"),
                         (71, "src/b/B.java", "release/2026.10"),
                         (72, "src/c/C.java", "")):          # legacy 無標記
        (cyc / f"pr-{pid}.json").write_text(json.dumps({
            "pr_url": "u", "pr_id": pid, "repo": "repo-a",
            "base_url": "https://x/C/P", "target_branch": tb,
            "draft": _draft(ent), "author_note": "", "provenance": "silent"}))
    monkeypatch.setattr(H, "client",
                        lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t2: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500,
        "sourceRefName": "refs/heads/release/2026.09",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])
    H._cycle_close(object(), kroot)
    assert (kroot / "repo-a" / "a" / "A.md").exists()        # 本週期 → 落地
    assert (kroot / "repo-a" / "c" / "C.md").exists()        # 萬用 → 落地
    assert not (kroot / "repo-a" / "b" / "B.md").exists()    # 下週期 → 留池
    assert [f.name for f in cyc.glob("*.json")] == ["pr-71.json"]


# ---------- 串流路徑端到端（同族 NameError 兩次得逞的共同根因＝這裡沒測） ----------

def test_stream_review_delivers_markdown_and_caches_commit(monkeypatch):
    from orchestrator import app as A
    A._RECENT.clear()

    async def f_meta(u):
        return {"repo": "r", "title": "t", "source_commit": "sha-X",
                "target_branch": "refs/heads/feature/x"}

    async def f_diff(u):
        return "--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"

    async def f_run(diff, md):
        return types.SimpleNamespace(markdown="## Pull Request Review OK",
                                     stats={"file_table": [],
                                            "graph_impacted": []})

    async def f_distill(*a, **k):
        return None

    monkeypatch.setattr(A.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(A.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(A, "run_review", f_run)
    monkeypatch.setattr(A.knowledge, "distill_and_stage", f_distill)
    monkeypatch.setenv("AUTO_POST", "false")

    async def consume():
        chunks = []
        async for c in A._stream_review("rid", "https://x/C/P/_git/R/pullrequest/9",
                                        "u", commit_hint="sha-X"):
            chunks.append(c)
        return "".join(chunks)

    out = asyncio.run(consume())
    assert "Pull Request Review OK" in out
    assert "❌" not in out                    # 全程無錯誤演出
    hit = A._RECENT["https://x/C/P/_git/R/pullrequest/9"]
    assert len(hit) == 3 and hit[2] == "sha-X"


# ---------- F 批：synthesis / coverage gate / verdict / scorer / cache 清理 ----------

def _fnd(rule="BUG", file="src/A.java", line=12, sev="warning",
         src="llm", conf=0.7, note="n"):
    from orchestrator.models import Finding
    f = Finding(rule=rule, file=file, line=line, severity=sev, note=note)
    f.source = src
    f.confidence = conf
    return f


def test_dedupe_corroborates_cross_source():
    from orchestrator.pipeline import _dedupe
    out = _dedupe([_fnd(src="static", conf=1.0), _fnd(src="llm", conf=0.7)])
    assert len(out) == 1
    assert out[0].confidence >= 1.0 - 1e-9        # 交叉命中不降信心


def test_synthesize_threshold_and_cap(monkeypatch):
    from orchestrator.pipeline import _synthesize
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "2")
    stats = {}
    out = _synthesize([_fnd(conf=0.9, file="a.java", note="keep1"),
                       _fnd(conf=0.8, file="b.java", note="keep2"),
                       _fnd(conf=0.7, file="c.java", note="capped"),
                       _fnd(conf=0.2, file="d.java", note="low")], stats)
    assert len(out) == 2 and stats["dropped_low_conf"] == 1


def test_coverage_gate_sweeps_orphans():
    from orchestrator.pipeline import _Dim, _coverage_gate
    from orchestrator.rules import RulesBundle
    diff = ("--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"
            "--- a/y.java\n+++ b/y.java\n@@ -1,1 +1,2 @@\n a\n+c\n")
    ana = parse_diff(diff)
    dims = [_Dim(name="Only-X", focus="f", hypotheses=[],
                 files=["x.java"], rules=[])]
    out = _coverage_gate(dims, ana, RulesBundle(), {})
    swept = [d for d in out if d is not dims[0]]
    assert swept and any("y.java" in f for d in swept for f in d.files)


def test_verdict_incomplete_when_all_reviewers_failed():
    from orchestrator.render import _verdict
    assert "REVIEW INCOMPLETE" in _verdict(
        [], {"reviewer_errors": 4, "static_findings": 0})
    assert "APPROVED" in _verdict([], {"reviewer_errors": 0,
                                       "static_findings": 3})


def test_scorer_reports_per_source_recall():
    from eval.run_eval import _score_v2
    expected = [{"rule": "SECURITY"}, {"rule": "MISSING-TEST"}]
    findings = [{"rule": "SECURITY", "source": "llm", "file": "a"},
                {"rule": "MISSING-TEST", "source": "static", "file": ""}]
    recall, _, _, comment, by_src = _score_v2(expected, findings, 2)
    assert recall == 1.0 and by_src == {"static": 1, "llm": 1}
    assert "static 1 / llm 1" in comment


def test_cache_cleanup_survives_three_tuples():
    from orchestrator import app as A
    A._RECENT.clear()
    for i in range(205):                       # F2：>200 觸發清理路徑
        A._cache_put(f"u{i}", "md", f"sha{i}")
    assert len(A._RECENT) >= 200               # 未過期不清、但絕不 ValueError


def test_anatomy_ignores_git_header_lines():
    diff = ("diff --git a/x.java b/x.java\n"
            "index 111..222 100644\n"
            "--- a/x.java\n+++ b/x.java\n@@ -1,2 +1,3 @@\n"
            " a\n+b\n c\n"
            "diff --git a/y.java b/y.java\n"
            "new file mode 100644\n"
            "--- /dev/null\n+++ b/y.java\n@@ -0,0 +1,1 @@\n+z\n")
    ana = parse_diff(diff)
    xs = [fc for fc in ana.files if fc.path.endswith("x.java")][0]
    nums = [n for h in xs.hunks for _, n, _ in h.lines if n is not None]
    assert max(nums) == 3                      # 不再出現幽靈第 4/5 行


def test_harvester_never_posts_shadow_or_legacy_drafts(kroot, monkeypatch):
    """影子模式（dry_run）與無標記存量稿：active PR + posted:false，
    harvester 必須陪等、絕不代貼；post_failed 的真失敗稿才補貼、且只一次。"""
    from orchestrator import knowledge_harvest as H
    posted = []
    monkeypatch.setattr(H, "_get_pr", lambda c, r: {"status": "active"})
    monkeypatch.setattr(H, "_post_thread",
                        lambda c, r, t2: posted.append(t2) or 77)
    pend_dir = kroot / "_pending" / "repo-a"
    pend_dir.mkdir(parents=True)

    def mk(pid, **extra):
        f = pend_dir / f"pr-{pid}.json"
        f.write_text(json.dumps({
            "pr_url": "u", "pr_id": pid, "repo": "repo-a",
            "base_url": "https://x/C/P", "distilled_commit": "sha",
            "author": "a", "thread_id": None, "posted": False,
            "draft": _draft("src/x/F.java"), "created": "t", **extra}))
        return f

    shadow = mk(70, dry_run=True, post_failed=False)
    legacy = mk(71)                                   # F7 修正前的舊稿：無標記
    failed = mk(72, dry_run=False, post_failed=True)
    for f in (shadow, legacy):
        out = H._process_pending(object(), kroot, f)
        assert "陪等" in out and not posted
        assert json.loads(f.read_text())["posted"] is False
    out = H._process_pending(object(), kroot, failed)
    assert "補救成功" in out and len(posted) == 1
    info = json.loads(failed.read_text())
    assert info["posted"] is True and info["thread_id"] == 77
    assert "陪等" in H._process_pending(object(), kroot, failed)  # 不重貼


# ---------- digest / 條目 ID：蒸餾時定、暫存→落地不變、reject 三粒度 ----------

def test_assign_ids_idempotent_and_legacy_shapes():
    from orchestrator.knowledge import assign_ids, entry_ids, norm_id, split_id
    d = {"decisions": [{"text": "a"}, {"id": "PR9-D7", "text": "b"}],
         "invariants": ["x", {"id": "PR9-I3", "text": "y"}, "", {"text": "z"}]}
    assign_ids(d, 12)
    assert [q["id"] for q in d["decisions"]] == ["PR12-D1", "PR9-D7"]
    assert d["invariants"] == [{"id": "PR12-I1", "text": "x"},
                               {"id": "PR9-I3", "text": "y"},
                               {"id": "PR12-I4", "text": "z"}]
    snap = json.dumps(d)
    assign_ids(d, 99)                              # 冪等：已有 id 一律不動
    assert json.dumps(d) == snap
    assert entry_ids(d, 12) == {"PR12", "PR12-D1", "PR9-D7",
                                "PR12-I1", "PR9-I3", "PR12-I4"}
    assert norm_id("pr12-i1") == "PR12-I1" and norm_id("PR12") == ""
    assert split_id("rule text `PR12-I1`") == ("rule text", "PR12-I1")
    assert split_id("legacy line") == ("legacy line", "")


def test_land_ids_retire_by_id_and_dedupe_by_text(kroot):
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants
    H._land(kroot, "repo-a", 55,
            _draft("src/a/cache/S.java", invariants=["inv-A", "inv-B"],
                   decisions=[{"text": "dec", "evidence": ["PR"]}]),
            "cycle/silent", "")
    page = kroot / "repo-a" / "cache" / "S.md"
    body = page.read_text()
    assert "- inv-A `PR55-I1`" in body and "- dec [PR] `PR55-D1`" in body
    cat = existing_invariants("repo-a", ["src/a/cache/S.java"])
    assert cat == [("repo-a/cache/S.md", [("PR55-I1", "inv-A"),
                                          ("PR55-I2", "inv-B")])]
    # PR#56：用 ID 註銷 inv-A、重述 inv-B（文字相同 → 不重複落）、新增 inv-C
    H._land(kroot, "repo-a", 56,
            _draft("src/a/cache/S.java", invariants=["inv-B", "inv-C"],
                   supersedes=[{"entity": "S", "what": "A gone",
                                "retires_id": "pr55-i1"}]),
            "cycle/explicit", "", rejected_ids=["PR56-D9"])
    body = page.read_text()
    assert "- ~~inv-A~~ ⚠ retired by PR#56 `PR55-I1`" in body
    assert body.count("inv-B") == 1 and "inv-C `PR56-I2`" in body
    assert "rejected: `PR56-D9`" in body
    # 已註銷的不再列入 catalogue；ID 跟著行走
    cat = existing_invariants("repo-a", ["src/a/cache/S.java"])
    assert cat[0][1] == [("PR55-I2", "inv-B"), ("PR56-I2", "inv-C")]
    # 舊格式（無 ID）行仍可用逐字相等註銷
    page.write_text(body.replace("- inv-C `PR56-I2`", "- legacy-rule"))
    H._land(kroot, "repo-a", 57, _draft(
        "src/a/cache/S.java",
        supersedes=[{"entity": "S", "what": "x", "retires_verbatim": "legacy-rule"}]),
        "cycle/silent", "")
    assert "~~legacy-rule~~ ⚠ retired by PR#57" in page.read_text()


def test_land_stem_collision_skip_returns_pair(kroot):
    """同 parent+stem、不同實體 → 分頁；重複落地走 skip 路徑必須回 2-tuple
    （原本回三個值，呼叫端 unpack 會炸、整個週期卡死）。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 60, _draft("src/a/cache/S.java"), "cycle/silent", "")
    msg, page = H._land(kroot, "repo-a", 61, _draft("src/b/cache/S.java"),
                        "cycle/silent", "")
    assert page.name.startswith("S-") and page.exists()
    msg2, page2 = H._land(kroot, "repo-a", 61, _draft("src/b/cache/S.java"),
                          "cycle/silent", "")
    assert msg2.startswith("skip") and page2 == page


def _stage(kroot, pid, ent, **kw):
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True, exist_ok=True)
    (cyc / f"pr-{pid}.json").write_text(json.dumps({
        "pr_url": "u", "pr_id": pid, "repo": "repo-a",
        "base_url": "https://x/C/P", "target_branch": "release/x",
        "draft": _draft(ent, invariants=[f"rule-{pid}"], **kw),
        "author_note": "", "provenance": "silent"}))


def test_digest_first_delta_then_unlisted_on_close(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    posted, active, completed = [], [], []
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: posted.append(t))
    monkeypatch.setattr(H, "_list_active", lambda c, r: active)
    monkeypatch.setattr(H, "_list_completed", lambda c, r: completed)
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    acc = {"pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
           "targetRefName": "refs/heads/master", "closedDate": "z"}
    state_f = kroot / "_state" / "cycle.json"

    _stage(kroot, 70, "src/x/A.java")
    active.append(acc)
    H._cycle_close(object(), kroot)                       # 首次 digest
    assert len(posted) == 1 and "pending acceptance" in posted[0]
    assert "PR#70" in posted[0] and "`PR70-I1`" in posted[0]
    assert json.loads(state_f.read_text())["repo-a#500"]["listed"] == ["PR70"]

    H._cycle_close(object(), kroot)                       # 沒新稿：不重貼
    assert len(posted) == 1

    _stage(kroot, 71, "src/y/B.java")
    H._cycle_close(object(), kroot)                       # delta：只列新進
    assert len(posted) == 2 and "update: 1 new entry" in posted[1]
    assert "PR#71" in posted[1] and "`PR70-I1`" not in posted[1]
    assert json.loads(state_f.read_text())["repo-a#500"]["listed"] == ["PR70", "PR71"]

    _stage(kroot, 72, "src/z/C.java")                     # 驗收 PR 已完成才進池
    active.clear()
    completed.append(acc)
    lines = H._cycle_close(object(), kroot)
    assert len(posted) == 2                               # completed 不再貼 digest
    for stem in ("x/A", "y/B", "z/C"):
        assert (kroot / "repo-a" / f"{stem}.md").exists()
    receipt = next((kroot / "repo-a" / "_cycles").glob("*-PR500.md")).read_text()
    assert "| PR#72 | C | cycle/silent+unlisted |" in receipt
    assert "| PR#70 | A | cycle/silent |" in receipt
    assert "cycle/silent+unlisted" in (kroot / "repo-a" / "z" / "C.md").read_text()
    assert any("1 條從未出現在 digest" in ln for ln in lines)
    assert json.loads(state_f.read_text())["repo-a#500"]["closed"] is True


def test_reject_by_id_line_and_entry(kroot):
    from orchestrator import knowledge_harvest as H
    _stage(kroot, 70, "src/x/A.java",
           decisions=[{"text": "d1"}, {"text": "d2"}])
    _stage(kroot, 71, "src/y/B.java")
    cyc = kroot / "_cycle" / "repo-a"
    staged = sorted(cyc.glob("*.json"))
    msgs, amb = H._apply_rejects({"pr70-d2", "pr70-i1", "pr99-i1"}, staged, "t")
    assert not amb
    info = json.loads((cyc / "pr-70.json").read_text())
    assert [d["id"] for d in info["draft"]["decisions"]] == ["PR70-D1"]
    assert info["draft"]["invariants"] == []            # 行拿掉、條目還在
    assert info["rejected_ids"] == ["PR70-D2", "PR70-I1"]
    assert any("PR99-I1" in m and "查無" in m for m in msgs)
    msgs, _ = H._apply_rejects({"pr71"}, sorted(cyc.glob("*.json")), "t")
    assert any("整條丟" in m for m in msgs)
    assert [f.name for f in cyc.glob("*.json")] == ["pr-70.json"]


def test_digest_validate_and_render_offline():
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 70, "provenance": "silent", "author_note": "keep it",
              "draft": _draft("src/x/A.java",
                              invariants=[{"id": "PR70-I1", "text": "r70"}],
                              decisions=[{"id": "PR70-D1", "text": "d70",
                                          "evidence": ["PR"]}])},
             {"pr_id": 71, "provenance": "explicit", "author_note": "",
              "draft": _draft("src/y/B.java",
                              invariants=[{"id": "PR71-I1", "text": "r71"}],
                              supersedes=[{"entity": "A", "what": "w",
                                           "retires_id": "PR1-I1"}])}]
    known = D.known_ids(items, [("repo-a/x/A.md", [("PR1-I1", "old")])])
    assert {"PR70", "PR70-I1", "PR70-D1", "PR71", "PR71-I1", "PR1-I1"} <= known
    raw = {"headline": "  cycle  summary ",
           "flags": [{"kind": "Conflict", "ids": ["pr70-i1", "PR71-I1", "PR5-I9"],
                      "note": "a\nb", "suggest": "reject PR70-I1"},
                     {"kind": "duplicate", "ids": ["PR404-I1"], "note": "n"},
                     {"kind": "praise", "ids": ["PR70-I1"], "note": "n"},
                     {"kind": "unclear", "ids": ["PR71-I1"], "note": "n",
                      "suggest": "reject PR9-I9 now"},
                     "junk"]}
    out = D.validate(raw, known)
    assert out["headline"] == "cycle summary"
    assert [f["kind"] for f in out["flags"]] == ["conflict", "unclear"]
    assert out["flags"][0]["ids"] == ["PR70-I1", "PR71-I1"]     # 未知 ID 被濾
    assert out["flags"][0]["note"] == "a b"
    assert out["flags"][1]["suggest"] == ""                      # 非法建議只留 note
    assert D.validate(raw, known, max_flags=1)["flags"][0]["kind"] == "conflict"
    assert D.validate("nope", known) is None

    md = D.render(items, None, out)
    assert md.startswith(D.KMARK) and "pending acceptance** (2 entries)" in md
    assert "Needs a look" in md and "`PR70-I1` ↔ `PR71-I1`" in md
    assert "→ `reject PR70-I1`" in md and "_cycle summary_" in md
    assert "Rules being retired" in md and "retires `PR1-I1`" in md
    assert "- d70 [PR] `PR70-D1`" in md and "💬 author: keep it" in md
    assert "`reject PR12-I1`" in md                              # 指令說明
    md_delta = D.render(items, {"PR71"}, out, total=2)
    assert "update: 1 new entry" in md_delta and "PR#70 ·" not in md_delta
    assert "`PR70-I1` ↔ `PR71-I1`" in md_delta                   # 牽涉新進 PR → 留
    assert "**unclear**" in md_delta and "_cycle summary_" not in md_delta
    md_plain = D.render(items, None, None)
    assert "Needs a look" not in md_plain and "PR#70" in md_plain
    assert D.parse_reject_token("pr70-i1") == ("line", "PR70-I1")
    assert D.parse_reject_token("PR70") == ("entry", "PR70")
    assert D.parse_reject_token("x/A.java") == ("name", "x/A.java")


def test_analyze_degrades_to_none_and_respects_switch(monkeypatch):
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 1, "draft": _draft("a/B.java", invariants=["r"])}]
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "false")
    assert D.analyze("repo-a", items) is None
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "true")
    monkeypatch.setattr(D, "complete_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    log = []
    assert D.analyze("repo-a", items, log=log.append) is None
    assert log and "分析失敗" in log[0]
    monkeypatch.setattr(D, "complete_sync", lambda *a, **k:
                        '{"headline":"h","flags":[{"kind":"unclear","ids":["PR1-I1"],"note":"n"}]}')
    out = D.analyze("repo-a", items)
    assert out["flags"][0]["ids"] == ["PR1-I1"]


def test_write_json_atomic_leaves_no_tmp(tmp_path=None):
    from orchestrator.knowledge import write_json_atomic
    d = pathlib.Path(tempfile.mkdtemp()) / "nested"
    write_json_atomic(d / "pr-1.json", {"a": 1})
    write_json_atomic(d / "pr-1.json", {"a": 2})
    assert json.loads((d / "pr-1.json").read_text()) == {"a": 2}
    assert [p.name for p in d.iterdir()] == ["pr-1.json"]


def test_mark_superseded_locates_page_by_id(kroot):
    """retires_id 命中時不走 stem 猜頁：entity 亂填也蓋對頁。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 40, _draft("src/a/lock/LockService.java",
                                        invariants=["both keys"]), "cycle/silent", "")
    msg = H._mark_superseded(kroot, "repo-a",
                             {"entity": "com.acme.Whatever", "what": "w",
                              "retires_id": "PR40-I1"}, 41)
    assert "PR40-I1 定位" in msg
    body = (kroot / "repo-a" / "lock" / "LockService.md").read_text()
    assert "Superseded (PR#41" in body


def test_cycle_close_waits_for_pending_of_same_branch(kroot, monkeypatch):
    """驗收 PR 完成、池子空、但還有小 PR 未終態且指向本週期分支 → 不標 closed，
    等它進池後照常落地（原本會永久孤兒）。"""
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見（見水位測試）
        json.dumps({"repo-a#1": {"closed": True}}))
    pend = kroot / "_pending" / "repo-a"
    pend.mkdir(parents=True)
    (pend / "pr-80.json").write_text(json.dumps({
        "pr_id": 80, "repo": "repo-a", "target_branch": "release/x",
        "draft": _draft("src/q/Q.java")}))
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])
    lines = H._cycle_close(object(), kroot)
    st = json.loads((kroot / "_state" / "cycle.json").read_text())
    assert not st.get("repo-a#500", {}).get("closed")
    assert any("暫不收口" in ln for ln in lines)
    (pend / "pr-80.json").unlink()                     # 小 PR 終態 → 進池
    _stage(kroot, 80, "src/q/Q.java")
    H._cycle_close(object(), kroot)
    assert (kroot / "repo-a" / "q" / "Q.md").exists()
    assert json.loads((kroot / "_state" / "cycle.json").read_text())["repo-a#500"]["closed"]


# ========== Phase 1：蒸餾證據地板（切片 / evidence 驗證 / primary 錨點） ==========

_EV_DIFF = ("--- a/src/main/java/com/acme/cache/SessionCache.java\n"
            "+++ b/src/main/java/com/acme/cache/SessionCache.java\n"
            "@@ -10,4 +10,6 @@\n"
            " ctx10\n ctx11\n+added12\n+added13\n ctx14\n ctx15\n")


def test_verify_evidence_matrix():
    """四種 evidence 形式 × 過/不過；剝光的 decision 整條不入稿。"""
    from orchestrator.anatomy import Anatomy
    from orchestrator.knowledge import verify_evidence
    ana = parse_diff(_EV_DIFF)
    long_desc = "x" * 60
    data = {"decisions": [
        {"text": "keeps a real line", "evidence": ["SessionCache.java:12"]},
        {"text": "keeps a finding id", "evidence": ["F2"]},
        {"text": "keeps a PR-backed rationale", "evidence": ["PR"]},
        {"text": "mixed: one good one bogus",
         "evidence": ["Nope.java:12", "SessionCache.java:14"]},
        {"text": "line outside the excerpt", "evidence": ["SessionCache.java:99"]},
        {"text": "file not in this diff", "evidence": ["Other.java:12"]},
        {"text": "finding id we never issued", "evidence": ["F9"]},
        {"text": "no evidence at all", "evidence": []},
        {"text": "free-form prose", "evidence": ["see the diff"]},
    ]}
    out, dropped = verify_evidence(data, ana, {"F1", "F2"}, long_desc)
    kept = [d["text"] for d in out["decisions"]]
    assert kept == ["keeps a real line", "keeps a finding id",
                    "keeps a PR-backed rationale", "mixed: one good one bogus"]
    assert out["decisions"][3]["evidence"] == ["SessionCache.java:14"]  # 壞的被剝
    assert len(dropped) == 5 and "no evidence at all" in dropped

    # "PR" 需要有內容的 description（去空白後 >= 40 字）
    thin, _ = verify_evidence({"decisions": [{"text": "t", "evidence": ["PR"]}]},
                              ana, set(), "too short")
    assert thin["decisions"] == []
    # 沒有 diff 可比對時 file:line 一律不過（寧可少收，不可假證據）
    blind, _ = verify_evidence(
        {"decisions": [{"text": "t", "evidence": ["SessionCache.java:12"]}]},
        Anatomy(), set(), long_desc)
    assert blind["decisions"] == []


def test_distill_slices_priority_and_budget(monkeypatch):
    """優先序：新增最多 → finding 引到的 → 有新宣告的 → 其餘；超預算留註記。"""
    from orchestrator.knowledge import _distill_order, distill_slices

    def f(path, adds, decl=False):
        body = "\n".join((f"+    public void m{i}() {{" if decl else f"+ line{i}")
                         for i in range(adds))
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{adds} @@\n{body}\n"

    ana = parse_diff(f("src/Big.java", 12) + f("src/Cited.java", 3)
                     + f("src/Decl.java", 3, decl=True) + f("src/Plain.java", 3))
    order = [fc.path for fc in _distill_order(ana, ["src/Cited.java"])]
    assert order == ["/src/Big.java", "/src/Cited.java", "/src/Decl.java",
                     "/src/Plain.java"]
    monkeypatch.setenv("DISTILL_DIFF_MAX_CHARS", "400")
    text, skipped = distill_slices(ana, ["src/Cited.java"])
    assert skipped and "omitted from the diff excerpt" in text
    assert "/src/Big.java" not in skipped          # 最高優先序不會被省掉


def test_primary_entity_prefers_added_lines_over_path_length():
    """舊 fallback 取『路徑最長』——那通常是測試檔。改取新增行數最多的原始碼檔。"""
    from orchestrator.knowledge import fix_primary_entity, pick_primary_entity
    diff = ("--- /dev/null\n+++ b/src/main/java/com/acme/lock/LockService.java\n"
            "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
            "--- /dev/null\n"
            "+++ b/src/test/java/com/acme/lock/integration/LockServiceIT.java\n"
            "@@ -0,0 +1,8 @@\n" + "\n".join(f"+t{i}" for i in range(8)) + "\n"
            "--- /dev/null\n+++ b/pom.xml\n@@ -0,0 +1,9 @@\n"
            + "\n".join(f"+<dep{i}/>" for i in range(9)) + "\n")
    ana = parse_diff(diff)
    assert pick_primary_entity(ana, []).endswith("LockService.java")
    d = {"primary_entity": "src/main/java/com/acme/lock/LockService.java"}
    assert fix_primary_entity(d, ana, []) is False        # 在變更檔內 → 不動
    d = {"primary_entity": "com.acme.SomewhereElse"}
    assert fix_primary_entity(d, ana, []) is True         # 不在 → fallback
    assert d["primary_entity"].endswith("LockService.java")


# ========== Phase 2：引用率（注入 id 名單 → knowledge_ref → 確定性執法） ==========

def _seed_page(kroot, repo, rel, entity, invariants, affected=(), pad=0):
    p = kroot / repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {p.stem}", "", f"> entity: {entity}",
            "> approval: seed · last: PR#9 · 2026-08-01", "",
            "## Summary", "s" * pad if pad else "seed page", "", "## Invariants"]
    body += [f"- {t} `{i}`" for i, t in invariants]
    if affected:
        body += ["", "## Affected entities", ", ".join(f"`{a}`" for a in affected)]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def test_load_for_reports_only_injected_ids(kroot):
    from orchestrator.knowledge import load_for
    _seed_page(kroot, "repo-a", "cache/SessionCache.md",
               "src/main/java/com/acme/cache/SessionCache.java",
               [("PR41-I1", "ttl is millis")])
    text, stats = load_for("repo-a",
                           ["src/main/java/com/acme/cache/SessionCache.java"])
    assert stats["knowledge_ids"] == ["PR41-I1"]      # 尾註的說明文字不算數
    assert stats["knowledge_full"] == 1 and stats["knowledge_omitted"] == 0
    assert "knowledge_ref" in text                    # 引用合約有講給模型聽
    assert load_for("repo-a",
                    ["src/other/Unrelated.java"])[1]["knowledge_entries"] == 0


def test_synthesize_verifies_knowledge_refs():
    from orchestrator.pipeline import _synthesize
    good = _fnd(file="a.java", note="grounded")
    good.knowledge_ref = ["pr41-i1", "PR99-I9"]       # 後者沒被注入過 → 剝掉
    bare = _fnd(file="b.java", note="ungrounded")
    stats = {"knowledge_ids": ["PR41-I1"]}
    out = _synthesize([good, bare], stats)
    assert good.knowledge_ref == ["PR41-I1"]
    assert stats["knowledge_grounded"] == 1 and stats["knowledge_refs_stripped"] == 1
    assert stats["knowledge_ids_injected"] == 1
    assert len(out) == 2


def test_dedupe_keeps_knowledge_ref_of_the_dropped_twin():
    from orchestrator.pipeline import _dedupe
    keep = _fnd(conf=0.9, note="kept")
    twin = _fnd(conf=0.7, note="dup with a citation")
    twin.knowledge_ref = ["PR41-I1"]
    out = _dedupe([keep, twin])
    assert len(out) == 1 and out[0].knowledge_ref == ["PR41-I1"]


def test_render_shows_knowledge_citation():
    from orchestrator.render import render_markdown
    f = _fnd(note="ttl unit is wrong", sev="error")
    f.knowledge_ref = ["PR41-I1"]
    md = render_markdown([f], {"files": 1, "added_lines": 3,
                               "knowledge_entries": 1, "knowledge_grounded": 1})
    assert "(knowledge: PR41-I1)" in md
    assert "1 finding(s) cite a ledger entry" in md


def test_score_knowledge_requires_both_catch_and_citation():
    from eval.run_eval import _score_knowledge
    expected = [{"rule": "BUG", "file": "src/A.java", "knowledge_ref": "PR41-I1"}]
    caught_and_cited = [{"rule": "BUG", "file": "src/A.java",
                         "knowledge_ref": ["PR41-I1"]}]
    caught_only = [{"rule": "BUG", "file": "src/A.java", "knowledge_ref": []}]
    cited_elsewhere = [{"rule": "NAMING", "file": "src/A.java",
                        "knowledge_ref": ["PR41-I1"]}]
    assert _score_knowledge(expected, caught_and_cited)[0] == 1.0
    assert _score_knowledge(expected, caught_only)[0] == 0.0
    assert _score_knowledge(expected, cited_elsewhere)[0] == 0.0
    assert _score_knowledge([{"rule": "BUG"}], caught_only) is None   # 非知識題


# ========== Phase 3：檢索排序（tier）與分級降載 ==========

def test_page_tier_ordering(kroot):
    from orchestrator.knowledge import _MatchCtx
    changed = ["src/main/java/com/acme/cache/SessionCache.java",
               "src/main/java/com/acme/pricing/PricingService.java"]
    ctx = _MatchCtx(changed)
    exact = _seed_page(kroot, "r", "cache/SessionCache.md", changed[0],
                       [("PR1-I1", "x")])
    # 同 (parent, stem) 但住在另一棵子樹的實體：檔名命中，不是精確路徑命中
    other = _seed_page(kroot, "r", "pricing/PricingService.md",
                       "src/legacy/pricing/PricingService.java", [("PR2-I1", "y")])
    neigh = _seed_page(kroot, "r", "cfg/CacheConfig.md",
                       "src/main/java/com/acme/cfg/CacheConfig.java",
                       [("PR3-I1", "z")], affected=["SessionCache.java"])
    mod = _seed_page(kroot, "r", "_module/cache.md", "(module)", [("PR4-I1", "w")])
    miss = _seed_page(kroot, "r", "far/Unrelated.md",
                      "src/main/java/com/acme/far/Unrelated.java", [("PR5-I1", "v")])

    def tier(page):
        return ctx.page_hit(page, page.relative_to(kroot / "r"),
                            page.read_text(encoding="utf-8"))

    assert tier(exact) == 4
    assert tier(other) == 3          # 同 (parent, stem)，但 entity 路徑不同
    assert tier(neigh) == 2          # Affected entities 鄰接
    assert tier(mod) == 1            # _module 路徑段
    assert tier(miss) == 0
    assert ctx.entry_tier(changed[0], []) == 4
    assert ctx.entry_tier("src/legacy/pricing/PricingService.java", []) == 3
    assert ctx.entry_tier("src/x/SessionCache.java", []) == 3
    assert ctx.entry_tier("src/x/Nope.java", ["SessionCache.java"]) == 2
    assert ctx.entry_tier("src/x/Nope.java", []) == 0


def test_load_for_guarantees_primary_and_degrades_the_rest(kroot, monkeypatch):
    """預算極小時：tier 4 仍保證入場（降為 Invariants 段），較弱的命中留註記。"""
    from orchestrator.knowledge import load_for
    changed = ["src/main/java/com/acme/cache/SessionCache.java"]
    _seed_page(kroot, "repo-a", "cache/SessionCache.md", changed[0],
               [("PR41-I1", "ttl is millis")], pad=4000)
    # 鄰接命中的那頁連「只注入 Invariants 段」都塞不下 → 它才是被省略的那個
    _seed_page(kroot, "repo-a", "cfg/CacheConfig.md",
               "src/main/java/com/acme/cfg/CacheConfig.java",
               [("PR42-I1", "config is seconds " + "y" * 1200)],
               affected=["SessionCache.java"], pad=4000)
    monkeypatch.setenv("KNOWLEDGE_MAX_CHARS", "900")
    text, stats = load_for("repo-a", changed)
    assert stats["knowledge_full"] == 0
    assert stats["knowledge_invariants_only"] == 1        # tier 4 保證入場
    assert stats["knowledge_omitted"] == 1
    assert "PR41-I1" in stats["knowledge_ids"]
    assert "PR42-I1" not in stats["knowledge_ids"]
    assert "invariants only" in text and "more matched entries omitted" in text
    assert "CacheConfig" in text.split("more matched entries omitted")[1]


def test_load_for_orders_by_tier_not_by_walk_order(kroot):
    from orchestrator.knowledge import load_for
    changed = ["src/main/java/com/acme/zone/ZoneRouter.java"]
    _seed_page(kroot, "repo-a", "aaa/Neighbour.md",
               "src/main/java/com/acme/aaa/Neighbour.java",
               [("PR10-I1", "neighbour rule")], affected=["ZoneRouter.java"])
    _seed_page(kroot, "repo-a", "zone/ZoneRouter.md", changed[0],
               [("PR20-I1", "compare with equals")])
    text, _ = load_for("repo-a", changed)
    assert text.index("ZoneRouter") < text.index("Neighbour")   # 精確命中在前


# ========== Phase 4：蒸餾考卷的確定性計分（含三題陷阱的判法） ==========

def _distill_expect(**kw):
    exp = {"intent_stated": True, "retires_id": None, "must_mention": [],
           "primary_entity": "src/main/java/com/acme/cache/SessionCache.java"}
    exp.update(kw)
    return exp


def _distill_draft(**kw):
    d = {"summary": "ttl boundary is milliseconds", "type": "bugfix",
         "intent_stated": True,
         "primary_entity": "src/main/java/com/acme/cache/SessionCache.java",
         "decisions": [{"text": "chose millisecond boundary",
                        "evidence": ["SessionCache.java:12"]}],
         "invariants": [], "supersedes": [], "affected": [],
         "ledger_line": "ttl unit fixed"}
    d.update(kw)
    return d


def _clean_info(**kw):
    info = {"dropped": [], "evidence_stripped": 0, "primary_fixed": False}
    info.update(kw)
    return info


def test_score_distill_perfect_paper():
    from eval.run_distill_eval import score_distill
    rows, score = score_distill(_distill_expect(must_mention=["millisecond"]),
                                _distill_draft(), _clean_info(), [])
    assert score == 1.0
    assert {n for n, _, _ in rows} == {"intent_ok", "retires_ok", "evidence_ok",
                                       "must_mention", "primary_ok"}


def test_score_distill_traps():
    """三題陷阱各自的判法：編 rationale / 推論 supersede / 幻覺不變式。"""
    from eval.run_distill_eval import score_distill

    def val(rows, name):
        return next(v for n, v, _ in rows if n == name)

    # ① why 缺席：intent_stated 說了謊，且條目裡冒出 rationale 措辭
    rows, _ = score_distill(
        _distill_expect(intent_stated=False,
                        forbidden_mention=["because", "so that"]),
        _distill_draft(intent_stated=True,
                       decisions=[{"text": "raised the batch size because "
                                           "throughput was low",
                                   "evidence": ["SessionCache.java:12"]}]),
        _clean_info(), [])
    assert val(rows, "intent_ok") == 0.0 and val(rows, "no_fabrication") == 0.0

    # ② supersede 假訊號：沒有明示反轉卻填了 retires_id（誤殺一條還有效的規則）
    rows, _ = score_distill(
        _distill_expect(),
        _distill_draft(supersedes=[{"entity": "FeatureFlags",
                                    "what": "per-request reads",
                                    "retires_id": "PR44-I1"}]),
        _clean_info(), [])
    assert val(rows, "retires_ok") == 0.0
    # 反過來：該點名卻點錯 ID —— 一樣扣（誤殺別條比漏標更貴）
    rows, _ = score_distill(
        _distill_expect(retires_id="PR31-I1"),
        _distill_draft(supersedes=[{"entity": "WriteClient", "what": "retries",
                                    "retires_id": "PR31-I2"}]),
        _clean_info(), [])
    assert val(rows, "retires_ok") == 0.0

    # ③ 幻覺不變式：材料撐不起任何 invariant，蒸出來就扣
    rows, _ = score_distill(
        _distill_expect(forbid_invariants=True),
        _distill_draft(invariants=[{"id": "PR1-I1", "text": "javadoc must be "
                                                            "spelled correctly"}]),
        _clean_info(), [])
    assert val(rows, "no_hallucination") == 0.0


def test_score_distill_evidence_and_primary():
    from eval.run_distill_eval import score_distill

    def val(rows, name):
        return next(v for n, v, _ in rows if n == name)

    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(evidence_stripped=1), [])
    assert val(rows, "evidence_ok") == 0.0            # 有 evidence 被剝 = 沒過關
    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(dropped=["a decision with no proof"]), [])
    assert val(rows, "evidence_ok") == 0.0
    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(primary_fixed=True), [])
    assert val(rows, "primary_ok") == 0.0             # 靠 fallback 救回不算分
    rows, _ = score_distill(
        _distill_expect(primary_entity="src/main/java/com/acme/other/X.java"),
        _distill_draft(), _clean_info(), [])
    assert val(rows, "primary_ok") == 0.0             # 錨錯實體
    rows, _ = score_distill(_distill_expect(must_mention=["millisecond", "floor"]),
                            _distill_draft(), _clean_info(), [])
    assert val(rows, "must_mention") == 0.5


# ========== Phase 5：stale 重蒸（請求戳 / 重試上限 / 覆寫 / provenance） ==========

def _pending(kroot, pid=90, **extra):
    from orchestrator.knowledge import write_json_atomic
    f = kroot / "_pending" / "repo-a" / f"pr-{pid}.json"
    write_json_atomic(f, {
        "pr_url": f"https://x/C/P/_git/repo-a/pullrequest/{pid}",
        "pr_id": pid, "repo": "repo-a", "base_url": "https://x/C/P",
        "target_branch": "release/x", "distilled_commit": "sha-OLD",
        "author": "a", "thread_id": 7, "posted": True, "dry_run": False,
        "draft": _draft("src/x/F.java"), "created": "t", **extra})
    return f


def _completed_pr(merge="sha-NEW"):
    return {"status": "completed", "lastMergeSourceCommit": {"commitId": merge},
            "createdBy": {"id": "author-1"}}


def test_stale_requests_redistill_then_gives_up(kroot, monkeypatch):
    """戳 + attempts ≤ 2：30 分鐘一輪的 timer 不會變成無界的 LLM 重試迴圈。"""
    from orchestrator import knowledge_harvest as H
    calls = []
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill",
                        lambda url: (calls.append(url) or (True, "已請求重蒸")))
    pend = _pending(kroot)

    out = H._process_pending(object(), kroot, pend)
    assert "請求重蒸（第 1/2 次" in out and len(calls) == 1
    stamp = json.loads(pend.read_text(encoding="utf-8"))["redistill_requested"]
    assert stamp == {"commit": "sha-NEW", "attempts": 1}
    assert pend.exists()                              # 稿留著等重蒸

    out = H._process_pending(object(), kroot, pend)   # 背景失敗了 → 再一次
    assert "第 2/2 次" in out and len(calls) == 2
    assert json.loads(pend.read_text(encoding="utf-8"))["redistill_requested"] \
        == {"commit": "sha-NEW", "attempts": 2}

    out = H._process_pending(object(), kroot, pend)   # 兩次都沒成 → 丟稿
    assert "重蒸兩次未成" in out and not pend.exists() and len(calls) == 2


def test_stale_gives_up_when_a_newer_push_lands_mid_redistill(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill", lambda url: (True, "ok"))
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr("sha-NEWER"))
    pend = _pending(kroot, redistill_requested={"commit": "sha-NEW", "attempts": 1})
    out = H._process_pending(object(), kroot, pend)
    assert "世代變更" in out and not pend.exists()


def test_stale_falls_back_to_dropping_when_redistill_is_off(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setenv("KNOWLEDGE_REDISTILL", "false")
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill",
                        lambda url: pytest.fail("關掉了還打 endpoint"))
    pend = _pending(kroot)
    out = H._process_pending(object(), kroot, pend)
    assert "stale 丟稿" in out and "KNOWLEDGE_REDISTILL=false" in out
    assert not pend.exists()


def test_stale_keeps_author_reply_for_the_redistill_material(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_request_redistill", lambda url: (True, "ok"))
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [{
        "id": 7, "comments": [
            {"author": {"id": "bot"}, "content": "draft"},
            {"author": {"id": "author-1"}, "content": "we chose X over Y"}]}])
    pend = _pending(kroot)
    H._process_pending(object(), kroot, pend)
    assert json.loads(pend.read_text(encoding="utf-8"))["author_note"] \
        == "we chose X over Y"


def test_redistilled_draft_stages_with_visible_provenance(kroot, monkeypatch):
    """重蒸過的稿落地時 provenance 攤開說：內容是重蒸來的、作者有沒有說過話。"""
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr("sha-NEW"))
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])   # thread 讀不到
    pend = _pending(kroot, distilled_commit="sha-NEW", redistilled=True,
                    author_note="kept from the earlier draft")
    out = H._process_pending(object(), kroot, pend)
    assert "staged（explicit·redistilled" in out
    staged = json.loads((kroot / "_cycle" / "repo-a" / "pr-90.json")
                        .read_text(encoding="utf-8"))
    assert staged["provenance"] == "explicit·redistilled"
    assert staged["author_note"] == "kept from the earlier draft"

    pend2 = _pending(kroot, pid=91, distilled_commit="sha-NEW", redistilled=True)
    H._process_pending(object(), kroot, pend2)
    staged2 = json.loads((kroot / "_cycle" / "repo-a" / "pr-91.json")
                         .read_text(encoding="utf-8"))
    assert staged2["provenance"] == "silent·redistilled"      # 沒人說過話


def test_redistill_overwrites_pending_and_clears_stale_traces(kroot, monkeypatch):
    """重蒸：最終 diff 重蒸 → 覆寫 _pending、回覆進原 thread、清掉舊的『已列』。"""
    from orchestrator import knowledge as K
    pend = _pending(kroot, author_note="the author said this earlier",
                    redistill_requested={"commit": "sha-NEW", "attempts": 1})
    # 舊的暫存稿 + 已列狀態（手動補蒸的情境）：都必須被作廢
    K.write_json_atomic(kroot / "_cycle" / "repo-a" / "pr-90.json",
                        {"pr_id": 90, "rejected_ids": ["PR90-I1"],
                         "draft": _draft("src/x/F.java")})
    K.write_json_atomic(kroot / "_state" / "cycle.json",
                        {"repo-a#500": {"listed": ["PR70", "PR90"],
                                        "digest": True}})
    diff = ("--- a/src/x/F.java\n+++ b/src/x/F.java\n@@ -10,3 +10,4 @@\n"
            " c10\n+a11\n c12\n c13\n")
    seen = {}

    async def f_meta(u):
        return {"title": "t", "description": "d" * 60, "source_commit": "sha-NEW",
                "target_branch": "release/x"}

    async def f_diff(u):
        return diff

    async def f_post(pr_url, markdown, thread_id=None):
        seen["thread_id"] = thread_id
        seen["markdown"] = markdown
        return {"posted": True, "dry_run": False, "reply": True,
                "thread_id": thread_id}

    async def f_complete(system, user, **kw):
        seen["user"] = user
        return json.dumps({
            "summary": "redistilled from the final diff", "type": "bugfix",
            "intent_stated": True, "primary_entity": "src/x/F.java",
            "decisions": [{"text": "kept the guard", "evidence": ["F.java:11"]},
                          {"text": "no proof at all", "evidence": ["F2"]}],
            "invariants": ["the guard stays"], "supersedes": [], "affected": [],
            "ledger_line": "guard kept"})

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(K.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(K.clients, "post_knowledge_draft", f_post)
    monkeypatch.setattr(K, "prompt_or", lambda name, default: "SYSTEM")
    monkeypatch.setattr(K, "complete", f_complete)

    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))

    info = json.loads(pend.read_text(encoding="utf-8"))
    assert info["redistilled"] is True and info["redistill_of"] == "sha-OLD"
    assert info["distilled_commit"] == "sha-NEW"
    assert "redistill_requested" not in info                  # 戳被消化掉了
    assert info["draft"]["summary"] == "redistilled from the final diff"
    assert [d["id"] for d in info["draft"]["decisions"]] == ["PR90-D1"]
    assert info["distill_dropped"] == ["no proof at all"]      # F 編號已 stale
    assert info["evidence_verified"] is True
    # 舊痕跡作廢：暫存稿移除、listed 移除 PR90（下一則 delta 會重新列它）
    assert not (kroot / "_cycle" / "repo-a" / "pr-90.json").exists()
    state = json.loads((kroot / "_state" / "cycle.json").read_text(encoding="utf-8"))
    assert state["repo-a#500"]["listed"] == ["PR70"]
    # 材料：帶作者原話、不含舊 review 報告
    assert "the author said this earlier" in seen["user"]
    assert "Review report" not in seen["user"]
    assert seen["thread_id"] == 7                              # 回覆進原 thread
    assert "1 decision(s) dropped" in seen["markdown"]


def test_redistill_is_once_per_pr(kroot, monkeypatch):
    from orchestrator import knowledge as K
    pend = _pending(kroot, redistilled=True)
    before = pend.read_text(encoding="utf-8")

    async def boom(*a, **k):
        raise AssertionError("已重蒸過的稿不該再打 ADO")

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", boom)
    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))
    assert pend.read_text(encoding="utf-8") == before


def test_knowledge_draft_thread_reply_is_a_reply_not_a_new_thread(monkeypatch):
    from orchestrator.adapters import ado as S
    monkeypatch.setenv("KNOWLEDGE_POST", "false")
    url = "https://x/C/P/_git/repo-a/pullrequest/90"
    fresh = S.post_knowledge_draft(url, "body")
    reply = S.post_knowledge_draft(url, "body", thread_id=7)
    assert fresh["would_post_to"].endswith("/threads")
    assert reply["would_post_to"].endswith("/threads/7/comments")
    assert reply["reply"] is True and reply["dry_run"] is True
    assert "Draft updated after new commits" in reply["preview"]


def test_reviewer_knowledge_ref_shape_is_coerced_not_fatal(monkeypatch):
    """引用欄位寫歪（單值 / 混型別 / 亂形狀）不該讓整條 finding 被 pydantic 丟掉。"""
    from orchestrator import pipeline as P
    from orchestrator.rules import RulesBundle
    ana = parse_diff("--- a/src/A.java\n+++ b/src/A.java\n@@ -1,1 +1,2 @@\n a\n+b\n")

    async def fake(system, user, **kw):
        return json.dumps({"assessment": "a", "findings": [
            {"rule": "BUG", "file": "src/A.java", "line": 2,
             "severity": "warning", "note": "n1", "knowledge_ref": "PR1-I1"},
            {"rule": "PERF", "file": "src/A.java", "line": 2, "severity": "info",
             "note": "n2", "knowledge_ref": [12, "PR1-I2", ""]},
            {"rule": "NAMING", "file": "src/A.java", "line": 2, "severity": "info",
             "note": "n3", "knowledge_ref": {"bad": "shape"}}]})

    monkeypatch.setattr(P, "complete", fake)
    monkeypatch.setattr(P, "prompt_or", lambda name, default: default)
    dim = P._Dim(name="X", focus="f", hypotheses=[], files=[], rules=[])
    _assessment, finds, _checks, _loc = asyncio.run(
        P._review_one(dim, ana, RulesBundle(), "", ""))
    assert [f.knowledge_ref for f in finds] == [["PR1-I1"], ["12", "PR1-I2"], []]


def test_redistill_does_not_resurrect_a_dropped_draft(kroot, monkeypatch):
    """收割器在重蒸期間判死這條稿（重試用盡 / 世代變更）→ 重蒸不得把它寫回來。"""
    from orchestrator import knowledge as K
    pend = _pending(kroot)
    diff = ("--- a/src/x/F.java\n+++ b/src/x/F.java\n@@ -10,3 +10,4 @@\n"
            " c10\n+a11\n c12\n c13\n")

    async def f_meta(u):
        return {"title": "t", "description": "d" * 60, "source_commit": "sha-NEW",
                "target_branch": "release/x"}

    async def f_diff(u):
        pend.unlink()                 # 蒸到一半，收割器把稿丟了
        return diff

    async def f_post(pr_url, markdown, thread_id=None):
        return {"posted": True, "dry_run": False, "thread_id": thread_id}

    async def f_complete(system, user, **kw):
        return json.dumps({"summary": "s", "type": "bugfix",
                           "intent_stated": True, "primary_entity": "src/x/F.java",
                           "decisions": [{"text": "d", "evidence": ["F.java:11"]}],
                           "invariants": [], "supersedes": [], "affected": [],
                           "ledger_line": "l"})

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(K.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(K.clients, "post_knowledge_draft", f_post)
    monkeypatch.setattr(K, "prompt_or", lambda name, default: "SYSTEM")
    monkeypatch.setattr(K, "complete", f_complete)

    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))
    assert not pend.exists()


def test_post_knowledge_draft_forwards_thread_id_to_the_adapter(monkeypatch):
    """S1 之後 ③ 住在同一個行程：clients 只是 to_thread 的殼，thread_id 必須原樣到 adapter
    （重蒸的更新稿要回進作者回過話的那串，不能開新 thread）。"""
    from orchestrator import clients
    from orchestrator.adapters import ado
    sent = []
    monkeypatch.setattr(ado, "post_knowledge_draft",
                        lambda pr_url, md, thread_id=None: sent.append((pr_url, md, thread_id))
                        or {"posted": True})
    asyncio.run(clients.post_knowledge_draft("u", "md"))
    asyncio.run(clients.post_knowledge_draft("u", "md", thread_id=7))
    assert sent == [("u", "md", None), ("u", "md", 7)]


def test_cycle_close_first_sight_does_not_backfill(kroot, monkeypatch):
    """首見水位：歷史上早就 completed 的驗收 PR 不得收口當下暫存的稿。

    「哪些驗收 PR 結算過」只記在 _state/cycle.json，而它住在 KNOWLEDGE_ROOT——
    帳本重新 clone、_state 掉了、或清空 KB 重測，記憶就沒了，但 ADO 的歷史
    一條都沒少。少了這個判斷，上個月的驗收 PR 會把今天剛暫存的稿立刻落地，
    而那批稿沒有任何人在任何 digest 上看過。
    """
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    completed = [{"pullRequestId": 400, "sourceRefName": "refs/heads/release/x",
                  "targetRefName": "refs/heads/master",
                  "closedDate": "2026-01-01T00:00:00Z"}]        # 上一輪的
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: completed)

    _stage(kroot, 70, "src/x/A.java")                # 今天剛暫存的稿
    lines = H._cycle_close(object(), kroot)
    assert any("首見" in ln and "不回填" in ln for ln in lines)
    assert not (kroot / "repo-a" / "x" / "A.md").exists()        # 沒有被落地
    assert (kroot / "_cycle" / "repo-a" / "pr-70.json").exists()  # 稿還在池裡
    st = json.loads((kroot / "_state" / "cycle.json").read_text())
    assert st["repo-a#400"]["closed"] is True                   # 但不會再問一次

    # 之後真的有一條驗收 PR 完成 → 照常收口
    completed.append({"pullRequestId": 401,
                      "sourceRefName": "refs/heads/release/x",
                      "targetRefName": "refs/heads/master", "closedDate": "z"})
    lines = H._cycle_close(object(), kroot)
    assert any("驗收 PR#401 收口" in ln for ln in lines)
    assert (kroot / "repo-a" / "x" / "A.md").exists()
    assert not (kroot / "_cycle" / "repo-a" / "pr-70.json").exists()


def test_retired_invariants_stay_on_the_page_but_leave_the_injection(kroot):
    """註銷是標記不是蒸發——頁面留著，但注入給 reviewer 的區塊不帶它。

    帶著它，模型會照樣拿一條死掉的規則報 violation（作者明明已經說那個
    business 需求取消了），而誤報比漏報更快把信任燒光。
    """
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants, load_for
    H._land(kroot, "repo-a", 10,
            _draft("src/a/Fee.java", invariants=["Fee must be capped at 5%",
                                                 "Fee is always BigDecimal"]),
            "cycle/silent", "")
    H._land(kroot, "repo-a", 11,
            _draft("src/a/Fee.java",
                   supersedes=[{"entity": "Fee", "what": "cap removed",
                                "retires_id": "PR10-I1"}]),
            "cycle/explicit", "business requirement dropped")

    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "~~Fee must be capped at 5%~~ ⚠ retired by PR#11" in page   # 血緣留著
    assert existing_invariants("repo-a", ["src/a/Fee.java"])[0][1] == [
        ("PR10-I2", "Fee is always BigDecimal")]       # 目錄早就不列它

    text, stats = load_for("repo-a", ["src/a/Fee.java"])
    assert "Fee must be capped" not in text            # 注入不帶死規則
    assert "Fee is always BigDecimal" in text          # 活的照常
    assert stats["knowledge_ids"] == ["PR10-I2"]       # 也不能拿它當 grounding
    assert stats["knowledge_retired_hidden"] == 1      # 擋掉幾條，攤在 stats
    assert "business requirement dropped" in text      # 作者原話仍在（是脈絡）


# ========== 矛盾裁決：實作推翻規則 → 驗收窗決定拔不拔 ==========

def _seed_rule(kroot, entity="src/a/Fee.java", text="Fee must be capped at 5%"):
    """先讓一條規則落地，回它的 id。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 10, _draft(entity, invariants=[text]),
            "cycle/silent", "")
    return "PR10-I1"


def _stage_contradicting(kroot, pid, iid, ent="src/a/Fee.java"):
    from orchestrator.knowledge import write_json_atomic
    write_json_atomic(kroot / "_cycle" / "repo-a" / f"pr-{pid}.json", {
        "pr_url": "u", "pr_id": pid, "repo": "repo-a",
        "base_url": "https://x/C/P", "target_branch": "release/x",
        "draft": _draft(ent, invariants=[f"new rule from PR{pid}"]),
        "author_note": "the business requirement was dropped",
        "provenance": "explicit", "contradicted": [iid]})


def _cycle_env(kroot, monkeypatch, threads=()):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir(exist_ok=True)
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見
        json.dumps({"repo-a#1": {"closed": True}}))
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: list(threads))
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])


def test_contradicted_invariant_is_retired_at_cycle_close(kroot, monkeypatch):
    """帶著矛盾完成的 PR = 實作推翻了規則；沉默即接受 → 收口時畫掉那條。"""
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants, load_for
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 70, iid)
    _cycle_env(kroot, monkeypatch)

    lines = H._cycle_close(object(), kroot)
    assert any(f"拔除 {iid}" in ln for ln in lines)
    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "~~Fee must be capped at 5%~~ ⚠ retired by PR#70" in page   # 畫掉不刪除
    assert "retired `PR10-I1`: contradicted by merged code" in page    # History 留行
    assert "new rule from PR70" in page                                # 新條目照樣落地
    # 之後兩條讀取路徑都不再帶它
    assert all(i != iid for _, pairs in existing_invariants(
        "repo-a", ["src/a/Fee.java"]) for i, _ in pairs)
    assert "Fee must be capped" not in load_for("repo-a", ["src/a/Fee.java"])[0]
    receipt = next((kroot / "repo-a" / "_cycles").glob("*-PR500.md")).read_text(
        encoding="utf-8")
    assert "Invariants retired this cycle" in receipt


def test_keep_command_vetoes_the_retirement(kroot, monkeypatch):
    """任何人回 `keep PR10-I1` → 本週期不拔，規則原封不動。"""
    from orchestrator import knowledge_harvest as H
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 71, iid)
    _cycle_env(kroot, monkeypatch, threads=[{"id": 9, "comments": [
        {"content": "keep PR10-I1\n這條還要，只是這支例外"}]}])

    lines = H._cycle_close(object(), kroot)
    assert any(f"矛盾 {iid} 保留" in ln for ln in lines)
    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "- Fee must be capped at 5% `PR10-I1`" in page      # 沒被動過
    assert "~~" not in page


def test_contradiction_retirement_can_be_switched_off(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setenv("KNOWLEDGE_RETIRE_ON_CONTRADICTION", "false")
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 72, iid)
    _cycle_env(kroot, monkeypatch)
    lines = H._cycle_close(object(), kroot)
    assert not any("拔除" in ln for ln in lines)
    assert "~~" not in (kroot / "repo-a" / "a" / "Fee.md").read_text(
        encoding="utf-8")


def test_retire_by_id_refuses_when_the_id_is_ambiguous_or_absent(kroot):
    from orchestrator import knowledge_harvest as H
    _seed_rule(kroot)
    assert "查無此 ID" in H._retire_by_id(kroot, "repo-a", "PR99-I9", 70)
    # 同一個 ID 出現在兩頁（不該發生，但真發生時寧可不動）
    H._land(kroot, "repo-a", 11, _draft("src/b/Other.java",
                                        invariants=["dup"]), "cycle/silent", "")
    p = kroot / "repo-a" / "b" / "Other.md"
    p.write_text(p.read_text(encoding="utf-8").replace("`PR11-I1`", "`PR10-I1`"),
                 encoding="utf-8")
    assert "撞到 2 頁" in H._retire_by_id(kroot, "repo-a", "PR10-I1", 70)


def test_digest_shows_contradictions_as_a_proposal(kroot):
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 70, "provenance": "explicit", "author_note": "req dropped",
              "contradicted": ["PR10-I1"],
              "draft": _draft("src/a/Fee.java", invariants=["new rule"])}]
    md = D.render(items, None, None)
    assert "Invariants contradicted by merged code" in md
    assert "`PR10-I1`" in md and "keep PR10-I1" in md


def test_review_hands_the_distiller_its_diff_ids_and_contradictions(monkeypatch):
    """app → distiller 的接線：同一份 diff、F 編號、被踩到的不變式 id。

    這段是純接線，最容易在改動裡安靜斷掉（斷了也只會 print 一行 journal），
    所以用一條測試把四個參數釘住。
    """
    from orchestrator import app as A
    from orchestrator.models import Finding
    A._RECENT.clear()
    captured = {}

    async def f_meta(u):
        return {"repo": "r", "title": "t", "source_commit": "sha-X",
                "target_branch": "refs/heads/release/x"}

    async def f_diff(u):
        return "--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"

    async def f_run(diff, md):
        blocking = Finding(rule="BUG", file="x.java", line=2, severity="error",
                           note="violates the cap rule")
        blocking.knowledge_ref = ["PR41-I1"]
        advisory = Finding(rule="STYLE", file="x.java", line=2, severity="info",
                           note="nit")
        advisory.knowledge_ref = ["PR41-I2"]        # info 不算矛盾
        return types.SimpleNamespace(
            markdown="## Pull Request Review OK", findings=[blocking, advisory],
            stats={"file_table": [{"path": "x.java"}], "graph_impacted": []})

    async def f_distill(*a, **k):
        captured.update(k)
        captured["files"] = a[2]

    monkeypatch.setattr(A.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(A.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(A, "run_review", f_run)
    monkeypatch.setattr(A.knowledge, "distill_and_stage", f_distill)
    monkeypatch.setenv("AUTO_POST", "false")

    async def go():
        await A.handle_review("https://x/C/P/_git/R/pullrequest/9", "u")
        await asyncio.sleep(0)          # 讓 _bg 排的那條真的跑到

    asyncio.run(go())
    assert captured["finding_ids"] == {"F1", "F2"}
    assert captured["finding_files"] == ["x.java", "x.java"]
    assert captured["diff"].startswith("--- a/x.java")
    assert captured["contradicted"] == {"PR41-I1"}    # 只有 error/warning 算
