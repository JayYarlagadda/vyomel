# 05 — Tool Specification and Catalog

Status: **Approved baseline (v1.0)**

---

## 1. The tool contract

Every tool is a class implementing `astra.tools.base.Tool`. The contract is deliberately strict: the planner, policy engine, runtime, and verifier all consume this metadata, so an incomplete tool declaration is a correctness bug, not a style issue.

```python
class Tool(Protocol):
    name: str  # dotted, stable: "fs.read_file"
    version: str  # semver; recorded on every action
    description: str  # what the model sees — behavioral, not marketing
    Input: type[BaseModel]  # JSON-Schema source for structured tool calling
    Output: type[BaseModel]
    base_capability: Capability  # L0..L4 before escalation rules
    reversible: bool
    idempotent: bool
    actuation_tier: int  # 1=native API, 2=a11y, 3=DOM, 4=vision
    concurrency_key: str | None  # e.g. "desktop" -> serialized across the process
    default_timeout_s: int

    def classify(self, params: Input) -> Capability: ...
    async def preflight(self, params: Input, ctx: ToolContext) -> PreflightResult: ...
    async def execute(self, params: Input, ctx: ToolContext) -> Output: ...
    async def compensate(self, params: Input, result: Output, ctx: ToolContext) -> None: ...
    def verification_plan(self, params: Input, result: Output) -> list[Postcondition]: ...
```

### Rules

| Rule | Reason |
|---|---|
| `classify()` may **raise** the level based on parameters, never lower it. | Prevents parameter-driven privilege laundering. |
| Errors are raised as `ToolError(code, message, retryable, observation)`. | The runtime's retry ladder is driven by structured codes (`07` §6), never string matching. |
| `execute()` must not mutate task state or write audit records. | Layering rule; the runtime owns state. |
| `compensate()` is mandatory when `reversible=True`. | Cancellation must actually be able to undo. |
| `verification_plan()` must return at least one postcondition for `base_capability >= L2`. | Enforces FR-401. Returning `[]` fails a registry test. |
| `description` is written for the *model*: preconditions, side effects, failure modes. | Tool-choice accuracy is dominated by description quality — this is measured in `evals/suites/agent/`. |
| Every tool ships a fixture-backed test and an entry in the tool-call accuracy eval. | Otherwise the catalog rots silently. |

`tests/tools/test_contract.py` iterates the entire registry and asserts every rule above. A tool that does not comply cannot be registered.

---

## 2. Tool context

```python
@dataclass
class ToolContext:
    task_id: str
    action_id: str
    capability_granted: Capability  # ceiling the policy engine authorized
    scratch_dir: Path  # sandboxed working area
    allowed_roots: list[Path]  # fs sandbox
    allowed_domains: list[str]  # egress allowlist
    trace: Span
    models: ModelRouter  # for vision/LLM-assisted tools
    perception: PerceptionService
    deadline: datetime
    cancel: CancellationToken
```

A tool receives capability as a **granted ceiling**, so it can self-check (`assert ctx.capability_granted >= self.classify(params)`) — defense in depth against a runtime bug that skipped the gate.

---

## 3. Catalog

Legend: **Cap** = base capability, **Tier** = actuation tier, **Rev** = reversible, **Idem** = idempotent, **M** = milestone introduced.

### 3.1 Information (`L0`)

| Tool | Cap | Tier | Idem | M | Notes |
|---|---|---|---|---|---|
| `fs.read_file` | L0 | 1 | ✓ | M2 | allowlisted roots only |
| `fs.list_dir` | L0 | 1 | ✓ | M2 | |
| `fs.search` | L0 | 1 | ✓ | M2 | glob + content grep |
| `fs.stat` | L0 | 1 | ✓ | M2 | |
| `memory.query` | L0 | 1 | ✓ | M4 | hybrid retrieval, returns citations |
| `memory.get_entity` | L0 | 1 | ✓ | M4 | context-graph lookup |
| `web.search` | L0 | 1 | ✗ | M5 | provider-backed |
| `web.fetch` | L0 | 1 | ✓ | M5 | egress allowlist; result marked `tool_untrusted` |
| `screen.capture` | L0 | 4 | ✓ | M7 | redacts credential regions before any egress |
| `screen.read_active_window` | L0 | 2 | ✓ | M7 | UIA tree of the focused window |
| `clipboard.read` | L0 | 1 | ✓ | M7 | |
| `shell.run` (read-only allowlist) | L0 | 1 | ✗ | M3 | allowlisted commands only (`git status`, `ls`, …) |

### 3.2 Local mutation (`L1`–`L2`)

| Tool | Cap | Tier | Rev | M | Notes |
|---|---|---|---|---|---|
| `fs.write_file` | L1→L2 | 1 | ✓ | M3 | L1 inside scratch, L2 elsewhere; backs up prior content |
| `fs.move` / `fs.copy` | L2 | 1 | ✓ | M3 | |
| `fs.delete` | L2→L4 | 1 | ✓ | M3 | L4 for directory trees; moves to a trash dir, never `unlink` |
| `doc.edit` | L2 | 1 | ✓ | M8 | structured edits with a diff preview |
| `note.create` / `note.update` | L1 | 1 | ✓ | M4 | |
| `clipboard.write` | L1 | 1 | ✓ | M7 | |
| `app.open` / `app.focus` | L1 | 1 | ✓ | M7 | |
| `git.diff` / `git.status` | L0 | 1 | ✓ | M3 | |
| `git.commit` | L2 | 1 | ✓ | M3 | reversible via reset |
| `git.push` | L3 | 1 | ✗ | M3 | externally visible |

