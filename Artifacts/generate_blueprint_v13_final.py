#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AEGIS / AI AUTONOMOUS SWING TRADER
BLUEPRINT V1.3 — CONSOLIDATED & ARCHITECTURE-FROZEN MASTER

This generator consolidates:
  - V1.1: complete architectural baseline and implementation detail
  - V1.2: corrections, frozen stack, Replay-first validation, measurable gates
  - V1.3 decisions made after the Mimo review and subsequent architecture review

IMPORTANT:
  V1.1 is preserved as historical source material, but explicit V1.3
  supersession rules override obsolete V1.1/V1.2 clauses. This is necessary
  because V1.1/V1.2 explicitly prohibited LiveBroker, while V1.3 now requires
  LIVE to be fully implemented but disabled by default.

V1.3 FINAL DECISIONS
--------------------
1. No academic/thesis scope.
2. 16 phases remain; they are architectural boundaries, not artificial
   granularity.
3. V1.1 is the historical architectural baseline.
4. V1.2 is a delta/revision source.
5. V1.3 is the sole implementation authority.
6. Replay is a first-class validation foundation.
7. Stack is fully frozen.
8. Every phase has implementation + audit + measurable gate.
9. Audit statuses:
      PASS
      PASS_WITH_WARNINGS (only when the phase explicitly permits it)
      FAIL
      ARCHITECTURAL_BLOCKER
10. Sandbox and Live use the same trading pipeline.
11. LIVE broker adapter is IMPLEMENTED in V1.3.
12. LIVE is DISABLED by default.
13. Changing TRADING_ENVIRONMENT=SANDBOX to LIVE must select the Live adapter
    without code changes. Live must still pass all safety gates.
14. LIVE_ENABLED=false must block live execution fail-closed.
15. LIVE_ENABLED=true is necessary but NOT sufficient; live gates must pass.
16. V1 certification target remains PAPER_READY, while LIVE is implemented
    and disabled. V1 is NOT automatically LIVE_CERTIFIED.
17. No direct LLM -> Broker, Frontend -> Broker, or Risk bypass.
18. PostgreSQL is financial source of truth.
19. Decimal is mandatory for critical financial calculations.
20. UTC is mandatory internally.
21. correlation_id and idempotency are mandatory.
22. No look-ahead.
23. Secrets never enter source control, frontend, LLM prompts, or audit payloads.
24. If the Blueprint does not specify an architectural decision, coder MUST
    stop with ARCHITECTURAL_BLOCKER instead of inventing one.

FROZEN STACK
------------
Runtime:        Python 3.12+
API:            FastAPI
Validation:     Pydantic v2
ORM:            SQLAlchemy 2.x
Migrations:     Alembic
Database:       PostgreSQL
Cache/Messaging: Redis only when necessary; never financial source of truth
Data/Quant:     Polars / Pandas
Frontend:       React + TypeScript + Vite
Testing:        pytest
Containers:     Docker + Docker Compose
Observability:  Structured logs + metrics + audit trail
LLM:            Provider abstraction; provider/model/prompt version tracked

Suggested command:
  python generate_blueprint_v13_final.py \
      --v11 ./BLUEPRINT-V1.1-FINAL.md \
      --v12 ./AEGIS_Blueprint_V1.2.pdf \
      --out-dir ./artifacts

Dependencies:
  pip install pymupdf reportlab
"""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError as exc:
    raise SystemExit("Install PyMuPDF: pip install pymupdf") from exc

try:
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        PageBreak,
        Preformatted,
    )
except ImportError as exc:
    raise SystemExit("Install ReportLab: pip install reportlab") from exc


VERSION = "V1.3"
SYSTEM_NAME = "AEGIS — AUTONOMOUS TRADING INTELLIGENCE SYSTEM"
DOC_TITLE = "AEGIS V1.3 — CONSOLIDATED & ARCHITECTURE-FROZEN MASTER BLUEPRINT"


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def locate(explicit: str | None, patterns: list[str], roots: list[Path]) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Source not found: {p}")
        return p

    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            candidates.extend(root.glob(pattern))

    if not candidates:
        raise FileNotFoundError(
            f"Could not find source. Patterns: {patterns}"
        )

    candidates = sorted(
        set(candidates),
        key=lambda p: ("FINAL" in p.name.upper(), p.stat().st_mtime),
        reverse=True,
    )
    return candidates[0].resolve()


def read_text_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 5000:
        raise ValueError(f"Source appears incomplete: {path}")
    return text


def extract_pdf(path: Path) -> str:
    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc):
        pages.append(f"\n<!-- SOURCE V1.2 PAGE {i + 1} -->\n")
        pages.append(page.get_text("text"))
    text = "\n".join(pages)
    if len(text.strip()) < 5000:
        raise ValueError(f"V1.2 PDF extraction appears incomplete: {path}")
    return text


def clean_extracted_pdf(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^AEGIS.*Página \d+$", s, re.I):
            continue
        if re.match(r"^Página \d+$", s, re.I):
            continue
        if re.match(r"^BLUEPRINT.*Página \d+$", s, re.I):
            continue
        out.append(line.rstrip())
    return "\n".join(out)


# ---------------------------------------------------------------------------
# V1.3 architecture
# ---------------------------------------------------------------------------

V13_ARCHITECTURE = r"""
# V1.3 — ARCHITECTURE AUTHORITY

## 1. Status

**ARCHITECTURE FROZEN**

This document is the sole implementation authority for V1.3.

The coding agent is an implementer, not the architect.
The auditor is a verifier, not an implementer.

No architectural decision may be silently introduced into code.

If a required architectural decision is missing:

    STATUS = ARCHITECTURAL_BLOCKER

The agent must stop and request an architectural revision.

## 2. Scope

V1.3 is an autonomous swing-trading system for analysis, structured AI
decisions, deterministic risk control, execution, accounting, replay,
paper trading, observability, auditability, and controlled broker execution.

There is **no academic/thesis scope** in V1.3.

V1.3 supports two execution environments:

    SANDBOX
    LIVE

Both environments are implemented.

SANDBOX is the default and validation environment.

LIVE is fully implemented in the codebase but disabled by default and
protected by mandatory safety gates.

## 3. Canonical Pipeline

    Market Data
        ↓
    Context / Market State Builder
        ↓
    AI Decision Engine
        ↓
    Decision Contract
        ↓
    Risk Engine
        ↓
    Approved Order Intent
        ↓
    Execution Engine
        ↓
    Broker Adapter
        ├── SandboxBroker
        └── LiveBroker
        ↓
    Fill / Order State
        ↓
    Portfolio / Accounting
        ↓
    Audit / Observability
        ↓
    Dashboard

