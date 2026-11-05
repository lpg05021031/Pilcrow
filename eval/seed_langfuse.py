"""播種：把六份 system prompt 推上 Langfuse 受版控 + 建/擴三套考題。

三套考題（兩個 dataset）：
  pr-review-golden          30 題 review 金題 + 5 題知識依賴題（kfix-*）
  pr-review-distill-golden  10 題蒸餾考卷（含三題陷阱）

用法：python -m eval.seed_langfuse
（LANGFUSE_HOST / PUBLIC_KEY / SECRET_KEY 由環境變數提供）

冪等規則：
- prompts 每跑必推新版本（掛 production label）—— 只在「code 內建 prompt 有
  更新的部署輪」跑；日常迭代走 Langfuse UI，跑 seed 會把 UI 修改蓋掉。
- dataset items 以 metadata.fixture 為鍵「逐條冪等」：已存在的跳過、只補新的
  —— fixtures 目錄加了新題，重跑 seed 就只灌新題。

金題設計（Java / JPA / SQL / Spring Boot）：
  static 保底題（101–103）  semgrep 必中 → recall 地板
  LLM 邏輯題（104,106,107） 語意缺陷，static 看不見 → 量 reviewer 腦力
  已知難題（105,108,111）   MISSING-TEST / NAMING / 跨檔推理 → 進步空間的量尺
  乾淨負樣本（109,110）     expected 為空 → 量誤報；110 專防「重構被嫌沒測試」
日常養資料動線：Traces → Add to dataset → 整形成 v2 shape（見 run_eval.py）。
"""
from __future__ import annotations

from pathlib import Path

from langfuse import get_client

from orchestrator.core import DEFAULT_SYSTEM_PROMPT, PROMPT_NAME
from orchestrator.knowledge import DEFAULT_DISTILLER_PROMPT, DISTILLER_PROMPT_NAME
from orchestrator.knowledge_digest import DEFAULT_DIGEST_PROMPT, DIGEST_PROMPT_NAME
from orchestrator.pipeline import (
    ADVERSARY_PROMPT_NAME, DEFAULT_ADVERSARY_PROMPT,
    DEFAULT_PLANNER_PROMPT, DEFAULT_REVIEWER_PROMPT,
    PLANNER_PROMPT_NAME, REVIEWER_PROMPT_NAME,
)

DATASET = "pr-review-golden"
FIXTURES = Path(__file__).parent / "fixtures"

_J = "src/main/java/com/acme"
_FORBID = ["LGTM"]


def _case(fname, expected, allowed_extra, kind, focus, hard=False):
    exp = {"expected": expected, "allowed_extra": allowed_extra,
           "forbidden": _FORBID}
    meta = {"fixture": fname, "kind": kind, "focus": focus}
    if hard:
        meta["hard"] = True
    if fname.startswith("pr-1"):
        meta["lang"] = "java"
    return fname, exp, meta


