"""全套系統唯一的設定讀取點 —— 64 個環境變數的 schema、預設值與說明住這裡。

為什麼放 shared/：shared 是「笨層」，orchestrator / tools / eval / harvester
都可以 import 它，而它不 import 任何業務模組（也不 import langfuse）。

設計決定：
  變數名不動       每個欄位的 alias 就是既有的環境變數名；部署好的 env 檔
                   一個字都不用改。日後要改名，在這裡加 AliasChoices 即可。
  三層來源         env > config.toml > 預設。env 仍是最高優先（測試的
                   monkeypatch.setenv、run_eval 的對照組都靠它）；config.toml
                   是控制台 Settings 頁寫的檔（CONFIG_DIR/config.toml，S2 起）；
                   secrets 永遠只走 env（systemd EnvironmentFile=secrets.env），
                   toml 裡出現 SecretStr 欄位會被忽略並警告。
  惰性 + 可清快取  settings() 第一次呼叫才讀；reset() 清掉。tests/conftest.py
                   把 monkeypatch.setenv 綁上 reset()，既有測試零改動。
  空字串 = 未設    KNOWLEDGE_ROOT="" 是 run_eval 關掉知識迴圈的正式手法；
                   所有欄位對空字串一律當「未設、用預設值」。
  壞值就炸         型別不對（REVIEW_MAX_FINDINGS=abc）在啟動時報錯，不再
                   靜默退回預設值。app / tools / harvester 啟動時呼叫 fail_fast()。
  衍生判斷收進來   knowledge.enabled / graph.enabled / static.semgrep_enabled
                   這類「未設 = 停用」只寫一次。

用法：
  from shared.settings import settings
  settings().review.max_findings

  python -m shared.settings --show      目前生效值與來源（secrets 遮罩）
  python -m shared.settings --example   由 schema 產生 suite.env.example（全 env 版）
  python -m shared.settings --example-toml  由 schema 產生 config.toml 樣板
  python -m shared.settings --migrate   把目前 env 裡非預設、非 secret 的值寫成 config.toml，
                                        並印出該留在 secrets.env 的那幾行
  python -m shared.settings --check     只驳證；壞值回傳碼 1
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import tomllib
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, Literal

from pydantic import (AliasChoices, BaseModel, BeforeValidator, Field,
                      SecretStr, ValidationError, model_validator)
from pydantic.fields import FieldInfo
from pydantic_settings import (BaseSettings, NoDecode, PydanticBaseSettingsSource,
                               SettingsConfigDict)

# ---------- 共用的欄位型別 ----------

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def _lower(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _csv(v: Any) -> Any:
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    return v


def _tri_ssl(v: Any) -> Any:
    """ADO_VERIFY_SSL：true/false 之外的字串當 CA bundle 路徑。"""
    if isinstance(v, str):
        low = v.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        return v.strip()
    return v


def _switch(v: Any) -> Any:
    """STATIC_SEMGREP / STATIC_RUFF：auto | on | off（true/false 同義）。"""
    if isinstance(v, str):
        low = v.strip().lower()
        if low in _TRUE:
            return "on"
        if low in _FALSE:
            return "off"
        return low
    return v


Stripped = Annotated[str, BeforeValidator(_strip)]
Lowered = Annotated[str, BeforeValidator(_lower)]
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_csv)]
VerifySsl = Annotated[bool | str, BeforeValidator(_tri_ssl)]
Switch = Annotated[Literal["auto", "on", "off"], BeforeValidator(_switch)]


# ---------- config.toml：第二層來源 ----------

DEFAULT_CONFIG_DIR = "/etc/pr-review-suite"


def config_dir() -> Path:
    return Path(os.getenv("CONFIG_DIR", "") or DEFAULT_CONFIG_DIR)


def config_path() -> Path:
    """CONFIG_FILE 指定整個路徑，否則 CONFIG_DIR/config.toml。"""
    p = os.getenv("CONFIG_FILE", "")
    return Path(p) if p else config_dir() / "config.toml"


_toml_cache: tuple[Path, float, dict] | None = None
_toml_ignored_secrets: list[str] = []      # toml 裡出現的 secret 欄位（被忽略，warnings_for 會提）


def load_toml() -> dict[str, dict[str, Any]]:
    """讀 config.toml（依 mtime 快取）。檔不存在 = {}；壞檔 = raise（fail_fast 會轉成人話）。"""
    global _toml_cache
    p = config_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _toml_cache = None
        return {}
    if _toml_cache and _toml_cache[0] == p and _toml_cache[1] == mtime:
        return _toml_cache[2]
    with p.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: 頂層必須是 table")
    _toml_cache = (p, mtime, data)
    return data


class _TomlSection(PydanticBaseSettingsSource):
    """把 config.toml 裡 [<group>] 那張表當成這個 group 的來源（欄位名為鍵）。"""

    def __init__(self, settings_cls, section: str):
        super().__init__(settings_cls)
        self.section = section

    def get_field_value(self, field: FieldInfo, field_name: str):
        return load_toml().get(self.section, {}).get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        table = load_toml().get(self.section) or {}
        out: dict[str, Any] = {}
        for fname, info in self.settings_cls.model_fields.items():
            if fname not in table:
                continue
            if info.annotation is SecretStr:
                key = f"{self.section}.{fname}"
                if key not in _toml_ignored_secrets:
                    _toml_ignored_secrets.append(key)
                continue
            out[fname] = table[fname]
        return out


class _Group(BaseSettings):
    """每一組設定的基底：env > config.toml 的 [group] 表 > 預設；認 alias；忽略不認識的鍵。"""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True,
                                      frozen=True)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return (init_settings, env_settings,
                _TomlSection(settings_cls, cls.__name__.lower()))

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """環境變數設成空字串 = 沒設（用預設值）。"""
        if isinstance(data, dict):
            return {k: v for k, v in data.items()
                    if not (isinstance(v, str) and v.strip() == "")}
        return data


# ---------- 各組 ----------

class Ado(_Group):
    """Azure DevOps 連線與 diff 抓取（orchestrator/adapters/ado.py 與 harvester 共用）。"""

    pat: SecretStr = Field(
        SecretStr(""), alias="ADO_PAT",
        description="Azure DevOps Personal Access Token。只讀 PR 至少 Code > Read；"
                    "要真發留言需 Code > Read & Write。全套只填這一次。",
        json_schema_extra={"example": "changeme", "required": True})
    verify_ssl: VerifySsl = Field(
        True, alias="ADO_VERIFY_SSL",
        description="true / false，或 CA bundle 路徑（公司自簽 CA 指向系統合併包，"
                    "例如 /etc/ssl/certs/ca-certificates.crt）。")
    diff_max_files: int = Field(
        300, alias="ADO_DIFF_MAX_FILES", ge=1,
        description="一條 PR 最多納入幾個檔案。超過的檔案完全不會被抓下來——"
                    "LLM、semgrep/ruff、確定性 MISSING-TEST 都看不到。")
    diff_max_file_bytes: int = Field(
        600000, alias="ADO_DIFF_MAX_FILE_BYTES", ge=1,
        description="單檔內容上限（bytes），任一側超過就整檔略過並留註記。")
    diff_fetch_concurrency: int = Field(
        8, alias="ADO_DIFF_FETCH_CONCURRENCY", ge=1,
        description="抓檔併發度。每檔要抓 base/head 兩份；先確認 proxy/ADO 沒有連線數限制。")


class Posting(_Group):
    """對 Azure DevOps 的寫入開關（出廠全安全側；與 AUTO_POST 疊成雙保險）。"""

    dry_run: bool = Field(
        True, alias="DRY_RUN",
        description="review 稿是否只預覽不真發。內測期間保持 true（安全氣囊）；"
                    "要真發需同時 AUTO_POST=true。")
    knowledge_post: bool = Field(
        False, alias="KNOWLEDGE_POST",
        description="知識蒸餾稿是否真貼到 PR。跟 DRY_RUN 刻意分家，可先行試點。")


class Jobs(_Group):
    """行程內排程（原 repo-sync / knowledge-harvest 兩組 systemd timer）。0 = 關；對應 root 未設也不排。"""

    repo_sync_interval_min: int = Field(
        10, alias="REPO_SYNC_INTERVAL_MIN", ge=0,
        description="每隔幾分鐘把 REPOS_ROOT 底下的 clone git pull --ff-only 一次。")
    harvest_interval_min: int = Field(
        30, alias="HARVEST_INTERVAL_MIN", ge=0,
        description="每隔幾分鐘跑一次 knowledge harvest（PR 終態裁決 + 落地 + commit）。")
    graph_rebuild: Switch = Field(
        "auto", alias="REPO_SYNC_GRAPH_REBUILD",
        description="repo-sync 後是否跑 code-review-graph build。auto = 裝了就跑。")


class Llm(_Group):
    """OpenAI 相容的 LLM gateway。"""

    base_url: Stripped = Field(
        "http://localhost:8000", alias="LLM_BASE_URL",
        description="填到「/chat/completions 的上一層」（帶不帶 /v1 以實測為準）。",
        json_schema_extra={"example": "http://llm-gateway.internal", "required": True})
    api_key: SecretStr = Field(
        SecretStr("not-needed"), alias="LLM_API_KEY",
        description="gateway 不驗 key 就留 not-needed。")
    model: Stripped = Field(
        "gpt-4o-mini", alias="LLM_MODEL",
        description="planner / reviewer / distiller 用的模型。",
        json_schema_extra={"example": "changeme", "required": True})
    model_adversary: str | None = Field(
        None, alias="LLM_MODEL_ADVERSARY",
        description="adversary 專用模型（換一顆腦去自我同意偏差）；未設 = LLM_MODEL。")
    model_digest: str | None = Field(
        None, alias="LLM_MODEL_DIGEST",
        description="收口 digest 顧問分析專用模型；未設 = LLM_MODEL。")
    timeout_s: float = Field(
        180.0, alias="LLM_TIMEOUT_S", gt=0,
        description="單次 LLM 呼叫逾時秒數；逾時重試一次後把例外交還呼叫端走降級。")


class Langfuse(_Group):
    """Langfuse（trace / prompt 版控 / eval）。SDK 自己讀這三個變數；這裡只為了 --show 與 doctor 看得見。"""

    host: Stripped = Field("http://localhost:3000", alias="LANGFUSE_HOST",
                           description="Langfuse 位址。",
                           json_schema_extra={"required": True})
    public_key: Stripped = Field("", alias="LANGFUSE_PUBLIC_KEY",
                                 description="pk-lf-…",
                                 json_schema_extra={"example": "pk-lf-changeme", "required": True})
    secret_key: SecretStr = Field(SecretStr(""), alias="LANGFUSE_SECRET_KEY",
                                  description="sk-lf-…",
                                  json_schema_extra={"example": "sk-lf-changeme", "required": True})


class Orchestrator(_Group):
    """review-orchestrator（8080）的服務層旋鈕。"""

    pipeline: Annotated[Literal["v1", "v2"], BeforeValidator(_lower)] = Field(
        "v1", alias="REVIEW_PIPELINE",
        description="v1 = 單次 LLM 呼叫（回滾用）；v2 = anatomy→static→plan→平行 reviewer"
                    "→adversary→synthesis。切 v2 前先跑過 eval 基線。")
    auto_post: bool = Field(
        False, alias="AUTO_POST",
        description="review 完是否呼叫 ③ 回貼。需同時 DRY_RUN=false 才會真發（雙保險）。")
    dedupe_ttl: float = Field(
        120.0, alias="REVIEW_DEDUPE_TTL", ge=0,
        description="同一條 PR 在幾秒內重複觸發時，重播快取而不重跑。")
    api_key: SecretStr = Field(
        SecretStr(""), alias="ORCH_API_KEY",
        description="設了就要求 Bearer；/eval-webhook 刻意不驗。用到 K 段重蒸就必須設"
                    "（/knowledge/redistill 有 LLM 花費與寫檔語意）。harvester 讀同一個值。")
    url: Stripped = Field(
        "http://127.0.0.1:8080", alias="ORCH_URL",
        description="harvester 打重蒸請求的位址（同機 loopback；不走 proxy）。")
    data_dir: Path = Field(
        Path("/var/lib/pr-review-suite"), alias="DATA_DIR",
        description="review 歷史（reviews.db，SQLite）住這裡；服務帳號要能寫。開不出來 = 歷史停用、review 照跑。")

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


class Review(_Group):
    """v2 pipeline 旋鈕。全部進 trace metadata；改任何一顆先過 eval。"""

    max_dimensions: int = Field(
        8, alias="REVIEW_MAX_DIMENSIONS", ge=2,
        description="planner 最多開幾科（Coverage Sweep 可額外 +1）。開多科比把單科預算拉大好。")
    max_parallel: int = Field(
        6, alias="REVIEW_MAX_PARALLEL", ge=1,
        description="reviewer / adversary 的 LLM 併發上限。")
    llm_base_conf: float = Field(
        0.7, alias="REVIEW_LLM_BASE_CONF", ge=0, le=1,
        description="LLM finding 的基礎信心；adversary confirmed ×1.25 / rejected ×0.35。")
    min_confidence: float = Field(
        0.55, alias="REVIEW_MIN_CONFIDENCE", ge=0, le=1,
        description="低於此分的 finding 在 synthesis 被過濾。")
    max_findings: int = Field(
        30, alias="REVIEW_MAX_FINDINGS", ge=1,
        description="報告輸出上限（純輸出，不影響任何 prompt 長度）。")
    adversary: bool = Field(
        True, alias="REVIEW_ADVERSARY",
        description="關掉 = 跳過對抗段（A/B 用）。")
    adversary_chunk: int = Field(
        6, alias="ADVERSARY_CHUNK", ge=1,
        description="adversary 每次呼叫最多審幾條（分塊防注意力稀釋）。")
    reviewer_rules_max_chars: int = Field(
        20000, alias="REVIEWER_RULES_MAX_CHARS", ge=0,
        description="每個 reviewer 收到的規則文字上限。")
    reviewer_slices_max_chars: int = Field(
        30000, alias="REVIEWER_SLICES_MAX_CHARS", ge=0,
        description="每一科的 diff 切片總預算；超預算的檔降級 static-only 並進 slices_skipped。"
                    "⚠ 改這個數會改變 golden set 的語意。")
    reviewer_file_slice_max_chars: int = Field(
        6000, alias="REVIEWER_FILE_SLICE_MAX_CHARS", ge=0,
        description="單一檔案的切片上限（超過就檔內截斷）。")


class Static(_Group):
    """確定性靜態掃描（semgrep / ruff / MISSING-TEST）。"""

    semgrep: Switch = Field(
        "auto", alias="STATIC_SEMGREP",
        description="auto = 裝了就跑；on = 要跑（沒裝仍只能跳過並註記）；off = 不跑。"
                    "注意 systemd 的 PATH 要含 venv/bin 才找得到。")
    ruff: Switch = Field("auto", alias="STATIC_RUFF", description="同 STATIC_SEMGREP。")
    timeout: int = Field(
        300, alias="STATIC_TIMEOUT", ge=1,
        description="掃描逾時秒數。journal 出現 timed out 就往上調。")
    missing_test: bool = Field(
        True, alias="STATIC_MISSING_TEST",
        description="確定性 MISSING-TEST（diff 有新宣告卻零測試檔 → conf 1.0 保底）。")

    @property
    def semgrep_enabled(self) -> bool:
        return self.semgrep != "off" and shutil.which("semgrep") is not None

    @property
    def ruff_enabled(self) -> bool:
        return self.ruff != "off" and shutil.which("ruff") is not None


class Repos(_Group):
    """本地乾淨 clone（base 上下文）。REPOS_ROOT/<repo小寫>/ 一 repo 一顆，jobs 的 repo-sync 保鮮。"""

    repos_root: Path | None = Field(
        None, alias="REPOS_ROOT",
        description="REPOS_ROOT/<repo小寫>/ 一 repo 一顆 clone（target branch），"
                    "jobs 的 repo-sync 保鮮。未設 = 沒有 base 上下文，review 照跑。",
        json_schema_extra={"example": "/opt/repos"})
    context_lines: int = Field(
        6, alias="CONTEXT_LINES", ge=0,
        description="每個 hunk 從 base 取舊行號附近 ±N 行。")
    context_max_chars: int = Field(
        24000, alias="CONTEXT_MAX_CHARS", ge=0,
        description="base 上下文與 graph 摘要的預算。會進「每一科」的 reviewer prompt。")

    def root_for(self, repo: str | None) -> Path | None:
        """REPOS_ROOT/<repo小寫>/，存在才回；否則 None（呼叫端優雅降級）。"""
        if not self.repos_root or not repo:
            return None
        p = (self.repos_root / repo.strip().lower()).resolve()
        return p if p.is_dir() else None


class Graph(_Group):
    """④ code graph（選配）。GRAPH_TOOLS_URL 與 GRAPH_TOOL_BLAST 都設了才啟用。"""

    tools_url: str | None = Field(
        None, alias="GRAPH_TOOLS_URL",
        description="code-review-graph serve --http 的 MCP 位址。",
        json_schema_extra={"example": "http://127.0.0.1:8097/mcp"})
    tool_blast: str | None = Field(
        None, alias="GRAPH_TOOL_BLAST",
        description="影響半徑工具名（裝好後 list tools 確認）。",
        json_schema_extra={"example": "get_impact_radius_tool"})
    tool_blast_args: str = Field(
        '{"files": $files}', alias="GRAPH_TOOL_BLAST_ARGS",
        description="參數 JSON 樣板；$files / $repo / $repo_dir 會被代入。含 $ 與空白，"
                    "被 shell source 時要用單引號。多 repo 自動切庫："
                    '{"changed_files": $files, "repo_root": "<REPOS_ROOT>/$repo_dir"}')
    repo: str | None = Field(
        None, alias="GRAPH_REPO",
        description="graph server 綁定的 repo 名（小寫）；設了之後其他 repo 自動跳過 graph。"
                    "多 repo 切庫模式留空。")

    @property
    def enabled(self) -> bool:
        return bool(self.tools_url and self.tool_blast)

    @property
    def bound_repo(self) -> str:
        return (self.repo or "").strip().lower()


class Rules(_Group):
    """團隊規則 repo（本機 clone）。RULES_ROOT 未設 = 規則功能整個靜默停用。"""

    root: Path | None = Field(
        None, alias="RULES_ROOT",
        description="規則 repo 的本機 clone；每次 review 用到的 commit SHA 會進 trace。",
        json_schema_extra={"example": "/opt/pr-review-rules"})
    subdir: Stripped = Field("rules", alias="RULES_SUBDIR",
                             description="規則目錄名（<RULES_ROOT>/rules/）。")
    common_dir: Stripped = Field("common", alias="RULES_COMMON_DIR",
                                 description="每次 review 必載的子目錄名。")
    max_chars: int = Field(60000, alias="RULES_MAX_CHARS", ge=0,
                           description="組進 prompt 的規則總量上限（字元）。")

    @property
    def enabled(self) -> bool:
        return self.root is not None


class Knowledge(_Group):
    """知識迴圈（K 段，選配）。KNOWLEDGE_ROOT 未設 = 注入與蒸餾皆靜默停用。"""

    root: Path | None = Field(
        None, alias="KNOWLEDGE_ROOT",
        description="本地 knowledge repo 路徑（行程內的 harvest job 是唯一 commit 者）。",
        json_schema_extra={"example": "/opt/pr-review-knowledge"})
    branches: CsvList = Field(
        ["master", "main"], alias="KNOWLEDGE_BRANCHES",
        description="進帳本的目標分支（逗號分隔、支援萬用字元，例：master,release/*）。")
    max_chars: int = Field(
        18000, alias="KNOWLEDGE_MAX_CHARS", ge=0,
        description="Stage 04 注入區塊總上限；同樣會進 reviewer prompt。")
    distill_diff_max_chars: int = Field(
        100000, alias="DISTILL_DIFF_MAX_CHARS", ge=0,
        description="蒸餾看得到的 diff 切片預算；超預算的檔會留註記並進 distill_slices_skipped。")
    redistill: bool = Field(
        True, alias="KNOWLEDGE_REDISTILL",
        description="stale 重蒸（draft 之後又有 push → 用最終 diff 重蒸一次，≤2 次）。"
                    "false = 回到 stale 直接丟稿。")
    cycle_source: Stripped = Field(
        "release/*", alias="KNOWLEDGE_CYCLE_SOURCE",
        description="週期驗收 PR 的 source 分支樣式；符合者不蒸餾、作為收口儀式。")
    cycle_target: Stripped = Field(
        "master", alias="KNOWLEDGE_CYCLE_TARGET",
        description="週期驗收 PR 的 target 分支。")
    digest: bool = Field(
        True, alias="KNOWLEDGE_DIGEST",
        description="收口 digest 要不要貼到驗收 PR（false = 全暗影子模式：照樣落地、不發留言）。")
    digest_llm: bool = Field(
        True, alias="KNOWLEDGE_DIGEST_LLM",
        description="digest 的「Needs a look」顧問段（LLM 提案，落地路徑不讀）。false = 純清單。")
    digest_max_flags: int = Field(
        12, alias="KNOWLEDGE_DIGEST_MAX_FLAGS", ge=1,
        description="顧問旗標上限（防洗版）。")
    retire_on_contradiction: bool = Field(
        True, alias="KNOWLEDGE_RETIRE_ON_CONTRADICTION",
        description="實作推翻規則時收口是否拔掉那條不變式（沉默即拔；回 keep <ID> 否決）。"
                    "false = 只提案不執行。")

    @property
    def enabled(self) -> bool:
        return self.root is not None and self.root.is_dir()


class Eval(_Group):
    """離線考試（run_eval / run_distill_eval）。門檻未設 = report-only。"""

    knowledge_root: Path | None = Field(
        None, alias="EVAL_KNOWLEDGE_ROOT",
        description="知識依賴題的種子帳本；未設 = eval/fixtures/knowledge。")
    knowledge_ab: bool = Field(
        True, alias="EVAL_KNOWLEDGE_AB",
        description="對照組（同一批知識題再跑一次 KNOWLEDGE_ROOT=\"\"）；關掉就沒有 delta。")
    min_recall: float = Field(0.0, alias="EVAL_MIN_RECALL", ge=0, le=1,
                              description="CI gate：must_catch_recall 下限。")
    max_noise: float | None = Field(None, alias="EVAL_MAX_NOISE", ge=0,
                                    description="CI gate：noise rate 上限。")
    max_forbidden: float | None = Field(None, alias="EVAL_MAX_FORBIDDEN", ge=0,
                                        description="CI gate：forbidden-hit rate 上限。")
    min_knowledge_delta: float | None = Field(
        None, alias="EVAL_MIN_KNOWLEDGE_DELTA",
        description="knowledge_delta 的門檻。先 report-only，基線穩定兩輪再設。")
    min_distill_score: float | None = Field(
        None, alias="EVAL_MIN_DISTILL_SCORE", ge=0, le=1,
        description="蒸餾考卷的門檻，同樣先 report-only。")


# ---------- 總表 ----------

class Settings(BaseModel):
    model_config = {"frozen": True}

    ado: Ado = Field(default_factory=Ado)
    posting: Posting = Field(default_factory=Posting)
    jobs: Jobs = Field(default_factory=Jobs)
    llm: Llm = Field(default_factory=Llm)
    langfuse: Langfuse = Field(default_factory=Langfuse)
    orchestrator: Orchestrator = Field(default_factory=Orchestrator)
    review: Review = Field(default_factory=Review)
    static: Static = Field(default_factory=Static)
    repos: Repos = Field(default_factory=Repos)
    graph: Graph = Field(default_factory=Graph)
    rules: Rules = Field(default_factory=Rules)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    eval: Eval = Field(default_factory=Eval)

    def knobs(self) -> dict[str, Any]:
        """進 trace metadata 的旋鈕快照（影響 review 結果的那幾組；無 secrets）。"""
        out: dict[str, Any] = {}
        for grp in (self.orchestrator, self.posting, self.review, self.static,
                    self.repos, self.graph, self.rules, self.knowledge):
            for name, info in type(grp).model_fields.items():
                v = getattr(grp, name)
                if isinstance(v, SecretStr):
                    continue
                out[env_names(info)[0]] = str(v) if isinstance(v, Path) else v
        return out


GROUP_ORDER = ("ado", "posting", "llm", "langfuse", "orchestrator", "jobs", "review",
               "static", "repos", "graph", "rules", "knowledge", "eval")

# S1 之前有意義、現在整組退場的名字：還留在 env 檔裡就提醒，別讓人以為它們還管事。
RETIRED_ENV = ("HOST", "PORT", "ADO_TOOLS_PORT", "READER_TOOLS_PORT", "RESULT_TOOLS_PORT",
               "ADO_TOOLS_URL", "READER_TOOLS_URL", "RESULT_TOOLS_URL", "REPO_ROOT")

_cached: Settings | None = None


def settings() -> Settings:
    """惰性單例。環境變數變了要先 reset()（tests/conftest.py 已自動綁上）。"""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


_reload_hooks: list[Callable[[], None]] = []


def on_reload(fn: Callable[[], None]) -> None:
    """設定重載時要跟著清的東西（例如 llm 的長駐 client）在這裡登記。"""
    if fn not in _reload_hooks:
        _reload_hooks.append(fn)


def reset() -> None:
    global _cached, _toml_cache
    _cached = None
    _toml_cache = None
    _toml_ignored_secrets.clear()
    for fn in _reload_hooks:
        try:
            fn()
        except Exception:  # noqa: BLE001  —— 清快取失敗不該讓設定重載失敗
            pass


def fail_fast() -> Settings:
    """服務啟動時呼叫：壞值立刻以人話報錯並退出，而不是在第一條 review 中段才炸。"""
    try:
        reset()
        return settings()
    except ValidationError as e:
        print("設定值有誤，服務不啟動：", file=sys.stderr)
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            print(f"  {loc}: {err['msg']}（收到 {err.get('input')!r}）", file=sys.stderr)
        raise SystemExit(1)
    except (tomllib.TOMLDecodeError, ValueError) as e:
        print(f"config.toml 解析不過（{config_path()}）：{e}", file=sys.stderr)
        raise SystemExit(1)


# ---------- schema 反射：--show / --example / doctor 共用 ----------

def env_names(info: FieldInfo) -> list[str]:
    """一個欄位認得的環境變數名（第一個是正名）。"""
    va = info.validation_alias
    if isinstance(va, AliasChoices):
        return [c if isinstance(c, str) else ".".join(map(str, c.path))
                for c in va.choices]
    if isinstance(va, str):
        return [va]
    return [info.alias] if info.alias else []


def iter_fields() -> Iterator[tuple[str, type[_Group], str, FieldInfo]]:
    """(group 屬性名, group 類, 欄位名, FieldInfo)，依 GROUP_ORDER。"""
    for gname in GROUP_ORDER:
        gcls = Settings.model_fields[gname].annotation
        for fname, info in gcls.model_fields.items():
            yield gname, gcls, fname, info


def _render_value(v: Any) -> str:
    if isinstance(v, SecretStr):
        return v.get_secret_value()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ",".join(v)
    if v is None:
        return ""
    if isinstance(v, Path):
        return v.as_posix()         # Windows dev machine renders \, Linux is the target
    return str(v)


def _mask(v: Any) -> str:
    if isinstance(v, SecretStr):
        raw = v.get_secret_value()
        return "(unset)" if not raw else f"***{raw[-4:]}" if len(raw) > 8 else "***"
    return _render_value(v) or "(unset)"


def example_text() -> str:
    """由 schema 產出 suite.env.example（四個行程共用一份）。"""
    lines = [
        "# pr-review-suite 全套設定 —— 由 `python -m shared.settings --example` 產生，",
        "# 不要手改這個 .example；改 shared/settings.py 的 Field 再重生。",
        "# S2 起建議：secrets 放 /etc/pr-review-suite/secrets.env（systemd EnvironmentFile），",
        "# 其他旋鈕放 config.toml（控制台 Settings 頁寫；--migrate 可從既有 env 一次轉過去）。",
        "# 全部放 env 也仍然成立（env 永遠優先）——這份就是全 env 版。",
        "#",
        "# systemd 的 EnvironmentFile 不支援行內註解：註解一律獨立成行，",
        "# 等號右邊不可有空白或 # 尾巴（血淚：URL 會出現 %20）。含 $ 或空白的值用單引號。",
        "# 註解掉的行代表「未設 = 預設值 / 停用」；要用再打開。",
        "",
    ]
    for gname in GROUP_ORDER:
        gcls = Settings.model_fields[gname].annotation
        lines.append(f"# {'=' * 72}")
        lines.append(f"# [{gname}] {(gcls.__doc__ or '').strip()}")
        lines.append(f"# {'=' * 72}")
        for fname, info in gcls.model_fields.items():
            names = env_names(info)
            extra = info.json_schema_extra or {}
            example = extra.get("example") if isinstance(extra, dict) else None
            default = info.get_default(call_default_factory=True)
            lines.append("")
            for d in (info.description or "").split("。"):
                d = d.strip()
                if d:
                    lines.append(f"# {d}。" if not d.endswith(("）", ")", "\"")) else f"# {d}")
            if len(names) > 1:
                lines.append(f"# （舊名 {', '.join(names[1:])} 仍認）")
            val = example if example is not None else _render_value(default)
            needs_quote = any(ch in val for ch in " $") if val else False
            shown = f"'{val}'" if needs_quote else val
            required = isinstance(extra, dict) and extra.get("required")
            if required:
                # 一定要填的（PAT、gateway、模型）：打開、放佔位值
                lines.append(f"{names[0]}={shown}")
            elif val == "" or example is not None or "已淘汰" in (info.description or ""):
                # 未設=停用的選配、範例值不是真預設、已淘汰：留成註解，使用者自己打開
                lines.append(f"# {names[0]}={shown}")
            else:
                lines.append(f"{names[0]}={shown}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def source_of(gname: str, fname: str, info: FieldInfo | None = None) -> str:
    """'env:<NAME>' | 'file' | 'default' —— 這顆值現在是誰給的。"""
    info = info or Settings.model_fields[gname].annotation.model_fields[fname]
    src = next((n for n in env_names(info) if os.getenv(n, "") != ""), None)
    if src:
        return f"env:{src}"
    if fname in (load_toml().get(gname) or {}) and info.annotation is not SecretStr:
        return "file"
    return "default"


def show_rows(s: Settings | None = None) -> list[tuple[str, str, str, str]]:
    """(group, ENV, 生效值(遮罩), 來源) —— 給 --show 與 doctor。"""
    s = s or settings()
    rows = []
    for gname, _gcls, fname, info in iter_fields():
        rows.append((gname, env_names(info)[0], _mask(getattr(getattr(s, gname), fname)),
                     source_of(gname, fname, info)))
    return rows


# ---------- config.toml：寫入（控制台 Settings 頁、--migrate） ----------

def _toml_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_literal(x) for x in v) + "]"
    if isinstance(v, Path):
        v = v.as_posix()
    return json.dumps(str(v), ensure_ascii=False)      # TOML 基本字串 = JSON 字串語法


def _native(v: Any) -> Any:
    """把 settings 物件上的值變成能寫進 toml 的型別。"""
    if isinstance(v, Path):
        return v.as_posix()
    if isinstance(v, SecretStr):
        return v.get_secret_value()
    return v


def render_toml(values: dict[str, dict[str, Any]]) -> str:
    """由 schema 渲染整份 config.toml：每組一張表、每欄附說明；沒給值的欄位留成註解。
    secrets 欄位永遠只出現在註解裡（它們住 secrets.env）。"""
    lines = [
        "# pr-review-suite 設定（env 會覆寫這裡；secrets 不放這裡，放 secrets.env）",
        "# 由控制台 Settings 頁或 `python -m shared.settings --migrate` 寫入；",
        "# 手改也行，服務重載時會重讀。每個鍵的說明見下方註解。",
        "",
    ]
    for gname in GROUP_ORDER:
        gcls = Settings.model_fields[gname].annotation
        table = values.get(gname) or {}
        lines.append(f"[{gname}]")
        lines.append(f"# {(gcls.__doc__ or '').strip()}")
        for fname, info in gcls.model_fields.items():
            desc = " ".join(d.strip() for d in (info.description or "").split("。") if d.strip())
            env = env_names(info)[0]
            lines.append(f"# {fname}  (env {env})：{desc}")
            if info.annotation is SecretStr:
                lines.append(f"# {fname} = <secret：只能放 secrets.env 的 {env}>")
            elif fname in table and table[fname] is not None:
                lines.append(f"{fname} = {_toml_literal(table[fname])}")
            else:
                default = info.get_default(call_default_factory=True)
                lines.append(f"# {fname} = {_toml_literal(_native(default)) if default is not None else '<unset>'}")
        lines.append("")
    return "\n".join(lines)


def changes_log_path() -> Path:
    return config_path().with_name("config-changes.jsonl")


def validate_group_values(gname: str, values: dict[str, Any]) -> dict[str, Any]:
    """用 schema 驗一組「欄位名 → 值」（不合併 env；控制台表單專用）。回正規化後的值；壞值 raise ValidationError。"""
    gcls = Settings.model_fields[gname].annotation
    obj = gcls.model_validate(values)           # model_validate 不跑 env 來源，純驗值
    return {k: _native(getattr(obj, k)) for k in values}


def save_toml(changes: dict[str, dict[str, Any]], actor: str = "console") -> list[dict]:
    """把 changes（group → field → 值；值為 None = 刪掉這個鍵、回預設）寫進 config.toml。

    順序：驗值 → 讀現檔 → 合併 → 備份 config.toml.bak-<ts> → 原子寫入 → 寫 change log
    → reset()（下一次 settings() 就是新值）。secrets 欄位一律拒絕。
    回傳每筆變動 {group, field, old, new}。
    """
    current = {g: dict(t) for g, t in load_toml().items() if isinstance(t, dict)}
    before = settings()
    log: list[dict] = []
    for gname, fields in changes.items():
        gcls = Settings.model_fields[gname].annotation
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            clean = validate_group_values(gname, clean)
        for fname, v in fields.items():
            info = gcls.model_fields[fname]
            if info.annotation is SecretStr:
                raise ValueError(f"{gname}.{fname} 是 secret，只能放 secrets.env（{env_names(info)[0]}）")
            old = _native(getattr(getattr(before, gname), fname))
            table = current.setdefault(gname, {})
            if v is None:
                table.pop(fname, None)
                new = _native(info.get_default(call_default_factory=True))
            else:
                table[fname] = clean[fname]
                new = clean[fname]
            if old != new:
                log.append({"group": gname, "field": fname, "env": env_names(info)[0],
                            "old": old, "new": new})
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = render_toml(current)
    tomllib.loads(text)                          # 寫出去之前先確定自己讀得回來
    if p.exists():
        shutil.copy2(p, p.with_name(f"{p.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = p.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    if log:
        with changes_log_path().open("a", encoding="utf-8") as f:
            for entry in log:
                f.write(json.dumps({"ts": time.time(), "actor": actor, **entry},
                                   ensure_ascii=False, default=str) + "\n")
    reset()
    return log


def recent_changes(limit: int = 20) -> list[dict]:
    p = changes_log_path()
    if not p.exists():
        return []
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows[-limit:][::-1]


def migrate_from_env() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """把目前 env 裡「非預設、非 secret」的值整理成 toml values；secrets 另列成 env 行。"""
    s = settings()
    values: dict[str, dict[str, Any]] = {}
    secret_lines: list[str] = []
    for gname, gcls, fname, info in iter_fields():
        names = env_names(info)
        raw = next((os.getenv(n) for n in names if os.getenv(n, "") != ""), None)
        if raw is None:
            continue
        if info.annotation is SecretStr:
            secret_lines.append(f"{names[0]}={raw}")
            continue
        v = _native(getattr(getattr(s, gname), fname))
        default = _native(info.get_default(call_default_factory=True))
        if v != default:
            values.setdefault(gname, {})[fname] = v
    return values, secret_lines


def warnings_for(s: Settings | None = None) -> list[str]:
    """設定層就看得出來的問題（不打網路；那是 doctor 的事）。"""
    s = s or settings()
    out: list[str] = []
    if _toml_ignored_secrets:
        out.append("config.toml 裡放了 secret 欄位，已忽略（secrets 只能走 secrets.env）："
                   + ", ".join(_toml_ignored_secrets))
    stale = [n for n in RETIRED_ENV if os.getenv(n, "")]
    if stale:
        out.append("這些變數已無作用（①②③ 已併入 orchestrator 行程、② 已移除），"
                   f"從 env 檔刪掉：{', '.join(stale)}")
    if s.orchestrator.auto_post and s.posting.dry_run:
        out.append("AUTO_POST=true 但 DRY_RUN=true：review 會「假發」——只回預覽。"
                   "（這是出廠安全側，確認要真發再翻 DRY_RUN）")
    if s.jobs.harvest_interval_min and s.knowledge.root is None:
        pass                      # harvest 只在 KNOWLEDGE_ROOT 有設時排，不算問題
    if s.knowledge.root is not None and not s.knowledge.root.is_dir():
        out.append(f"KNOWLEDGE_ROOT 指向不存在的目錄：{s.knowledge.root}（K 段視同停用）")
    if s.rules.root is not None and not s.rules.root.is_dir():
        out.append(f"RULES_ROOT 指向不存在的目錄：{s.rules.root}（規則視同停用）")
    if s.repos.repos_root is not None and not s.repos.repos_root.is_dir():
        out.append(f"REPOS_ROOT 指向不存在的目錄：{s.repos.repos_root}（沒有 base 上下文）")
    if s.graph.tools_url and not s.graph.tool_blast:
        out.append("GRAPH_TOOLS_URL 有設但 GRAPH_TOOL_BLAST 沒設：graph 不會啟用。")
    if s.knowledge.redistill and s.knowledge.root and not s.orchestrator.api_key.get_secret_value():
        out.append("KNOWLEDGE_REDISTILL=true 但 ORCH_API_KEY 未設：/knowledge/redistill 無認證。")
    return out


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m shared.settings")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true", help="目前生效值與來源（secrets 遮罩）")
    g.add_argument("--example", action="store_true", help="由 schema 產生 suite.env.example")
    g.add_argument("--example-toml", action="store_true", help="由 schema 產生 config.toml 樣板")
    g.add_argument("--migrate", action="store_true",
                   help="目前 env 的非預設值寫成 CONFIG_DIR/config.toml；secrets 印出來讓你放 secrets.env")
    g.add_argument("--check", action="store_true", help="只驗證；壞值回傳碼 1")
    a = ap.parse_args(argv)

    if a.example:
        sys.stdout.write(example_text())
        return 0
    if a.example_toml:
        sys.stdout.write(render_toml({}))
        return 0
    if a.migrate:
        values, secret_lines = migrate_from_env()
        log = save_toml(values, actor="migrate")
        print(f"已寫入 {config_path()}（{sum(len(t) for t in values.values())} 個非預設值，"
              f"{len(log)} 個與生效值不同）")
        if secret_lines:
            print("\n這幾行是 secret，不進 toml；放到 secrets.env（chmod 600）並由 systemd EnvironmentFile 載入：")
            for ln in secret_lines:
                k, _v = ln.split("=", 1)
                print(f"  {k}=…")
        return 0
    try:
        s = Settings()
    except ValidationError as e:
        print("設定值有誤：")
        for err in e.errors():
            print(f"  {'.'.join(map(str, err['loc']))}: {err['msg']}（收到 {err.get('input')!r}）")
        return 1
    if a.check:
        print("設定 OK")
    else:
        w = max(len(r[1]) for r in show_rows(s))
        cur = None
        for grp, name, val, src in show_rows(s):
            if grp != cur:
                print(f"\n[{grp}]")
                cur = grp
            print(f"  {name:<{w}}  {val:<40}  {src}")
    warns = warnings_for(s)
    if warns:
        print("\n注意：")
        for ln in warns:
            print(f"  - {ln}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