The same decision/risk/execution pipeline is used in SANDBOX and LIVE.

Only the broker adapter and environment-specific operational controls differ.

## 4. Environment Contract

    TRADING_ENVIRONMENT = SANDBOX | LIVE

Default:

    TRADING_ENVIRONMENT=SANDBOX

Required runtime controls:

    LIVE_ENABLED=false
    LIVE_CONFIRMATION_REQUIRED=true

Changing:

    TRADING_ENVIRONMENT=SANDBOX

to:

    TRADING_ENVIRONMENT=LIVE

must select LiveBroker without changing application code.

However, selecting LIVE does not itself authorize an order.

LIVE execution requires all mandatory gates to pass.

### LIVE gate

    TRADING_ENVIRONMENT == LIVE
        ↓
    LIVE_ENABLED == true
        ↓
    live confirmation valid
        ↓
    live credentials valid
        ↓
    broker connectivity valid
        ↓
    permissions validated
        ↓
    risk profile valid
        ↓
    kill switch available and inactive
        ↓
    live order/value/loss/position limits valid
        ↓
    system health valid
        ↓
    reconciliation valid
        ↓
    EXECUTE

Any failed condition:

    FAIL-CLOSED
        ↓
    NO LIVE ORDER

### Required properties

- LiveBroker is implemented.
- LiveBroker implements the same BrokerAdapter contract as SandboxBroker.
- LiveBroker cannot bypass Risk Engine.
- LiveBroker cannot be called directly by the LLM.
- LiveBroker cannot be called directly by the frontend.
- LIVE_ENABLED=false always blocks live execution.
- Invalid/missing live credentials block execution.
- Unknown order state requires reconciliation before continuation.
- Environment changes are auditable.
- Every live execution carries correlation_id and idempotency protection.

## 5. Environment Separation

Environment-specific secrets must be separate.

SANDBOX credentials must never be silently reused as LIVE credentials.

LIVE credentials must never be exposed to:

- frontend;
- LLM prompts;
- audit payloads;
- Git;
- source code;
- normal application logs.

The dashboard may display:

    SANDBOX
    LIVE (DISABLED)
    LIVE (READY)
    LIVE (BLOCKED)

but must never expose secret values.

## 6. Financial Safety

Critical monetary/quantity calculations use Decimal.

UTC is the internal time standard.

PostgreSQL is the financial source of truth.

Redis is not the financial source of truth.

No look-ahead is permitted.

Only information available at the decision timestamp may enter a decision.

Hard risk limits are deterministic and cannot be overridden by the LLM.

The LLM recommends.

The Risk Engine authorizes or rejects.

The Execution Engine executes only an approved Order Intent.

## 7. AI Authority

The LLM has no direct execution authority.

The LLM may:

- analyze market context;
- produce structured reasoning/decision output;
- provide confidence;
- recommend LONG/HOLD/CLOSE;
- provide requested trade parameters inside the Decision Contract.

The LLM may NOT:

- call a broker;
- submit an order;
- bypass Risk Engine;
- modify hard safety limits;
- access provider secrets;
- alter accounting;
- alter audit history.

Invalid or unavailable LLM output fails safely.

## 8. Replay

Replay is a first-class validation foundation, not a late feature.

Replay must reproduce historical decision points deterministically while
respecting information availability at each timestamp.

Replay validates:

- market state;
- AI input/output;
- decisions;
- risk;
- order intents;
- state transitions;
- portfolio;
- audit reconstruction.

Replay must never authorize live execution.

## 9. Failure / Recovery

Startup:

    Load Configuration
        ↓
    Database Connectivity
        ↓
    Load Portfolio
        ↓
    Reconcile Orders
        ↓
    Validate Positions
        ↓
    Validate Risk
        ↓
    Recover Workers
        ↓
    RUN or PAUSE

If state is ambiguous:

    PAUSE

Never guess.

LLM unavailable:

- no new entries;
- existing positions remain protected;
- deterministic risk/protection continues.

Market data inconsistent:

- no new decision.

Risk Engine unavailable:

- no execution.

Order state unknown:

- reconcile before continuing.

## 10. Security Invariants

Secrets:

- never committed to Git;
- never returned to frontend;
- never injected into LLM prompts;
- never stored in audit payloads;
- never hardcoded.

Frontend:

- cannot access secrets;
- cannot bypass Risk Engine;
- cannot directly create broker orders;
- cannot alter hard risk limits.

## 11. Observability

Track at minimum:

Technical:
- API latency;
- LLM latency;
- LLM token usage;
- worker queue depth;
- market data lag;
- WebSocket connections;
- DB latency;
- Redis latency;
- error rate.

AI:
- invalid output rate;
- timeout rate;
- risk rejection rate;
- LONG/HOLD/CLOSE distribution;
- confidence;
- decision consistency.

Trading:
- P&L;
- drawdown;
- win rate;
- profit factor;
- Sharpe;
- exposure;
- fees;
- slippage.

Every critical operation is auditable.

## 12. Dataset / Experiment Registry

Dataset:

    dataset_id
    source
    version
    symbols
    timeframe
    start
    end
    checksum
    created_at

Experiment:

    experiment_id
    model
    prompt_version
    dataset_id
    configuration
    seed
    results
    created_at
"""


V13_STACK = r"""
# V1.3 — FROZEN TECHNOLOGY STACK

| Layer | Technology |
|---|---|
| Runtime | Python 3.12+ |
| API | FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Database | PostgreSQL |
| Cache / messaging | Redis, only when necessary |
| Data / Quant | Polars / Pandas |
| Frontend | React + TypeScript + Vite |
| Testing | pytest |
| Containers | Docker + Docker Compose |
| LLM | Provider abstraction |
| Observability | Structured logs + metrics + audit trail |

Performance policy:

V1.3 is not optimized for low latency. The objective is determinism,
security, testability, maintainability, and speed of development.

A future performance optimization requires objective evidence and an ADR.
"""


V13_ADRS = r"""
# V1.3 — ADR REGISTER

## ADR-001 — AI Has No Direct Execution Authority
The LLM cannot call a broker or submit orders.

## ADR-002 — Risk Engine Is Mandatory
Every Order Intent must pass deterministic Risk Engine validation.

## ADR-003 — PostgreSQL Is Financial Source of Truth
Redis may support coordination/cache/messaging but cannot be the authoritative
financial ledger.

## ADR-004 — Decimal for Critical Financial Calculations
No binary floating-point arithmetic for critical monetary/quantity calculations.

## ADR-005 — UTC
UTC is the internal time standard.

## ADR-006 — Fail-Closed
Ambiguous, unavailable, or unsafe conditions block new execution.