CASES = [
    # 註：原本的 pr-129 / pr-130（41 / 46 檔的截斷路徑考題）已移除——切片預算與
    # 科數調大之後，它們不再踩得到預算，考的東西名存實亡。日後要再考截斷路徑，
    # 得照新的預算重新生 fixture，並在 preflight._GEOMETRY 重新登記斷言。
    # ---- 既有 python 種題 ----
    _case("pr-002.diff",
          [{"rule": "BUG", "file": "src/calculator.py"},
           {"rule": "SECURITY", "file": "src/api.py"},
           {"rule": "MISSING-TEST"}],   # 類別級：掛哪個檔都算對
          2, "planted", "python-baseline"),
    _case("pr-003-clean.diff", [], 0, "clean", "python-clean"),
    # ---- static 保底（semgrep 必中 = recall 地板） ----
    _case("pr-101-jdbc-injection.diff",
          [{"rule": "SECURITY", "file": f"{_J}/user/UserRepository.java"},
           {"rule": "SECURITY", "file": f"{_J}/user/UserRepository.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "jdbctemplate-concat+format"),
    _case("pr-102-mybatis-dollar.diff",
          [{"rule": "SECURITY", "file": f"{_J}/order/OrderMapper.java"}],
          2, "planted", "mybatis-dollar-substitution"),
    _case("pr-103-catch-secret.diff",
          [{"rule": "SECURITY", "file": f"{_J}/payment/PaymentClient.java"},
           {"rule": "BUG", "file": f"{_J}/payment/PaymentClient.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-secret+empty-catch"),
    # ---- LLM 邏輯題（static 看不見） ----
    _case("pr-104-logic-boundary.diff",
          [{"rule": "BUG", "file": f"{_J}/pricing/PricingService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "off-by-one-vs-javadoc"),
    _case("pr-106-npe-optional.diff",
          [{"rule": "BUG", "file": f"{_J}/user/UserService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "optional-get-vs-null-contract"),
    _case("pr-107-transaction.diff",
          [{"rule": "BUG", "file": f"{_J}/transfer/TransferService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "spring-missing-transactional"),
    # ---- 已知難題（現階段 recall 缺口的量尺，不是 bug） ----
    _case("pr-105-missing-test.diff",
          [{"rule": "MISSING-TEST", "file": f"{_J}/auth/TokenService.java"}],
          2, "planted", "new-behavior-no-test", hard=True),
    _case("pr-108-naming.diff",
          [{"rule": "NAMING", "file": f"{_J}/account/AccountService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "map-named-list+double-negative", hard=True),
    _case("pr-111-crossfile-units.diff",
          [{"rule": "BUG", "file": f"{_J}/cache/SessionCache.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "cross-file-unit-mismatch", hard=True),
    # ---- 乾淨負樣本（量誤報；110 專防「重構被嫌沒測試」） ----
    _case("pr-109-clean-endpoint.diff", [], 0, "clean",
          "endpoint-with-test"),
    _case("pr-110-clean-refactor.diff", [], 0, "clean",
          "pure-refactor-no-behavior-change"),
    # ---- 敏感 log（112/113 有 static 保底；114 純 LLM/合規語境） ----
    _case("pr-112-log-password.diff",
          [{"rule": "SECURITY", "file": f"{_J}/auth/LoginService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "password-in-log"),
    _case("pr-113-log-auth-header.diff",
          [{"rule": "SECURITY", "file": f"{_J}/web/RequestLogFilter.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "authorization-header-in-log"),
    _case("pr-114-log-pii.diff",
          [{"rule": "SECURITY", "file": f"{_J}/user/ProfileService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "pii-in-log-vs-compliance-note"),
    _case("pr-115-clean-masked-log.diff", [], 0, "clean",
          "masked-logging-with-test"),
    # ---- hardcode（118 有 static 保底；116/117 對照 javadoc 的意圖矛盾） ----
    _case("pr-116-hardcoded-config.diff",
          [{"rule": "BUG", "file": f"{_J}/sync/SyncScheduler.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-interval-vs-configurable-contract"),
    _case("pr-117-hardcoded-path.diff",
          [{"rule": "BUG", "file": f"{_J}/export/ReportExporter.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-path-vs-readonly-container"),
    _case("pr-118-ecb-cipher.diff",
          [{"rule": "SECURITY", "file": f"{_J}/crypto/FieldEncryptor.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "ecb-cipher-mode"),
    # ---- 簡單題（120 有 static 保底） ----
    _case("pr-119-obvious-npe.diff",
          [{"rule": "BUG", "file": f"{_J}/config/FeatureFlags.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "npe-on-absent-property"),
    _case("pr-120-string-eq.diff",
          [{"rule": "BUG", "file": f"{_J}/order/OrderStateMachine.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "string-reference-equality"),
    _case("pr-121-interrupted.diff",
          [{"rule": "BUG", "file": f"{_J}/worker/RetryHelper.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "swallowed-interrupt-no-reinterrupt"),
    _case("pr-122-resource-leak.diff",
          [{"rule": "BUG", "file": f"{_J}/imports/CsvLoader.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "reader-not-closed"),
    _case("pr-123-clean-equals.diff", [], 0, "clean",
          "equals-hashcode-with-test"),
    # ---- 更多情況（併發 / Spring 暗坑 / PERF / API 契約；後三題標 hard） ----
    _case("pr-124-shared-dateformat.diff",
          [{"rule": "BUG", "file": f"{_J}/report/DateFormats.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "shared-simpledateformat-threads"),
    _case("pr-125-tx-self-invocation.diff",
          [{"rule": "BUG", "file": f"{_J}/billing/InvoiceService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "transactional-self-invocation", hard=True),
    _case("pr-126-n-plus-one.diff",
          [{"rule": "PERF", "file": f"{_J}/user/BulkLookupService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "n-plus-one-lookup", hard=True),
    _case("pr-127-api-contract.diff",
          [{"rule": "BUG"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "frozen-api-field-rename", hard=True),
    _case("pr-128-clean-fp-trap.diff", [], 0, "clean",
          "password-words-but-nothing-sensitive"),
]

# ---- 知識依賴題（Phase 2.2）：沒讀帳本就抓不到的缺陷 ----
#
# 三題「單頁」（kfix-1..3）：帳本只有一頁，注入必完整 —— 量的是 consumption
# 純度（reviewer 有沒有真的用上它、有沒有引 id）。
# 兩題「distractor」（kfix-4/5）：同 repo 另有 4–5 頁其他實體的頁，注入總量
# 推過 KNOWLEDGE_MAX_CHARS —— 量的是排序與預算有沒有參與（tier 4 保證入場、
# 降載成 invariants-only 而不是整條蒸發）。
# 兩型分開出分：delta=0 時才判得出是 consumption 問題還是排序問題。
KNOWLEDGE_DIR = FIXTURES / "knowledge_cases"


def _kcase(name, repo, title, description, expected, allowed_extra, kind, focus):
    return (name, repo, title, description,
            {"expected": expected, "allowed_extra": allowed_extra,
             "forbidden": _FORBID},
            {"fixture": name, "kind": "knowledge", "ktype": kind, "focus": focus,
             "lang": "java"})


KNOWLEDGE_CASES = [
    _kcase("kfix-1.diff", "kfix-1",
           "Apply the configured session timeout at startup",
           "The session TTL has been hard-coded since the edge rewrite. This "
           "change reads session.ttl.seconds from the properties file and "
           "pushes it into the cache when the loader starts, so operations can "
           "tune it per environment without a rebuild.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/session/SessionConfigLoader.java",
             "knowledge_ref": "PR41-I1"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "seconds passed into a millisecond boundary — only the ledger says so"),
    _kcase("kfix-2.diff", "kfix-2",
           "Retry transient charge failures",
           "Transient provider errors currently surface to the customer as a "
           "failed checkout. This adds a bounded retry with exponential backoff "
           "around the charge call.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/retry/RetryExecutor.java",
             "knowledge_ref": "PR52-I1"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "fresh idempotency key per attempt — double charge; ledger knows"),
    _kcase("kfix-3.diff", "kfix-3",
           "Move the cancellation audit write off the request thread",
           "Cancellation latency is dominated by the audit insert. The audit "
           "table is not on the hot path, so the write is now dispatched "
           "asynchronously.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/orders/OrderService.java",
             "knowledge_ref": "PR63-I2"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "audit escapes the mutating transaction — module page carries the rule"),
    _kcase("kfix-4.diff", "kfix-4",
           "Report the protocol name from each handler and route on it",
           "Each frame handler now reports the protocol it speaks so the router "
           "can prefer protocol-scoped zone entries over the plain zone entry.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/routing/ZoneRouter.java",
             "knowledge_ref": "PR75-I1"},
            {"rule": "MISSING-TEST"}],
           4, "distractor",
           "string reference equality; 6 matched pages blow the budget and the "
           "load-bearing page sorts last (tier-4 guarantee must save it)"),
    _kcase("kfix-5.diff", "kfix-5",
           "Add the tiered fee schedule",
           "This release introduces tiered fees. The calculator gains a tiered "
           "rate path, the writer appends the fee entry alongside its parent "
           "transaction, and statements render the tier label.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/ledger/FeeCalculator.java",
             "knowledge_ref": "PR85-I3"},
            {"rule": "MISSING-TEST"}],
           4, "distractor",
           "double round-trip on money; the rule lives on a neighbour page that "
           "only survives the budget as invariants-only"),
]


# ---- 蒸餾考卷（Phase 4）：K 段的 LLM 輸出從此也押著考題 ----
#
# review pipeline 有 30 題押著，distiller 過去零考卷——改它的 prompt 等於裸奔。
# 三題陷阱刻意佔三成：why 缺席（編 rationale 就扣）、supersede 假訊號（推論就
# 扣）、幻覺不變式（材料撐不起 invariant 還蒸出來就扣）。invariants 不做
# evidence 驗證是明文接受的殘餘風險，第三題就是它的統計兜底。

DISTILL_DATASET = "pr-review-distill-golden"
DISTILL_DIR = FIXTURES / "distill"


def _dcase(fname, title, description, expected, focus, catalogue=None):
    return (fname, title, description, catalogue or [], expected,
            {"fixture": fname, "kind": "distill", "focus": focus})


DISTILL_CASES = [
    _dcase("d1-ttl-unit.diff",
           "Make the cache TTL boundary explicitly millisecond-based",
           "setTtl took an int of seconds while the field it wrote was already "
           "milliseconds, so every configured TTL expired a thousand times too "
           "early. We considered an overloaded setTtl(value, unit) instead, but "
           "rejected it: autoboxing picked the wrong overload at two call sites "
           "during the 2026.04 incident. The boundary is milliseconds, full "
           "stop, and it now rejects values below the floor.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["millisecond"],
            "primary_entity": "src/main/java/com/acme/cache/SessionCache.java"},
           "ordinary entry with a stated why and a rejected alternative"),
    _dcase("d2-retry-revert.diff",
           "Stop retrying writes in WriteClient",
           "This retires the write-retry policy introduced in PR#31. Retrying a "
           "non-idempotent write is what turned last month's partial outage into "
           "duplicated orders, so writes are now sent exactly once and the "
           "caller decides what a failure means. The rule that writes are "
           "retried up to five times no longer holds.",
           {"intent_stated": True, "retires_id": "PR31-I1",
            "must_mention": ["retr"],
            "primary_entity": "src/main/java/com/acme/http/WriteClient.java"},
           "explicit supersede — retires_id must hit the seeded id exactly",
           catalogue=[("acme/http/WriteClient.md", [
               ("PR31-I1", "Writes are retried up to five times with jitter; "
                           "callers must not implement their own retry loop."),
               ("PR31-I2", "The client never blocks the caller thread for more "
                           "than the configured request timeout.")])]),
    _dcase("d3-batch-size.diff",
           "Raise the import batch size to 500",
           "Bump BATCH_SIZE from 100 to 500.",
           {"intent_stated": False, "retires_id": None,
            "must_mention": ["batch"],
            "forbidden_mention": ["because", "in order to", "rationale",
                                  "so that"],
            "primary_entity":
                "src/main/java/com/acme/importer/BatchLoader.java"},
           "TRAP why-absent — description is pure what; inventing a why is a miss"),
    _dcase("d4-flag-cache.diff",
           "Batch feature-flag reads per request",
           "Reading flags one by one costs a store round-trip each. readAll "
           "fetches the whole set a request needs in one call. We talked about "
           "caching flags per instance instead, which would be faster still, "
           "but that is not what this change does — flags are still read from "
           "the store on every request.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["flag"],
            "primary_entity":
                "src/main/java/com/acme/config/FeatureFlags.java"},
           "TRAP supersede false signal — sounds like a reversal, never states one",
           catalogue=[("acme/config/FeatureFlags.md", [
               ("PR44-I1", "Feature flags are read per request, never cached "
                           "for the lifetime of an instance."),
               ("PR44-I2", "A flag that cannot be read fails closed.")])]),
    _dcase("d5-typo-only.diff",
           "Fix spelling in ReportBuilder",
           "Typos in a javadoc block and a TODO comment.",
           {"intent_stated": False, "retires_id": None, "must_mention": [],
            "forbid_invariants": True,
            "primary_entity":
                "src/main/java/com/acme/report/ReportBuilder.java"},
           "TRAP hallucinated invariant — nothing here constrains future change"),
    _dcase("d6-cache-library.diff",
           "Move LookupCache from Guava to Caffeine",
           "Guava's cache has no hit-rate instrumentation we can export, and we "
           "have been guessing at the sizing for two quarters. We chose "
           "Caffeine over both Guava and a hand-rolled map because it gives us "
           "recordStats() for free and keeps the same eviction semantics, so "
           "this is a drop-in with metrics rather than a behaviour change.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["caffeine", "guava"],
            "primary_entity": "src/main/java/com/acme/cache/LookupCache.java"},
           "rejected alternatives named in the description — PR evidence carries it"),
    _dcase("d7-sql-parameterize.diff",
           "Parameterize findByName",
           "The name argument was concatenated into the SQL string. It is now a "
           "bound parameter. Reported by the review bot on the previous PR; the "
           "same pattern exists nowhere else in this repository, which we "
           "checked before merging.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["parameter"],
            "primary_entity":
                "src/main/java/com/acme/user/UserRepository.java"},
           "security fix — evidence must resolve to lines in the excerpt"),
    _dcase("d8-api-compat.diff",
           "Expose available_balance and deprecate balance",
           "Partners read balance as if it were spendable, which it is not once "
           "holds are counted. available_balance is the new field. balance "
           "stays in the payload until 2027-01-01 because two partner "
           "integrations cannot ship a client change before then; removing it "
           "earlier is a breaking change we have already promised not to make.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["2027"],
            "primary_entity":
                "src/main/java/com/acme/api/AccountResponse.java"},
           "compatibility window — a date a diff alone cannot show"),
    _dcase("d9-primary-entity.diff",
           "Acquire both lock keys in a fixed order",
           "Two callers acquiring the same pair of keys in opposite orders "
           "deadlocked in production. acquireBoth sorts the keys first, which "
           "makes the order global. The integration test reproduces the old "
           "deadlock under contention.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["order"],
            "primary_entity": "src/main/java/com/acme/lock/LockService.java"},
           "the longest path is the test file — the page must anchor on the service"),
    _dcase("d10-two-decisions.diff",
           "Stream CSV export instead of building it in memory",
           "Month-end exports for the largest accounts exceeded the heap. The "
           "exporter now writes into a caller-supplied stream and flushes every "
           "FLUSH_EVERY rows. We deliberately do not close the sink: the caller "
           "owns it, and closing a servlet output stream here truncated the "
           "response during the first attempt.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["stream", "flush"],
            "primary_entity": "src/main/java/com/acme/export/CsvExporter.java"},
           "two load-bearing decisions, one of them a negative decision"),
]


def seed_distill_dataset(lf) -> None:
    """蒸餾考卷 —— 與 golden set 同樣的逐條冪等規則（metadata.fixture 為鍵）。"""
    from orchestrator.knowledge import catalogue_text

    lf.create_dataset(name=DISTILL_DATASET)
    existing: set[str] = set()
    try:
        for it in lf.get_dataset(DISTILL_DATASET).items:
            if "ARCHIVED" in str(getattr(it, "status", "") or "").upper():
                continue
            m = it.metadata or {}
            if isinstance(m, dict) and m.get("fixture"):
                existing.add(str(m["fixture"]))
    except Exception:
        pass

    added = skipped = missing = 0
    for fname, title, desc, catalogue, expected, meta in DISTILL_CASES:
        if fname in existing:
            skipped += 1
            continue
        path = DISTILL_DIR / fname
        if not path.exists():
            print(f"略過 {fname}（fixtures/distill 裡沒有這個檔）")
            missing += 1
            continue
        # 目錄用 production 的同一支 renderer 排版：考卷看到的格式 = 真的那份
        cat = catalogue_text(
            [(rel, pairs) for rel, pairs in catalogue],
            "# Existing ledger invariants for the entities this PR touches\n"
            "# (supersede detection only — do NOT restate these as your own "
            "decisions; put the bracketed id in retires_id if one is retired)\n")
        lf.create_dataset_item(
            dataset_name=DISTILL_DATASET,
            input={"diff": path.read_text(encoding="utf-8"), "title": title,
                   "description": desc, "catalogue": cat},
            expected_output=expected,
            metadata=meta,
        )
        added += 1
    lf.flush()
    print(f"dataset '{DISTILL_DATASET}'：新增 {added} 條、已存在跳過 {skipped} 條"
          + (f"、缺檔 {missing} 條" if missing else ""))


def main() -> None:
    lf = get_client()

    # 1) prompts 資產化（UI 改版、code 零改動；每跑必升版）
    prompts = {
        PROMPT_NAME: DEFAULT_SYSTEM_PROMPT,             # v1 單呼叫
        PLANNER_PROMPT_NAME: DEFAULT_PLANNER_PROMPT,    # v2 規劃者
        REVIEWER_PROMPT_NAME: DEFAULT_REVIEWER_PROMPT,  # v2 通用 reviewer
        ADVERSARY_PROMPT_NAME: DEFAULT_ADVERSARY_PROMPT,  # v2 對抗者
        DISTILLER_PROMPT_NAME: DEFAULT_DISTILLER_PROMPT,  # K2 知識蒸餾
        DIGEST_PROMPT_NAME: DEFAULT_DIGEST_PROMPT,        # 週期 digest 顧問分析
    }
    for name, text in prompts.items():
        lf.create_prompt(name=name, type="text", prompt=text,
                         labels=["production"])
        print(f"prompt '{name}' 已推上 Langfuse（label: production）")

    # 2) golden dataset —— 逐條冪等（以 metadata.fixture 為鍵）
    lf.create_dataset(name=DATASET)
    existing: set[str] = set()
    try:
        for it in lf.get_dataset(DATASET).items:
            if "ARCHIVED" in str(getattr(it, "status", "") or "").upper():
                continue          # archive 過的不算存在 —— 允許重灌
            m = it.metadata or {}
            if isinstance(m, dict) and m.get("fixture"):
                existing.add(str(m["fixture"]))
    except Exception:
        pass

    added = skipped = missing = 0
    for fname, expected, meta in CASES:
        if fname in existing:
            skipped += 1
            continue
        path = FIXTURES / fname
        if not path.exists():
            print(f"略過 {fname}（fixtures 裡沒有這個檔）")
            missing += 1
            continue
        lf.create_dataset_item(
            dataset_name=DATASET,
            input={"diff": path.read_text(encoding="utf-8")},
            expected_output=expected,
            metadata=meta,
        )
        added += 1

    # 知識依賴題：同一個 dataset，但 input 多帶 repo（= 種子帳本目錄）與
    # description（沒有 description 就沒有「意圖 vs 實作」的張力可考）
    kadded = 0
    for fname, repo, title, desc, expected, meta in KNOWLEDGE_CASES:
        if fname in existing:
            skipped += 1
            continue
        path = KNOWLEDGE_DIR / fname
        if not path.exists():
            print(f"略過 {fname}（knowledge_cases 裡沒有這個檔）")
            missing += 1
            continue
        lf.create_dataset_item(
            dataset_name=DATASET,
            input={"diff": path.read_text(encoding="utf-8"), "repo": repo,
                   "title": title, "description": desc},
            expected_output=expected,
            metadata=meta,
        )
        kadded += 1
    lf.flush()
    print(f"dataset '{DATASET}'：新增 {added} 條 review 題 + {kadded} 條知識依賴題、"
          f"已存在跳過 {skipped} 條"
          + (f"、缺檔 {missing} 條" if missing else "")
          + " —— 去 UI 的 Datasets 確認")
    seed_distill_dataset(lf)


if __name__ == "__main__":
    main()