### 3.3 Browser (M5)

| Tool | Cap | Tier | Notes |
|---|---|---|---|
| `browser.open` | L1 | 3 | |
| `browser.read_page` | L0 | 2 | accessibility tree first, DOM second |
| `browser.query` | L0 | 3 | selector/role-based element query |
| `browser.click` | L2 | 2/3 | role+name selector; coordinates only as fallback (then L2 minimum) |
| `browser.type` | L2 | 3 | refuses to type into password fields unless explicitly approved |
| `browser.select` / `browser.scroll` | L1 | 3 | |
| `browser.submit` | L3 | 3 | form submission is external communication |
| `browser.download` | L2 | 3 | into scratch dir only |
| `browser.screenshot` | L0 | 4 | |

Browser sessions run in a **dedicated, persistent Playwright profile** separate from the user's daily browser. This bounds blast radius: Astra only has the sessions the user deliberately logged in through it.

### 3.4 Desktop (M7, Windows UIA)

| Tool | Cap | Tier | Notes |
|---|---|---|---|
| `desktop.list_windows` | L0 | 2 | |
| `desktop.read_tree` | L0 | 2 | UIA element tree, depth-bounded |
| `desktop.find_element` | L0 | 2 | by name/role/automation-id |
| `desktop.click_element` | L2 | 2 | UIA invoke pattern preferred over synthetic click |
| `desktop.set_field` | L2 | 2 | UIA value pattern |
| `desktop.type_text` | L2 | 2 | |
| `desktop.key` | L2 | 2 | shortcut chords |
| `desktop.click_xy` | L2 | 4 | **last resort**; always ≥ L2, always logs a screenshot as evidence |
| `desktop.scroll` | L1 | 2 | |

The tier hierarchy is enforced by `DesktopActuator.resolve()`, which attempts tiers 1→4 in order and records `astra_actuation_tier_total{tier}`. A tool cannot skip directly to tier 4.

### 3.5 External APIs (M8)

| Tool | Cap | Notes |
|---|---|---|
| `email.search` / `email.read` | L0 | Gmail API, read-only scope |
| `email.draft` | L1 | creates a draft, sends nothing |
| `email.send` | L3 | writes to `side_effect_ledger`; never auto-approved in practice |
| `calendar.list` / `calendar.find_free` | L0 | |
| `calendar.create_event` | L2 | L3 when it has external attendees |
| `calendar.delete_event` | L2 | |
| `github.search` / `github.read` | L0 | |
| `github.create_issue` / `github.comment` | L3 | |
| `http.get` | L0 | allowlisted domains |
| `http.post` | L3 | allowlisted domains |

OAuth uses least-privilege scopes, tokens in the OS keyring, refresh rotation, and a `astra auth revoke` path.

### 3.6 Media (M14, plugin)

| Tool | Cap | Notes |
|---|---|---|
| `media.probe` | L0 | ffprobe |
| `media.transcribe` | L0 | local Whisper; word-level timestamps |
| `media.detect_segments` | L0 | profanity/filler/silence/highlight detection over the transcript |
| `media.cut` / `media.concat` | L1 | writes to scratch |
| `media.mute_segment` | L1 | |
| `media.caption` | L1 | burn-in or sidecar `.srt` |
| `media.export` | L2 | writes the final artifact |

This is how the "AI video editor" idea lands as **one Astra toolset** rather than a separate product — same runtime, same permissions, same verification.

### 3.7 Meta

| Tool | Cap | Notes |
|---|---|---|
| `task.ask_user` | L0 | request clarification; moves task to `NEEDS_HUMAN` |
| `task.report` | L0 | final structured answer with citations |
| `memory.remember` | L1 | persist a fact/preference into the context graph |
| `memory.forget` | L2 | hard delete (FR-509) |
| `workflow.invoke` | inherits | run a saved workflow; inherits the caller's ceiling |

---

## 4. Structured tool calling

The planner receives tools as JSON-Schema function definitions generated directly from the Pydantic `Input` models — **one source of truth** for validation, documentation, and model-facing schema.

Reliability measures:

1. **Strict schema mode** where the provider supports it (OpenAI `strict: true`, structured outputs).
2. **Validation before dispatch.** A call failing schema validation never becomes an action; the planner is re-prompted with the validation error (max 2 attempts).
3. **Capability-filtered catalog.** Tools above the task's ceiling are not shown to the model at all — the cheapest way to prevent an entire class of bad plans.
4. **Catalog size discipline.** More than ~30 tools in one prompt measurably degrades selection accuracy, so the catalog is pre-filtered by relevance (retrieval over tool descriptions) when it exceeds that.
5. **`tool_call_accuracy`** is a first-class tracked metric in `evals/suites/agent/`, broken down into: correct tool, correct parameters, schema-valid, and unnecessary-call rate.

---

## 5. Adding a tool — checklist

1. Implement `Tool` in `astra/tools/<domain>/<name>.py`.
2. Declare capability, reversibility, idempotency, tier, concurrency key, timeout.
3. Implement `classify()` with parameter-based escalation.
4. Implement `verification_plan()` (mandatory for ≥ L2).
5. Implement `compensate()` if reversible.
6. Register in `astra/tools/registry.py`.
7. Add a policy rule in `config/policy.yaml` if the default level is wrong for it.
8. Write `tests/tools/<domain>/test_<name>.py` with success, failure, timeout, and permission-denied cases.
9. Add at least one case to `evals/suites/agent/cases/`.
10. Document it in this file's catalog table.

CI fails if steps 6, 8, or 10 are missing — `tests/tools/test_catalog_sync.py` cross-checks the registry against this document's tables.