## ADR-007 — Replay Is First-Class
Replay is a foundational validation mechanism.

## ADR-008 — Multi-Environment Execution
SANDBOX and LIVE implement the same BrokerAdapter contract and use the same
decision/risk/execution pipeline.

## ADR-009 — LIVE Implemented but Disabled by Default
LiveBroker is implemented in V1.3 but cannot execute while LIVE_ENABLED is false
or any required live gate fails.

## ADR-010 — Environment Selected by Configuration
TRADING_ENVIRONMENT=SANDBOX|LIVE selects the execution adapter without code
changes. The selected environment is auditable.

## ADR-011 — Architecture Freeze
Any unspecified architectural decision produces ARCHITECTURAL_BLOCKER.

## ADR-012 — No Academic Scope
V1.3 has no thesis/academic deliverable requirement.

## ADR Process

    Proposal
        ↓
    ADR
        ↓
    Blueprint Version
        ↓
    Implementation

No silent architectural decisions.
"""


V13_DELTA = r"""
# V1.3 — FORMAL DELTA / SUPERSESSION MATRIX

The table below prevents the exact context-loss problem identified in the
transition from V1.1 to V1.2.

| ID | Earlier decision | V1.3 decision | Status |
|---|---|---|---|
| D-001 | V1.1 baseline | Preserve all valid architectural content | PRESERVED |
| D-002 | V1.2 Replay was late | Replay is first-class foundation | ADOPTED |
| D-003 | V1.2 frozen stack | Freeze complete stack | ADOPTED |
| D-004 | V1.2 measurable gates | Mandatory measurable gates | ADOPTED |
| D-005 | V1.2 PASS_WITH_WARNINGS | Retained only when phase gate permits | ADOPTED |
| D-006 | 16 phases | Retain 16 architectural phases | CONFIRMED |
| D-007 | Academic scope | Explicitly removed | SUPERSEDED |
| D-008 | V1.2 repeated evidence blocks | Replace with this delta matrix | SUPERSEDED |
| D-009 | V1.1/V1.2 LiveBroker absent | LiveBroker fully implemented in V1.3 | SUPERSEDED |
| D-010 | LIVE outside V1 | LIVE implementation exists but is disabled | SUPERSEDED |
| D-011 | Sandbox/Paper only | SANDBOX + LIVE environments | SUPERSEDED |
| D-012 | Stack partially described | Full stack frozen | ADOPTED |
| D-013 | Fase 16 generic release | Explicit final certification gate | ADOPTED |
| D-014 | Coder could encounter ambiguity | ARCHITECTURAL_BLOCKER mandatory | PRESERVED |
| D-015 | V1 final target PAPER_READY | PAPER_READY remains V1 release target | CONFIRMED |

### Important interpretation

"LIVE implemented" is not equivalent to "LIVE certified."

V1.3 implements the LiveBroker and all required code paths so that the
environment can be switched without a code rewrite.

The V1 release certification remains:

    V1_STATUS = PAPER_READY

LIVE execution requires its own explicit operational gates and is disabled
by default.

This is deliberate separation between implementation completeness and
operational authorization.
"""


V13_GOVERNANCE = r"""
# V1.3 — IMPLEMENTATION GOVERNANCE

## Mandatory Phase Contract

Every phase MUST contain:

1. Objective
2. Scope
3. Out of Scope
4. Dependencies
5. Architectural View
6. Contracts Involved
7. Acceptance Criteria
8. Implementation Prompt
9. Audit Prompt
10. Gate
11. Required Evidence

The 16 phases are retained because they represent architectural boundaries
and dependency boundaries, not artificial granularity.

## Coding Agent Rules

Before coding:

- inspect repository;
- inspect existing implementation;
- read applicable V1.3 phase;
- read applicable ADRs;
- inspect dependencies from approved phases;
- do not reimplement existing contracts.

During coding:

- implement only authorized scope;
- write automated tests;
- preserve existing contracts;
- preserve state machines;
- preserve risk invariants;
- do not perform unrelated refactors;
- do not invent unspecified architecture.

At completion:

- run tests;
- list files changed;
- list tests executed;
- map acceptance criteria to evidence;
- report limitations;
- report ARCHITECTURAL_BLOCKERs;
- do not self-approve.

## Auditor Rules

The auditor must inspect the actual repository and not trust claims.

The auditor must:

- execute relevant tests;
- verify acceptance criteria;
- inspect contracts;
- inspect state machines;
- search for out-of-scope functionality;
- search for bypasses;
- search for secrets;
- search for financial float usage;
- verify UTC;
- verify correlation_id;
- verify idempotency;
- verify fail-closed behavior;
- verify no look-ahead;
- verify LLM has no direct execution authority;
- verify frontend cannot bypass Risk;
- verify SandboxBroker and LiveBroker both implement BrokerAdapter;
- verify LIVE is blocked when LIVE_ENABLED=false;
- verify environment switching requires no code modification;
- verify live gates;
- verify reconciliation behavior;
- verify audit trail.

The auditor does not silently repair code.

## Audit Status

PASS:
all mandatory criteria demonstrably satisfied.

PASS_WITH_WARNINGS:
only when the phase explicitly permits warnings and no mandatory safety or
architectural criterion is violated.

FAIL:
a required criterion is missing or violated.

ARCHITECTURAL_BLOCKER:
implementation requires a decision not specified by V1.3.

A phase cannot be approved merely because the project compiles.

## Phase Approval Gate

APPROVED only when:

- implementation complete;
- unit tests pass;
- integration tests pass;
- phase-specific tests pass;
- architecture verified;
- security verified;
- audit executed;
- deviations corrected;
- evidence collected;
- Git commit recorded;
- PHASE_XX_APPROVED.md created;
- no ARCHITECTURAL_BLOCKER remains.

## Required Evidence

Each phase must leave:

    docs/phases/PHASE_XX_APPROVED.md
    test output
    relevant logs/evidence
    Git commit
    audit report

The approval file must identify:

- blueprint version;
- phase;
- commit;
- test results;
- audit status;
- acceptance evidence;
- auditor;
- timestamp.
"""


V13_FINAL_CERTIFICATION = r"""
# V1.3 — FINAL CERTIFICATION

## Phase certification

    ALL 16 PHASES PASS
            ↓
    FULL E2E
            ↓
    REPLAY
            ↓
    BACKTEST
            ↓
    SECURITY AUDIT
            ↓
    CHAOS / RESILIENCE
            ↓
    RECOVERY
            ↓
    BACKUP / RESTORE
            ↓
    PAPER TRADING
            ↓
    ENVIRONMENT SWITCH TEST
            ↓
    LIVE SAFETY-GATE TESTS
            ↓
    AUDIT TRAIL INTEGRITY
            ↓
    FINAL APPROVAL
            ↓
    V1_STATUS = PAPER_READY

## Live implementation certification

The final V1 certification must also prove:

- LiveBroker is implemented.
- LiveBroker conforms to BrokerAdapter.
- LiveBroker cannot bypass Risk Engine.
- LIVE_ENABLED=false blocks live orders.
- missing credentials block live orders.
- failed health checks block live orders.
- failed reconciliation blocks live orders.
- invalid risk configuration blocks live orders.
- kill switch blocks live orders.
- environment selection changes adapter without code changes.
- live orders preserve idempotency and correlation_id.
- live order state is reconciled.
- live execution is auditable.

## Important

The V1 system is therefore:

    SANDBOX = IMPLEMENTED + DEFAULT + ENABLED

    LIVE = IMPLEMENTED + DISABLED BY DEFAULT

This does NOT mean LIVE is certified for unrestricted production use.

Operational LIVE authorization is a separate release decision.

## Final Invariants

Never permit:

    LLM → Broker
    Frontend → Broker
    Risk bypass
    Unknown-order continuation
    Ambiguous-state continuation
    Secret exposure
    Look-ahead
    Financial float for critical calculations
    LIVE execution when any gate fails
"""


# ---------------------------------------------------------------------------
# Prompts for all 16 phases
# ---------------------------------------------------------------------------

PHASES = [
    {
        "num": "01",
        "name": "Foundation, Stack & Repository",
        "scope": (
            "Freeze stack, monorepo, Docker Compose, environment configuration, "
            "health checks, domain skeleton and CI-quality foundations."
        ),
        "out_scope": "No broker execution, no operational AI, no functional trading.",
        "criteria": [
            ("AC-01.01", "Python 3.12+ is used by the project."),
            ("AC-01.02", "FastAPI starts successfully."),
            ("AC-01.03", "Docker image builds successfully and reproducibly."),
            ("AC-01.04", "Docker Compose starts the services defined for the phase."),
            ("AC-01.05", "Configuration is loaded through environment/configuration mechanisms, not hardcoded."),
            ("AC-01.06", "TRADING_ENVIRONMENT accepts only SANDBOX or LIVE."),
            ("AC-01.07", "SANDBOX is the default trading environment."),
            ("AC-01.08", "LIVE_ENABLED defaults to false."),
            ("AC-01.09", "No secrets are hardcoded in source code."),
            ("AC-01.10", "Application health check responds successfully."),
            ("AC-01.11", "Automated test infrastructure is functional."),
            ("AC-01.12", "The project test command is reproducible."),
        ],
    },
    {
        "num": "02",
        "name": "Domain Contracts & State Machines",
        "scope": (
            "Implement Domain Contracts, enums, Event Contracts, State Machines, "
            "Time Contract, correlation_id and idempotency conventions."
        ),
        "out_scope": "No database persistence, broker calls or LLM execution.",
        "criteria": [
            ("AC-02.01", "Domain Contracts are explicitly defined."),
            ("AC-02.02", "Domain enums are centralized."),
            ("AC-02.03", "Order State Machine has explicit valid transitions."),
            ("AC-02.04", "Position State Machine has explicit valid transitions."),
            ("AC-02.05", "Decision State Machine has explicit valid transitions."),
            ("AC-02.06", "Invalid state transitions are rejected."),
            ("AC-02.07", "Domain events have validated schemas."),
            ("AC-02.08", "correlation_id is propagated through critical operations."),
            ("AC-02.09", "Idempotency keys are supported."),
            ("AC-02.10", "Internal timestamps use UTC."),
            ("AC-02.11", "Tests cover valid and invalid state transitions."),
        ],
    },
    {
        "num": "03",
        "name": "PostgreSQL, ORM & Persistence",
        "scope": (
            "Implement PostgreSQL schema, SQLAlchemy models, Alembic migrations "
            "and repository patterns with PostgreSQL as financial source of truth."
        ),
        "out_scope": "No live execution and no bypass of domain contracts.",
        "criteria": [
            ("AC-03.01", "PostgreSQL is the primary financial database."),
            ("AC-03.02", "SQLAlchemy 2.x is configured."),
            ("AC-03.03", "Alembic is configured."),
            ("AC-03.04", "Database migrations can be executed from an empty database."),
            ("AC-03.05", "Migration strategy is documented and reversible where applicable."),
            ("AC-03.06", "Required financial entities have persistence models."),
            ("AC-03.07", "Repository layer does not violate Domain Contracts."),
            ("AC-03.08", "Critical persistence operations have consistent transaction behavior."),
            ("AC-03.09", "Critical monetary and quantity values do not rely on binary floating point."),
            ("AC-03.10", "Persisted state survives application restart."),
            ("AC-03.11", "Persistence tests pass."),
        ],
    },
    {
        "num": "04",
        "name": "Market Data & Context Builder",
        "scope": (
            "Implement market data ingestion, candle validation, closed-candle "
            "policy, normalization, Market State and Context Builder."
        ),
        "out_scope": "No trading decision execution.",
        "criteria": [
            ("AC-04.01", "Market data can be ingested through the defined abstraction."),
            ("AC-04.02", "Market timestamps are normalized to UTC."),
            ("AC-04.03", "Invalid market data is rejected or quarantined."),
            ("AC-04.04", "Candles pass consistency validation."),
            ("AC-04.05", "Closed-candle policy is explicitly enforced."),
            ("AC-04.06", "Market State is deterministic for the same input."),
            ("AC-04.07", "Context Builder never uses future information."),
            ("AC-04.08", "LLM context is reproducible from the recorded market state."),
            ("AC-04.09", "Market-data lag is detectable."),
            ("AC-04.10", "Tests demonstrate absence of look-ahead."),
        ],
    },
    {
        "num": "05",
        "name": "LLM Provider & AI Decision Engine",
        "scope": (
            "Implement provider abstraction, model configuration, prompt versioning, "
            "AI runs, structured Decision Contract validation and safe failure."
        ),
        "out_scope": "LLM cannot call broker, Risk Engine or Execution Engine directly.",
        "criteria": [
            ("AC-05.01", "An abstract LLM provider interface exists."),
            ("AC-05.02", "Provider and model can be selected through configuration."),
            ("AC-05.03", "The selected provider/model is recorded for each AI run."),
            ("AC-05.04", "Prompt versions are explicit and traceable."),
            ("AC-05.05", "LLM inputs are recorded without secrets."),
            ("AC-05.06", "LLM output is validated against the Decision Contract."),
            ("AC-05.07", "Invalid LLM output is rejected safely."),
            ("AC-05.08", "LLM timeout produces safe behavior."),
            ("AC-05.09", "LLM has no direct broker access."),
            ("AC-05.10", "LLM cannot modify hard Risk limits."),
            ("AC-05.11", "Decision ID and correlation_id are preserved."),
            ("AC-05.12", "Tests cover valid, invalid and timeout responses."),
        ],
    },
    {
        "num": "06",
        "name": "Deterministic Risk Engine",
        "scope": (
            "Implement deterministic risk limits, position sizing, exposure, "
            "stop requirements, daily loss limits, kill switch and reason codes."
        ),
        "out_scope": "No risk bypass and no LLM override of hard limits.",
        "criteria": [
            ("AC-06.01", "Risk Engine accepts only valid Decision Contracts."),
            ("AC-06.02", "Position sizing is deterministic."),
            ("AC-06.03", "Exposure limits are enforced."),
            ("AC-06.04", "Maximum position size is enforced."),
            ("AC-06.05", "Required stop-loss conditions are validated."),
            ("AC-06.06", "Daily loss limit is enforced."),
            ("AC-06.07", "Kill switch blocks new orders."),
            ("AC-06.08", "Risk rejection produces a machine-readable reason code."),
            ("AC-06.09", "LLM cannot override hard Risk limits."),
            ("AC-06.10", "An unapproved order can never reach Execution Engine."),
            ("AC-06.11", "Critical financial calculations use Decimal."),
            ("AC-06.12", "Tests cover limits, boundary conditions and rejection paths."),
        ],
    },
    {
        "num": "07",
        "name": "Portfolio & Accounting",
        "scope": (
            "Implement cash, positions, fills, realized/unrealized P&L, "
            "average cost, exposure and deterministic accounting."
        ),
        "out_scope": "Accounting must not depend on the LLM as source of truth.",
        "criteria": [
            ("AC-07.01", "Cash balance is maintained."),
            ("AC-07.02", "Positions are maintained."),
            ("AC-07.03", "Fills update positions correctly."),
            ("AC-07.04", "Average cost is calculated deterministically."),
            ("AC-07.05", "Realized P&L is calculated."),
            ("AC-07.06", "Unrealized P&L is calculated."),
            ("AC-07.07", "Fees are accounted for."),
            ("AC-07.08", "Exposure is calculated."),
            ("AC-07.09", "Accounting does not depend on LLM output."),
            ("AC-07.10", "Financial state survives restart."),
            ("AC-07.11", "Accounting tests cover entries, exits, partial fills and fees."),
        ],
    },
    {
        "num": "08",
        "name": "Broker Contract & Sandbox Execution",
        "scope": (
            "Implement BrokerAdapter contract, SandboxBroker, order lifecycle, "
            "idempotency and simulated fills."
        ),
        "out_scope": "Broker execution must never bypass Risk Engine.",
        "criteria": [
            ("AC-08.01", "BrokerAdapter contract is explicitly defined."),
            ("AC-08.02", "SandboxBroker implements BrokerAdapter."),
            ("AC-08.03", "Order submission works in Sandbox."),
            ("AC-08.04", "Order cancellation works in Sandbox."),
            ("AC-08.05", "Order status retrieval works."),
            ("AC-08.06", "Sandbox fill simulation works."),
            ("AC-08.07", "Idempotency prevents duplicate orders."),
            ("AC-08.08", "Order lifecycle follows the Order State Machine."),
            ("AC-08.09", "Broker cannot receive an order without Risk approval."),
            ("AC-08.10", "Sandbox never uses LIVE credentials."),
            ("AC-08.11", "Sandbox execution tests pass."),
        ],
    },
    {
        "num": "09",
        "name": "Live Broker Implementation & Environment Switching",
        "scope": (
            "Implement LiveBroker using the same BrokerAdapter contract, "
            "environment selection and mandatory LIVE safety gates."
        ),
        "out_scope": "LIVE must remain disabled by default and cannot bypass safety gates.",
        "criteria": [
            ("AC-09.01", "LiveBroker is implemented."),
            ("AC-09.02", "LiveBroker fully implements BrokerAdapter."),
            ("AC-09.03", "Sandbox and Live use the same execution contract."),
            ("AC-09.04", "TRADING_ENVIRONMENT=SANDBOX selects SandboxBroker."),
            ("AC-09.05", "TRADING_ENVIRONMENT=LIVE selects LiveBroker."),
            ("AC-09.06", "Changing environment requires no application-code modification."),
            ("AC-09.07", "LIVE_ENABLED=false blocks every LIVE order attempt."),
            ("AC-09.08", "Invalid or missing LIVE credentials block execution."),
            ("AC-09.09", "Broker connectivity failure blocks LIVE execution."),
            ("AC-09.10", "Failed broker health check blocks LIVE execution."),
            ("AC-09.11", "Invalid LIVE risk configuration blocks execution."),
            ("AC-09.12", "Active kill switch blocks LIVE execution."),
            ("AC-09.13", "LIVE order, exposure, loss and position limits are enforced."),
            ("AC-09.14", "LiveBroker cannot be called directly by the LLM."),
            ("AC-09.15", "LiveBroker cannot be called directly by the frontend."),
            ("AC-09.16", "LiveBroker cannot bypass Risk Engine."),
            ("AC-09.17", "Every LIVE execution attempt is auditable."),
            ("AC-09.18", "LIVE secrets never appear in logs, frontend or audit payloads."),
            ("AC-09.19", "Fail-closed behavior is covered by automated tests."),
            ("AC-09.20", "SANDBOX→LIVE switching is proven without code changes."),
        ],
    },
    {
        "num": "10",
        "name": "Execution Engine, Reconciliation & Recovery",
        "scope": (
            "Implement orchestration from approved Order Intent to broker adapter, "
            "ACK/fill tracking, reconciliation, restart recovery and fail-closed behavior."
        ),
        "out_scope": "No execution from unapproved intents.",
        "criteria": [
            ("AC-10.01", "Only Approved Order Intent can be executed."),
            ("AC-10.02", "Execution Engine uses BrokerAdapter."),
            ("AC-10.03", "Order acknowledgement is processed."),
            ("AC-10.04", "Partial fills are processed."),
            ("AC-10.05", "Full fills are processed."),
            ("AC-10.06", "Cancellation is processed."),
            ("AC-10.07", "Unknown order state triggers reconciliation."),
            ("AC-10.08", "Application restart triggers required reconciliation."),
            ("AC-10.09", "System never assumes order success without confirmation."),
            ("AC-10.10", "Idempotency prevents duplicate order submission."),
            ("AC-10.11", "Risk Engine failure blocks execution."),
            ("AC-10.12", "Recovery behavior is tested."),
        ],
    },
    {
        "num": "11",
        "name": "Replay Engine",
        "scope": (
            "Implement deterministic historical replay with timestamp-correct "
            "information availability and no look-ahead."
        ),
        "out_scope": "Replay cannot authorize or send LIVE execution.",
        "criteria": [
            ("AC-11.01", "Replay accepts a versioned dataset."),
            ("AC-11.02", "Replay preserves historical timestamps."),
            ("AC-11.03", "Replay uses only information available at each timestamp."),
            ("AC-11.04", "Look-ahead is impossible or explicitly detected."),
            ("AC-11.05", "Historical Market State can be reconstructed."),
            ("AC-11.06", "AI Decision can be reproduced or deterministically stubbed."),
            ("AC-11.07", "Risk decisions can be reproduced."),
            ("AC-11.08", "Order Intents can be reproduced."),
            ("AC-11.09", "Portfolio state can be reconstructed."),
            ("AC-11.10", "Replay audit trail is reconstructible."),
            ("AC-11.11", "Replay cannot invoke LIVE execution."),
            ("AC-11.12", "Repeated replay with identical inputs is deterministic within defined tolerances."),
        ],
    },
    {
        "num": "12",
        "name": "Backtest, Metrics & Experiment Registry",
        "scope": (
            "Implement backtest execution, dataset registry, experiment registry, "
            "metrics, reproducibility and result persistence."
        ),
        "out_scope": "Backtest cannot execute real broker orders.",
        "criteria": [
            ("AC-12.01", "Each dataset has an ID and version."),
            ("AC-12.02", "Each dataset has a checksum."),
            ("AC-12.03", "Each experiment has an ID."),
            ("AC-12.04", "Model identity is recorded."),
            ("AC-12.05", "Prompt version is recorded."),
            ("AC-12.06", "Experiment configuration is recorded."),
            ("AC-12.07", "Seed is recorded when applicable."),
            ("AC-12.08", "Backtest results are persisted."),
            ("AC-12.09", "P&L is calculated."),
            ("AC-12.10", "Drawdown is calculated."),
            ("AC-12.11", "Win rate is calculated."),
            ("AC-12.12", "Profit factor is calculated."),
            ("AC-12.13", "Sharpe is calculated when applicable."),
            ("AC-12.14", "Backtest cannot submit real broker orders."),
        ],
    },
    {
        "num": "13",
        "name": "Audit, Observability & Security",
        "scope": (
            "Implement end-to-end audit trail, structured logs, metrics, "
            "secret handling, security controls and trace reconstruction."
        ),
        "out_scope": "No secret leakage.",
        "criteria": [
            ("AC-13.01", "Every critical operation produces an audit event."),
            ("AC-13.02", "correlation_id enables end-to-end operation tracing."),
            ("AC-13.03", "Audit records decision, risk, order and outcome."),
            ("AC-13.04", "Logs are structured."),
            ("AC-13.05", "Required operational metrics are available."),
            ("AC-13.06", "LLM latency is observable."),
            ("AC-13.07", "Market-data lag is observable."),
            ("AC-13.08", "Risk rejection is observable."),
            ("AC-13.09", "Secrets never appear in logs."),
            ("AC-13.10", "Secrets never appear in audit records."),
            ("AC-13.11", "Secrets never reach the frontend."),
            ("AC-13.12", "Security scan finds no hardcoded secrets."),
            ("AC-13.13", "A trading operation can be reconstructed from the audit trail."),
        ],
    },
    {
        "num": "14",
        "name": "Dashboard & Operational UI",
        "scope": (
            "Implement dashboard for positions, P&L, orders, audit, risk, "
            "environment, provider/LLM configuration and operational status."
        ),
        "out_scope": "Frontend cannot execute broker orders or alter hard risk rules.",
        "criteria": [
            ("AC-14.01", "Dashboard displays current trading environment."),
            ("AC-14.02", "Dashboard displays system health/status."),
            ("AC-14.03", "Dashboard displays open positions."),
            ("AC-14.04", "Dashboard displays P&L."),
            ("AC-14.05", "Dashboard displays orders."),
            ("AC-14.06", "Dashboard displays exposure."),
            ("AC-14.07", "Dashboard displays Risk status."),
            ("AC-14.08", "Provider/LLM configuration is visible without exposing secrets."),
            ("AC-14.09", "Configured stop-loss settings can be viewed/managed according to authorized permissions."),
            ("AC-14.10", "Dashboard clearly displays LIVE state as DISABLED, BLOCKED or READY."),
            ("AC-14.11", "Frontend cannot call Broker directly."),
            ("AC-14.12", "Frontend cannot bypass Risk Engine."),
            ("AC-14.13", "Frontend cannot alter hard safety limits."),
            ("AC-14.14", "Critical actions require confirmation and produce audit events."),
        ],
    },
    {
        "num": "15",
        "name": "E2E, Chaos, Recovery & Release Gates",
        "scope": (
            "Implement comprehensive E2E, failure injection, recovery, backup/restore, "
            "environment-switch and LIVE safety-gate tests."
        ),
        "out_scope": "Do not certify unrestricted production LIVE operation.",
        "criteria": [
            ("AC-15.01", "Complete SANDBOX pipeline operates successfully."),
            ("AC-15.02", "Market Data→AI→Risk→Execution→Fill→Portfolio→Audit works end-to-end."),
            ("AC-15.03", "LLM timeout is handled safely."),
            ("AC-15.04", "Market-data failure is handled safely."),
            ("AC-15.05", "Database failure is handled safely."),
            ("AC-15.06", "Redis failure is handled safely when Redis is used."),
            ("AC-15.07", "Broker failure is handled safely."),
            ("AC-15.08", "Unknown order state is reconciled."),
            ("AC-15.09", "Restart recovery works."),
            ("AC-15.10", "Backup and restore are verified."),
            ("AC-15.11", "Kill switch works end-to-end."),
            ("AC-15.12", "Risk limits work end-to-end."),
            ("AC-15.13", "Environment switching works without code modification."),
            ("AC-15.14", "LIVE remains blocked when LIVE_ENABLED=false."),
            ("AC-15.15", "All mandatory LIVE safety gates are tested."),
            ("AC-15.16", "Replay works end-to-end."),
            ("AC-15.17", "Backtest works end-to-end."),
            ("AC-15.18", "Audit reconstruction works end-to-end."),
        ],
    },
    {
        "num": "16",
        "name": "Final Certification",
        "scope": (
            "Execute final certification sequence, verify every phase, invariant, "
            "release gate and produce final certification artifacts."
        ),
        "out_scope": "Do not introduce new architecture during certification.",
        "criteria": [
            ("AC-16.01", "All previous 15 phases have formal approval."),
            ("AC-16.02", "No ARCHITECTURAL_BLOCKER remains open."),
            ("AC-16.03", "No mandatory FAIL remains open."),
            ("AC-16.04", "Complete automated test suite passes."),
            ("AC-16.05", "End-to-end suite passes."),
            ("AC-16.06", "Replay certification passes."),
            ("AC-16.07", "Backtest certification passes."),
            ("AC-16.08", "Security audit passes."),
            ("AC-16.09", "Chaos and recovery certification passes."),
            ("AC-16.10", "Backup/restore certification passes."),
            ("AC-16.11", "Sandbox execution certification passes."),
            ("AC-16.12", "LiveBroker implementation is certified against BrokerAdapter."),
            ("AC-16.13", "LIVE safety gates are certified."),
            ("AC-16.14", "LIVE_ENABLED=false is proven to prevent LIVE execution."),
            ("AC-16.15", "Environment switching is proven without code modification."),
            ("AC-16.16", "Audit trail integrity and reconstruction are proven."),
            ("AC-16.17", "No secret leakage is identified."),
            ("AC-16.18", "No Risk Engine bypass is identified."),
            ("AC-16.19", "No LLM→Broker execution path exists."),
            ("AC-16.20", "No Frontend→Broker execution path exists."),
            ("AC-16.21", "V1_STATUS is PAPER_READY."),
            ("AC-16.22", "LIVE remains IMPLEMENTED + DISABLED BY DEFAULT."),
        ],
    },
]


def phase_prompt(phase: dict) -> str:
    num = phase["num"]
    name = phase["name"]
    scope = phase["scope"]
    out_scope = phase["out_scope"]
    criteria = phase["criteria"]

    criteria_md = "\n".join(
        f"- **{cid}** — {description}"
        for cid, description in criteria
    )

    criteria_plain = "\n".join(
        f"{cid} | {description}"
        for cid, description in criteria
    )

    return f"""
# FASE {num} — {name}

## Objetivo

{scope}

## Escopo

{scope}

## Fora do Escopo

{out_scope}

## Visão Arquitetural

Implement strictly within the V1.3 architecture.

Preserve existing Domain Contracts and State Machines.

The canonical pipeline is:

Market Data
→ Context
→ AI Decision
→ Decision Contract
→ Risk Engine
→ Approved Order Intent
→ Execution Engine
→ Broker Adapter
→ Fill
→ Portfolio
→ Audit.

SANDBOX and LIVE share the same decision/risk/execution pipeline.

Environment selection is configuration-driven:

    TRADING_ENVIRONMENT=SANDBOX|LIVE

SANDBOX is the default.

LIVE is fully implemented but disabled by default.

LIVE_ENABLED=false must fail-closed.

---

## Acceptance Criteria

The following criteria are the **authoritative acceptance contract for this
phase**.

The coding agent MUST implement them.

The auditor MUST verify every criterion individually.

{criteria_md}

---

## Prompt de Implementação

```text
You are the coding agent for AEGIS V1.3.

AUTHORITATIVE DOCUMENT:
    AEGIS V1.3 — CONSOLIDATED & ARCHITECTURE-FROZEN MASTER BLUEPRINT

You are NOT the architect.

Implement ONLY:

    PHASE {num} — {name}

SCOPE:
    {scope}

OUT OF SCOPE:
    {out_scope}

ACCEPTANCE CRITERIA:

{criteria_plain}

MANDATORY ARCHITECTURAL RULES:

1. Do not invent architecture.
2. Do not change Domain Contracts without a formal V1.3 revision.
3. Do not change State Machines without a formal V1.3 revision.
4. Do not bypass Risk Engine.
5. Do not give the LLM execution authority.
6. Do not let Frontend call Broker directly.
7. Use Decimal for critical financial calculations.
8. Use UTC internally.
9. Preserve correlation_id.
10. Preserve idempotency.
11. Never introduce look-ahead.
12. Never hardcode secrets.
13. Never expose secrets to frontend, prompts or audit payloads.
14. PostgreSQL remains financial source of truth.
15. Fail closed on ambiguous or unsafe conditions.
16. If architecture is unspecified or contradictory, STOP and report:
       STATUS = ARCHITECTURAL_BLOCKER
17. Do not silently resolve architectural contradictions.
18. Write automated tests for implemented behavior.
19. Do not modify unrelated components.
20. Do not declare the phase APPROVED.

ENVIRONMENT RULE:

    SANDBOX is the default.
    LIVE is implemented but disabled by default.

If this phase touches execution:

    TRADING_ENVIRONMENT=SANDBOX|LIVE

must select the appropriate broker adapter without application-code changes.

    LIVE_ENABLED=false

must always fail-closed.

IMPLEMENTATION REQUIREMENT:

For EVERY Acceptance Criterion:

    - implement the required behavior;
    - create or update an automated test where applicable;
    - preserve evidence showing how the criterion was satisfied.

At completion report:

- files created/changed;
- tests executed;
- Acceptance Criteria status;
- evidence location for each AC;
- architectural decisions encountered;
- ARCHITECTURAL_BLOCKERs, if any;
- limitations;
- proposed commit message.

Do NOT claim PASS.
Do NOT mark the phase approved.
"""


def build_phase_plan() -> str:
    return (
        "# V1.3 — 16-PHASE IMPLEMENTATION PLAN\n\n"
        + "\n\n---\n\n".join(
            phase_prompt(phase)
            for phase in PHASES
        )
    )


# ---------------------------------------------------------------------------
# Build final Markdown
# ---------------------------------------------------------------------------

def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_markdown(v11: str, v12: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    historical = f"""
# APPENDIX A — HISTORICAL V1.1 BASELINE

The complete V1.1 source is preserved below for traceability.

IMPORTANT:
V1.3 is the current authority. Any clause in this historical appendix that
conflicts with the V1.3 Supersession Matrix is superseded by V1.3.

This preserves context without allowing obsolete V1.1 decisions to control
implementation.

---

{v11.rstrip()}
"""

    v12_digest = f"""
# APPENDIX B — V1.2 SOURCE DIGEST

The V1.2 source is retained as a traceability source, but repeated evidence
blocks are intentionally NOT copied into the main blueprint.

Source SHA-256:

    {source_hash(v12)}

V1.2 contributions incorporated into V1.3 include:

- Replay-first validation;
- frozen technology stack;
- measurable acceptance criteria;
- explicit phase audits;
- PASS_WITH_WARNINGS;
- release-gate thinking;
- LLM governance;
- 16-phase implementation structure;
- academic scope removal.

The full V1.2 source remains external traceability material; V1.3 itself is
the implementation authority.
"""

    header = f"""---
title: "{DOC_TITLE}"
version: "{VERSION}"
status: "ARCHITECTURE FROZEN"
generated_at: "{now}"
baseline: "V1.1"
revision_source: "V1.2"
---

# {SYSTEM_NAME}

# {DOC_TITLE}

> **ARCHITECTURE FROZEN**
>
> This is the final V1.3 implementation authority.
>
> V1.1 = historical architectural baseline.
>
> V1.2 = revision/delta source.
>
> V1.3 = consolidated authority.

## Executive Summary

AEGIS V1.3 is an autonomous swing-trading platform using AI for market
analysis and structured decision generation, deterministic risk control,
broker execution, accounting, replay, paper trading, observability and
auditability.

The architecture supports both:

    SANDBOX
    LIVE

Both are implemented through the same canonical pipeline.

SANDBOX is enabled by default.

LIVE is implemented but disabled by default.

Changing:

    TRADING_ENVIRONMENT=SANDBOX

to:

    TRADING_ENVIRONMENT=LIVE

must select the LiveBroker without a code change. Live execution still
requires every mandatory LIVE safety gate to pass.

The V1 release target is:

    V1_STATUS = PAPER_READY

This means the complete system is implemented and validated for replay/paper
operation, while LIVE remains disabled by default and requires explicit
operational authorization.

"""

    return (
        header
        + V13_ARCHITECTURE
        + "\n\n"
        + V13_STACK
        + "\n\n"
        + V13_ADRS
        + "\n\n"
        + V13_DELTA
        + "\n\n"
        + V13_GOVERNANCE
        + "\n\n"
        + build_phase_plan()
        + "\n\n"
        + V13_FINAL_CERTIFICATION
        + "\n\n"
        + historical
        + "\n\n"
        + v12_digest
    )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def md_to_flowables(md: str):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="Cover",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=25,
        spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        name="H1V13",
        parent=styles["Heading1"],
        fontSize=15,
        leading=19,
        spaceBefore=13,
        spaceAfter=7,
    ))
    styles.add(ParagraphStyle(
        name="H2V13",
        parent=styles["Heading2"],
        fontSize=11.5,
        leading=15,
        spaceBefore=9,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="BodyV13",
        parent=styles["BodyText"],
        fontSize=8.6,
        leading=12.3,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="CodeV13",
        fontName="Courier",
        fontSize=6.5,
        leading=8.5,
        leftIndent=5,
        rightIndent=5,
        spaceBefore=3,
        spaceAfter=5,
    ))

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    flow = []
    code = False
    code_lines = []
    first_title = True

    for raw in md.splitlines():
        line = raw.rstrip()

        if line.startswith("```"):
            if code:
                code = False
                if code_lines:
                    flow.append(Preformatted(
                        "\n".join(code_lines), styles["CodeV13"]
                    ))
                    code_lines = []
            else:
                code = True
            continue

        if code:
            code_lines.append(line)
            continue

        if line.startswith("---"):
            continue

        if not line.strip():
            flow.append(Spacer(1, 2.5))
            continue

        if line.startswith("# "):
            if not first_title:
                flow.append(PageBreak())
            first_title = False
            flow.append(Paragraph(esc(line[2:]), styles["H1V13"]))
            continue

        if line.startswith("## "):
            flow.append(Paragraph(esc(line[3:]), styles["H1V13"]))
            continue

        if line.startswith("### "):
            flow.append(Paragraph(esc(line[4:]), styles["H2V13"]))
            continue

        if line.startswith("> "):
            flow.append(Paragraph("<b>" + esc(line[2:]) + "</b>",
                                  styles["BodyV13"]))
            continue

        if line.startswith("- "):
            flow.append(Paragraph("• " + esc(line[2:]), styles["BodyV13"]))
            continue

        text = esc(line)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"`([^`]+)`",
                      r"<font name='Courier'>\1</font>", text)
        flow.append(Paragraph(text, styles["BodyV13"]))

    return flow


def make_pdf(markdown: str, output: Path):
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(
            15 * mm, 8 * mm,
            "AEGIS V1.3 — Architecture Frozen"
        )
        canvas.drawRightString(
            A4[0] - 15 * mm, 8 * mm,
            f"Page {doc.page}"
        )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=DOC_TITLE,
        author="AEGIS Architecture",
        subject="Consolidated and Architecture-Frozen V1.3 Blueprint",
    )
    doc.build(
        md_to_flowables(markdown),
        onFirstPage=footer,
        onLaterPages=footer,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate AEGIS V1.3 MD + PDF"
    )
    parser.add_argument("--v11", default=None)
    parser.add_argument("--v12", default=None)
    parser.add_argument("--out-dir", default="./artifacts")
    args = parser.parse_args()

    roots = [
        Path.cwd(),
        Path.cwd() / "artifacts",
        Path("/mnt/data"),
    ]

    v11_path = locate(
        args.v11,
        ["*V1.1*FINAL*.md", "*V1.1*.md", "*V1.1*.txt"],
        roots,
    )
    v12_path = locate(
        args.v12,
        ["*V1.2*.pdf"],
        roots,
    )

    print(f"[V1.3] V1.1 source: {v11_path}")
    print(f"[V1.3] V1.2 source: {v12_path}")

    v11 = read_text_file(v11_path)
    v12 = clean_extracted_pdf(extract_pdf(v12_path))

    markdown = build_markdown(v11, v12)

    out = Path(args.out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    md_path = out / "AEGIS-BLUEPRINT-V1.3-FINAL.md"
    pdf_path = out / "AEGIS-BLUEPRINT-V1.3-FINAL.pdf"

    md_path.write_text(markdown, encoding="utf-8")
    make_pdf(markdown, pdf_path)

    print()
    print("=== AEGIS V1.3 GENERATED ===")
    print(f"Markdown: {md_path}")
    print(f"PDF:      {pdf_path}")
    print(f"Characters: {len(markdown):,}")
    print()
    print("Architecture:")
    print("  SANDBOX = IMPLEMENTED + DEFAULT + ENABLED")
    print("  LIVE    = IMPLEMENTED + DISABLED BY DEFAULT")
    print("  LIVE switch requires no code change")
    print("  LIVE safety gates are mandatory")
    print()
    print("Academic scope: REMOVED")
    print("Phases: 16")
    print("Replay: FIRST-CLASS")
    print("Audit: PASS / PASS_WITH_WARNINGS / FAIL / ARCHITECTURAL_BLOCKER")
    print("Final target: PAPER_READY")


if __name__ == "__main__":
    main()
