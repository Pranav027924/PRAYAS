# PRAYAS — Low-Level Design

**Companion to:** `PRAYAS-HLD.md`
**Scope:** Schemas, state machines, algorithms, API contracts, concurrency control, test plan

---

## 1. Module map

```
prayas/
├── ingest/
│   ├── webhook.py          # signature verify, dedupe, persist
│   └── projector.py        # event log → domain state
├── domain/
│   ├── models.py           # Mandate, Cycle, Attempt
│   ├── states.py           # state machines + legal transitions
│   └── rails.py            # rail adapters (UPI Autopay, card e-mandate)
├── inference/
│   ├── cause.py            # ② latent cause posterior
│   ├── hazard.py           # ③ discrete-time survival model
│   ├── nowcast.py          # ④ Wilson + CUSUM issuer health
│   └── calibration.py      # reliability curves, ECE, PSI
├── sequencer/
│   ├── dp.py               # ⑤ the dynamic program
│   ├── windows.py          # legality mask construction
│   └── economics.py        # cost model, annoyance, revocation penalty
├── notify/
│   ├── optimizer.py        # ⑥ PDN timing / channel / content
│   └── templates.py        # DLT template slots
├── gate/
│   ├── engine.py           # ⑦ rule evaluation
│   ├── predicate.py        # sandboxed expression evaluator
│   └── rules/*.yaml        # versioned rule definitions
├── executor/
│   ├── workflows.py        # ⑧ durable workflows
│   ├── firing.py           # fire-time revalidation transaction
│   └── outbox.py           # transactional outbox relay
├── ledger/
│   ├── chain.py            # ⑨ hash chain writer
│   └── replay.py           # decision reconstruction
├── measure/
│   ├── assignment.py       # deterministic holdout hashing
│   ├── cuped.py            # variance reduction
│   └── ope.py              # ⑩ IPS / doubly-robust
├── llm/
│   ├── explain.py          # audit record → prose (grounded)
│   ├── parse_reply.py      # inbound Hinglish → liquidity hint
│   └── parse_policy.py     # merchant NL → proposed rules
├── sim/
│   └── generator.py        # synthetic mandate cycles with ground truth
└── api/
    └── routes.py
```

---

## 2. Domain model and state machines

### 2.1 Entities

```
Mandate  1 ──── N  Cycle  1 ──── N  Attempt
                     │
                     └──── N  Notification
```

A **Cycle** owns the attempt budget. It is simultaneously the unit of randomisation, measurement, and audit — keeping those three aligned is what makes the incrementality claim coherent.

### 2.2 Mandate state machine

```
   created ──AFA success──► active ──pause──► paused ──resume──► active
                              │                 │
                              ├──revoke─────────┴──────► revoked  (terminal)
                              └──validity end──────────► expired  (terminal)
```

### 2.3 Cycle state machine

```
              ┌──────────────────────────────────────────┐
              │                                          │
  scheduled ──► pdn_sent ──► executing ──► succeeded (terminal)
      │             │            │
      │             │            ├──► budget_exhausted (terminal)
      │             │            ├──► stopped_economic (terminal, EV < cost)
      │             │            └──► stopped_hard     (terminal, hard decline / revoked)
      │             │
      │             └──► pdn_failed ──► deferred (retry PDN, cannot debit)
      │
      └──► superseded (terminal — next cycle began, e.g. customer paid out of band)
```

Terminal states are absorbing; the projector rejects any transition out of one and emits a `stale_transition` metric rather than raising, since out-of-order webhooks make these routine.

### 2.4 Attempt state machine

```
  planned ──gate ALLOW──► gated ──fire──► fired ──webhook──► succeeded
     │                      │               │                     │
     │                  DENY│               │                     └─► failed
     │                      ▼               │
     └──► cancelled     denied          ambiguous ──reconcile──► succeeded|failed
          (revalidation                 (timeout/5xx)
           failed)
```

The `ambiguous` state is essential and frequently omitted. A timeout on a debit call means *the debit may have happened*. It must never be retried blindly; it must be reconciled against the provider using the idempotency key.

---

## 3. Database schema

```sql
-- ═══════════════ EVENT SPINE ═══════════════

CREATE TABLE events_raw (
    event_id        TEXT PRIMARY KEY,          -- provider event id; dedupe key
    provider        TEXT NOT NULL DEFAULT 'razorpay',
    event_type      TEXT NOT NULL,
    mandate_id      TEXT,                      -- nullable: some events precede correlation
    payload         JSONB NOT NULL,
    signature_ok    BOOLEAN NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX idx_events_mandate ON events_raw (mandate_id, received_at);
CREATE INDEX idx_events_unprocessed ON events_raw (received_at) WHERE processed_at IS NULL;

-- ═══════════════ DOMAIN STATE ═══════════════

CREATE TABLE mandates (
    mandate_id       TEXT PRIMARY KEY,
    merchant_id      TEXT NOT NULL,
    customer_id      TEXT NOT NULL,
    rail             TEXT NOT NULL CHECK (rail IN ('upi_autopay','card_emandate','enach')),
    issuer_code      TEXT,
    mcc              TEXT,
    max_amount_paise BIGINT NOT NULL,
    state            TEXT NOT NULL,
    consent_ref      TEXT NOT NULL,            -- DPDP consent artifact id
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    version          INT NOT NULL DEFAULT 0
);
CREATE INDEX idx_mandates_customer ON mandates (customer_id);
CREATE INDEX idx_mandates_issuer ON mandates (issuer_code, rail);

CREATE TABLE cycles (
    cycle_id         TEXT PRIMARY KEY,
    mandate_id       TEXT NOT NULL REFERENCES mandates(mandate_id),
    seq_no           INT NOT NULL,             -- billing cycle number
    amount_paise     BIGINT NOT NULL,
    due_at           TIMESTAMPTZ NOT NULL,
    deadline_at      TIMESTAMPTZ NOT NULL,     -- revocation / halt boundary
    attempt_budget   SMALLINT NOT NULL,        -- rail-specific: 4 for UPI Autopay
    attempts_used    SMALLINT NOT NULL DEFAULT 0,
    state            TEXT NOT NULL,
    pdn_sent_at      TIMESTAMPTZ,
    last_failure_at  TIMESTAMPTZ,              -- conditions the hazard curve
    recovered_paise  BIGINT DEFAULT 0,
    version          INT NOT NULL DEFAULT 0,
    UNIQUE (mandate_id, seq_no),
    CHECK (attempts_used <= attempt_budget)    -- last line of defence
);
CREATE INDEX idx_cycles_open ON cycles (state, deadline_at)
    WHERE state NOT IN ('succeeded','budget_exhausted','stopped_economic','stopped_hard','superseded');

CREATE TABLE attempts (
    attempt_id       TEXT PRIMARY KEY,
    cycle_id         TEXT NOT NULL REFERENCES cycles(cycle_id),
    attempt_seq      SMALLINT NOT NULL,
    idem_key         TEXT NOT NULL UNIQUE,     -- ◄ structural double-debit prevention
    scheduled_for    TIMESTAMPTZ NOT NULL,
    fired_at         TIMESTAMPTZ,
    state            TEXT NOT NULL,
    decline_code     TEXT,
    provider_ref     TEXT,
    decision_id      TEXT NOT NULL,
    UNIQUE (cycle_id, attempt_seq)
);

CREATE TABLE notifications (
    notification_id  TEXT PRIMARY KEY,
    cycle_id         TEXT NOT NULL REFERENCES cycles(cycle_id),
    kind             TEXT NOT NULL CHECK (kind IN ('pdn','nudge','post_debit')),
    channel          TEXT NOT NULL,
    dlt_template_id  TEXT,
    scheduled_for    TIMESTAMPTZ NOT NULL,
    sent_at          TIMESTAMPTZ,
    delivered_at     TIMESTAMPTZ,
    idem_key         TEXT NOT NULL UNIQUE,
    decision_id      TEXT NOT NULL
);

-- ═══════════════ COMPLIANCE ═══════════════

CREATE TABLE compliance_rules (
    rule_id      TEXT NOT NULL,
    version      INT NOT NULL,
    regulator    TEXT NOT NULL,
    citation     TEXT NOT NULL,
    as_of        DATE NOT NULL,                -- when this version took legal effect
    applies_to   TEXT[] NOT NULL,
    predicate    TEXT NOT NULL,                -- sandboxed expression
    on_fail      TEXT NOT NULL CHECK (on_fail IN ('DENY','DEFER','ESCALATE_HUMAN')),
    active       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   TEXT NOT NULL,
    PRIMARY KEY (rule_id, version)
);

-- ═══════════════ AUDIT LEDGER ═══════════════

CREATE TABLE decisions (
    decision_id       TEXT PRIMARY KEY,
    merchant_id       TEXT NOT NULL,           -- chain partition key
    chain_seq         BIGINT NOT NULL,         -- monotonic within merchant
    prev_hash         TEXT NOT NULL,
    record_hash       TEXT NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    trigger_event_id  TEXT,
    cycle_id          TEXT,
    action_type       TEXT NOT NULL,
    verdict           TEXT NOT NULL,           -- ALLOW / DENY / DEFER / ESCALATE
    feature_snapshot_ref TEXT,
    model_versions    JSONB NOT NULL,
    cause_posterior   JSONB,
    candidate_actions JSONB NOT NULL,          -- every branch with its EV
    chosen_action     JSONB,
    rationale         TEXT,
    compliance_checks JSONB NOT NULL,          -- [{rule_id, version, as_of, verdict}]
    holdout_arm       TEXT,
    propensity        NUMERIC(6,5),
    degraded          BOOLEAN NOT NULL DEFAULT false,
    outcome           TEXT,
    outcome_ts        TIMESTAMPTZ,
    recovered_paise   BIGINT,
    UNIQUE (merchant_id, chain_seq)
) PARTITION BY RANGE (ts);

REVOKE UPDATE, DELETE ON decisions FROM prayas_app;   -- append-only at grant level

-- ═══════════════ EXECUTION ═══════════════

CREATE TABLE scheduled_actions (
    action_id     TEXT PRIMARY KEY,
    cycle_id      TEXT NOT NULL,
    action_type   TEXT NOT NULL,
    fire_at       TIMESTAMPTZ NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    attempts      INT NOT NULL DEFAULT 0,
    locked_until  TIMESTAMPTZ,
    payload       JSONB NOT NULL
);
CREATE INDEX idx_sched_due ON scheduled_actions (fire_at)
    WHERE state = 'pending';

CREATE TABLE outbox (
    outbox_id     BIGSERIAL PRIMARY KEY,
    idem_key      TEXT NOT NULL UNIQUE,
    target        TEXT NOT NULL,               -- razorpay | sms | whatsapp
    request       JSONB NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    attempts      INT NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);
CREATE INDEX idx_outbox_pending ON outbox (created_at) WHERE state = 'pending';

-- ═══════════════ MEASUREMENT ═══════════════

CREATE TABLE issuer_health (
    issuer_code   TEXT NOT NULL,
    rail          TEXT NOT NULL,
    bucket_start  TIMESTAMPTZ NOT NULL,
    n_total       INT NOT NULL,
    n_success     INT NOT NULL,
    wilson_lower  NUMERIC(5,4) NOT NULL,
    cusum_stat    NUMERIC(8,4) NOT NULL,
    status        TEXT NOT NULL,               -- healthy | degraded | recovering
    recovery_eta  TIMESTAMPTZ,
    PRIMARY KEY (issuer_code, rail, bucket_start)
);

CREATE TABLE experiment_config (
    experiment_id TEXT PRIMARY KEY,
    seed          TEXT NOT NULL,
    control_pct   NUMERIC(4,3) NOT NULL,
    git_commit    TEXT NOT NULL,               -- ◄ pre-registration proof
    committed_at  TIMESTAMPTZ NOT NULL,
    frozen        BOOLEAN NOT NULL DEFAULT false
);
```

**Note the two structural safeguards.** The `CHECK (attempts_used <= attempt_budget)` constraint means exceeding the NPCI budget is a database error, not a logic bug. The `UNIQUE` on `attempts.idem_key` means a double debit cannot be written even if every layer above it is wrong.

---

## 4. Ingest

### 4.1 Signature verification

```python
def verify(raw_body: bytes, header_sig: str, secrets: list[str]) -> bool:
    """Verify against RAW bytes. Re-serialising the parsed JSON changes
    key order and whitespace and silently breaks the HMAC — a classic bug.
    Accept multiple secrets to survive rotation windows."""
    for secret in secrets:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, header_sig):   # constant-time
            return True
    return False
```

### 4.2 Ingest handler

Ack fast, process later. The handler does exactly three things:

```python
async def handle(raw: bytes, sig: str) -> Response:
    if not verify(raw, sig, SECRETS):
        return Response(401)                        # do NOT persist unverified
    evt = json.loads(raw)
    try:
        await db.execute(
            "INSERT INTO events_raw (event_id, event_type, mandate_id, payload, signature_ok)"
            " VALUES ($1,$2,$3,$4,true) ON CONFLICT (event_id) DO NOTHING",
            evt["id"], evt["event"], extract_mandate_id(evt), evt)
    except Exception:
        return Response(500)                        # provider will retry — safe
    await bus.publish(topic="events.raw", key=mandate_id, value=evt)
    return Response(200)                            # p99 < 100ms
```

`ON CONFLICT DO NOTHING` is the entire duplicate story. No dedupe cache, no TTL, no distributed lock.

### 4.3 Projection and out-of-order handling

```python
TERMINAL = {"succeeded", "budget_exhausted", "stopped_economic",
            "stopped_hard", "superseded"}

def project(evt, cycle):
    if cycle.state in TERMINAL and evt.type != "payment.captured":
        metrics.incr("stale_transition", type=evt.type)
        return                                       # absorbing; ignore
    if evt.type == "payment.captured" and cycle.state != "succeeded":
        # Late capture: customer paid, possibly after we saw payment.failed.
        # This is expected behaviour, not an anomaly.
        cycle.transition("succeeded", recovered=evt.amount)
        cancel_scheduled_actions(cycle.id)           # ◄ prevents double debit
        return
    ...
```

The `payment.failed → payment.captured` sequence for the *same* transaction is documented Razorpay behaviour (in-app UPI retry, late authorisation). Handling it in the projector *and* again at fire time is intentional redundancy on the highest-severity failure mode.

---

## 5. Rail adapters

Rails differ in budget, windows, and notice semantics. One interface, several implementations, so the sequencer stays rail-agnostic.

```python
class RailAdapter(Protocol):
    def attempt_budget(self) -> int: ...
    def legal_slots(self, horizon: TimeGrid) -> np.ndarray:  # bool mask
        ...
    def notice_lead_slots(self) -> int: ...
    def notice_cutoff(self, debit_at: datetime) -> datetime | None: ...
    def attempt_cost_paise(self, amount: int) -> int: ...

class UpiAutopayAdapter:
    def attempt_budget(self) -> int:
        return 4                                  # 1 execution + 3 retries (NPCI)

    def legal_slots(self, grid):
        h = grid.hour_ist
        return (h < 10) | ((h >= 13) & (h < 17)) | (h >= 21.5)

    def notice_lead_slots(self) -> int:
        return 24                                 # ≥24h PDN (RBI)

    def notice_cutoff(self, debit_at):
        """PDN requests at/after 23:50 for a T+1 debit are rejected.
        Not a soft guideline — a cliff. Encoded as a hard constraint."""
        if debit_at.date() == (now_ist().date() + timedelta(days=1)):
            return debit_at.replace(hour=23, minute=50) - timedelta(days=1)
        return None
```

---

## 6. Cause inference (②)

### 6.1 Deterministic layer

```python
HARD = {"41": "credential_dead",   # lost card
        "43": "credential_dead",   # stolen
        "14": "credential_dead",   # invalid number
        "54": "credential_dead",   # expired
        "MANDATE_REVOKED": "mandate_dead"}

def deterministic(code, ctx):
    if code in HARD:
        return {HARD[code]: 1.0}, "hard"          # → STOP, no model needed
    if code == "51":
        return {"no_funds": 0.95, "limit_breach": 0.05}, "soft"
    return None, "latent"                          # code 05 lands here
```

### 6.2 Latent-cause posterior for code 05

Code 05 is 30–40% of all declines and carries almost no information on its own. The signal is entirely in the context.

| Feature | Discriminates |
|---|---|
| `issuer_wilson_lower` at failure time | issuer_degraded vs everything else |
| `peer_success_rate` — other customers, same issuer, same 5-min window | Rules issuer-side in or out |
| `sibling_failure_rate` — this customer's *other* mandates, same window | Rules customer-side in |
| `amount / p75(customer successful debits)` | limit_breach, no_funds |
| `day_of_month`, `days_since_inferred_payday` | no_funds |
| `n_concurrent_mandates` on the account | no_funds (competition for the same rupees) |
| `prior_cycle_fail_success_lag` | no_funds vs fraud_hold |

**Training.** The eventual outcome is a noisy label for the latent cause:

```
retry cleared 2 days later, same amount, day-of-month 1   → no_funds
retry cleared 5 minutes later on a different route        → issuer_degraded
never cleared, mandate later revoked                      → mandate_dead
cleared only after customer re-authenticated              → fraud_hold
```

Fit with EM over the latent variable: E-step assigns posterior responsibilities from the current model; M-step refits a gradient-boosted multiclass classifier on responsibility-weighted rows. Initialise from the deterministic rules above.

**Validation.** The simulator (§16) emits ground-truth causes, so cause inference is reported as an actual confusion matrix — a claim that is impossible to make on real data, and therefore a strong and honest use of synthetic data.

---

## 7. Issuer nowcast (④)

### 7.1 Wilson lower bound

Raw success rate over-reacts to small samples: three failures at a low-volume bank should not declare an outage.

```python
def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.5                                # uninformative prior
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denom
    margin = (z / denom) * math.sqrt(p*(1-p)/n + z**2/(4*n**2))
    return max(0.0, centre - margin)
```

### 7.2 CUSUM change-point detection

Detects a *drop* in success rate faster than a threshold on a rolling mean, and — equally important — detects recovery.

```python
class CusumDetector:
    """One-sided CUSUM on the success indicator.
    mu0     : baseline success rate for this (issuer, rail, hour-of-week)
    k       : slack; half the shift size we want to detect
    h       : decision threshold; tuned for ~1 false alarm/month/issuer
    """
    def __init__(self, mu0, k=0.05, h=5.0):
        self.mu0, self.k, self.h = mu0, k, h
        self.s_down = 0.0
        self.healthy_streak = 0

    def update(self, success_rate: float) -> str:
        self.s_down = max(0.0, self.s_down + (self.mu0 - self.k - success_rate))
        if self.s_down > self.h:
            self.healthy_streak = 0
            return "degraded"
        if success_rate >= self.mu0 - self.k:
            self.healthy_streak += 1
            if self.healthy_streak >= 3:          # require sustained recovery
                self.s_down = 0.0
                return "healthy"
        return "recovering"
```

**Baseline `mu0` is hour-of-week specific**, not global. Banks have genuine diurnal and weekly patterns; a global baseline would flag every Sunday night as an outage.

### 7.3 Recovery ETA

Fit a log-normal to historical outage durations per issuer; the ETA is the median residual duration given elapsed time — a survival estimate, consistent with the rest of the system:

```
ETA(t) = median{ D | D > elapsed }   where  D ~ LogNormal(μ_issuer, σ_issuer)
```

This feeds the sequencer's key deferral judgement: *don't burn an attempt now, this bank returns in ~40 minutes and the next legal window opens at 13:00 anyway.*

---

## 8. Liquidity hazard model (③)

### 8.1 Formulation

Discrete-time hazard over hourly slots:

```
h(t)  = P(account funded ≥ A at slot t | not funded before t)
S(t)  = ∏_{u ≤ t} (1 − h(u))          survival: still unfunded at t
```

The sequencer needs the **conditional** success probability given an observed failure at `t_last`. Under an absorbing-funding assumption:

```
p(t | t_last) = 1 − S(t)/S(t_last)
```

### 8.2 Training table

One row per `(cycle, candidate slot)` up to the first observed funding event, which is exactly how censoring is handled — slots after an unobserved outcome simply don't appear.

| Column | Type | Notes |
|---|---|---|
| `cycle_id`, `slot_idx` | key | |
| `dom_hist_density` | float | Density of this customer's historical *successful* debits on this day-of-month |
| `days_since_payday` | int | From inferred funding cycle |
| `amount_ratio` | float | `amount / p75(customer successful debits)` |
| `hour_success_rate` | float | Customer's historical success by hour-of-day |
| `n_concurrent_mandates` | int | Competition for the same balance |
| `prior_lag_median` | float | Median fail→success lag, prior cycles |
| `declared_funding_day` | int? | **From LLM-parsed inbound reply (§14)** |
| `segment_prior_hazard` | float | Hierarchical shrinkage target |
| `label_funded` | bool | Target |

### 8.3 Models and cold start

**V0 — per-segment empirical hazard.** A lookup table keyed by `(mcc, ticket_band, day_of_month, hour_band)`. Ships in a day and already beats a fixed calendar, because the calendar encodes no payday information at all. This is the fallback in the degradation ladder.

**V1 — gradient-boosted discrete-time hazard.** Binary objective on the row schema above, with `slot_idx` and its interactions as features.

**Hierarchical partial pooling for cold start:**

```
ĥ_customer = w · ĥ_observed + (1 − w) · ĥ_segment,
w = n_obs / (n_obs + κ)          κ ≈ 5 cycles, tuned on held-out data
```

shrinking toward `merchant × mcc × ticket_band`, then toward global.

### 8.4 Calibration — the property that actually matters

The DP consumes these probabilities as **expected rupees**. A model with excellent AUC and poor calibration does not merely rank badly; it computes the wrong money and therefore stops at the wrong time.

```python
def ece(y_true, y_prob, n_bins=20) -> float:
    """Expected calibration error. Alert above 0.05."""
    bins = np.digitize(y_prob, np.linspace(0, 1, n_bins + 1)) - 1
    total = 0.0
    for b in range(n_bins):
        m = bins == b
        if m.sum() == 0:
            continue
        total += (m.sum() / len(y_prob)) * abs(y_true[m].mean() - y_prob[m].mean())
    return total
```

Isotonic regression on a held-out fold, refit weekly. Reliability curves are a production dashboard, not a training artefact.

### 8.5 The absorbing assumption, stated honestly

Funding is not strictly absorbing — money arrives and is spent again. Mitigations: (a) a leak parameter `λ_leak` decaying `S(t)` recovery over long gaps; (b) a 30-day horizon cap, beyond which the assumption clearly fails; (c) validation against a simulator configured with explicitly non-absorbing dynamics. Naming this limitation before a judge does is worth more than hiding it.

---

## 9. The sequencer (⑤)

### 9.1 Problem statement

Choose attempt times `t₁ < … < t_k`, `k ≤ B`, maximising

```
E[net] = A · P(∪ᵢ success at tᵢ) − Σᵢ cost(tᵢ) − λ·annoyance − μ·P(revoked)
```

subject to: legal execution windows, `tᵢ₊₁ ≥ tᵢ + L` (notice lead time), PDN cutoff feasibility, and `t_k ≤ deadline`.

### 9.2 State and recursion

The state must include `t_last`, because the last observed failure conditions the hazard. Missing this is the most likely modelling error in a competing implementation.

```
V(b, t) = max ⎧ 0                                          ← STOP
              ⎨ max        p(t'|t)·A
                t' legal   + (1−p(t'|t))·V(b−1, t')
                t' ≥ t+L   − cost(t')
              ⎩
```

### 9.3 Implementation

```python
def solve(S, legal, cost, A, B, L, health, p_recoverable, H):
    """
    S[t]      : survival, P(still unfunded at slot t), monotone non-increasing
    legal[t]  : bool mask of legal execution slots (rail adapter)
    cost[t]   : paise cost of firing at t
    A         : amount at stake, paise
    B         : attempts remaining
    L         : notice lead time, slots
    health[t] : forecast issuer health multiplier ∈ (0,1]
    p_recoverable : P(cause ∈ recoverable) from the cause posterior
    Returns (V, policy) where policy[b][t] = best next slot, or -1 = STOP.
    """
    V      = np.zeros((B + 1, H))
    policy = np.full((B + 1, H), -1, dtype=int)

    for b in range(1, B + 1):
        for t in range(H - 1, -1, -1):
            if S[t] <= 1e-9:
                continue                                  # already funded
            lo = t + L
            if lo >= H:
                continue
            tp = np.arange(lo, H)
            mask = legal[lo:H]
            if not mask.any():
                continue
            tp = tp[mask]

            # conditional success probability given failure at t
            p = (1.0 - S[tp] / S[t]) * health[tp] * p_recoverable
            ev = p * A + (1.0 - p) * V[b - 1][tp] - cost[tp]

            j = int(np.argmax(ev))
            if ev[j] > 0.0:                               # else STOP dominates
                V[b][t]      = ev[j]
                policy[b][t] = int(tp[j])

    return V, policy
```

**Complexity** `O(B·H²)` = 4 × 720² ≈ 2.1 M operations; ~8 ms vectorised. Exact, not approximate — affordable precisely because the budget is small, which is the same constraint that makes the problem interesting.

### 9.4 Economic stopping

Stopping is not a rule. It is `policy[b][t] == -1`, which happens exactly when every legal continuation has `EV ≤ 0`:

```python
if policy[b][t] == -1:
    cycle.transition("stopped_economic")
    ledger.write(rationale=(
        f"stopped: best remaining EV ₹{best_ev/100:.2f} "
        f"< cost ₹{cost[best_t]/100:.2f}; "
        f"attempts remaining {b}, hazard trough already passed"))
```

Compare `if retries > 3: stop`. This version tells a merchant, an auditor, and a judge *why*, in rupees.

### 9.5 Cost model

```python
def attempt_cost(t, ctx) -> int:
    base       = ctx.rail.attempt_cost_paise(ctx.amount)     # fees
    fraud_risk = ctx.beta * ctx.attempts_used**2             # convex: escalates fast
    annoyance  = ctx.lam * proximity_penalty(t, ctx.last_contact_at)
    revocation = ctx.mu * p_revocation(ctx.attempts_used, ctx.n_messages)
    return int(base + fraud_risk + annoyance + revocation)
```

The revocation term encodes the guardrail directly into the objective: recovery that kills the mandate destroys lifetime value, so the optimiser must price it rather than have it caught after the fact by a metric. `λ`, `μ`, `β` are exposed as sliders in the policy simulator — surfacing the assumptions instead of burying one guess.

### 9.6 Hard overrides

Compliance and hard-decline stops sit *outside* the economics, never inside:

```python
HARD_STOPS = [
    lambda c: c.cause == "credential_dead",
    lambda c: c.cause == "mandate_dead",
    lambda c: c.mandate.state != "active",
    lambda c: c.customer_opted_out,
    lambda c: c.attempts_used >= c.attempt_budget,
    lambda c: now() > c.deadline_at,
]
```

An economic argument must never be able to override a legal one. Keeping these as a separate, earlier check makes that structurally true.

---

## 10. Notification optimizer (⑥)

The RBI-mandated pre-debit notification is legally guaranteed delivery, costs zero attempts, and lands *before* the money is lost. Optimising it is the shift-left lever.

```python
def plan_pdn(cycle, hazard, adapter):
    risk = 1.0 - hazard.p_funded_at(cycle.due_at)

    earliest = cycle.due_at - timedelta(hours=adapter.notice_lead_slots())
    cutoff   = adapter.notice_cutoff(cycle.due_at)          # 23:50 cliff
    latest   = min(earliest, cutoff) if cutoff else earliest

    # As late as legally permitted, aligned to attention, nudged toward
    # the evening before predicted funding. A notice 72h early is forgotten.
    send_at = align_to_attention(
        target=min(latest, hazard.eve_of_funding(cycle.due_at)),
        pattern=cycle.customer.engagement_hours)

    content = Template(
        merchant=cycle.merchant.legal_name,      # RBI-required fields
        amount=cycle.amount_paise,
        debit_date=cycle.due_at,
        opt_out_url=optout_link(cycle),
        pay_now_url=paylink(cycle) if risk > 0.4 else None,   # ◄ prevention lever
    )
    channel = pick_channel(cycle.customer, kind="transactional")   # 160-series
    return NotificationPlan(send_at, channel, content, risk_score=risk)
```

**Prevention accounting.** A cycle is counted `prevented` when it was flagged high-risk, received an optimised PDN, and then **succeeded on first execution with zero retries consumed**. This is reported separately from `recovered`, and compared against the holdout's baseline notification — otherwise it is just a success rate, not an effect.

---

## 11. Compliance gate (⑦)

### 11.1 Rule definition

```yaml
- rule_id: NPCI-AUTOPAY-WINDOW
  version: 3
  regulator: NPCI
  citation: "UPI Autopay non-peak execution windows"
  as_of: 2026-08-01
  applies_to: [debit_attempt]
  predicate: "hour_ist < 10 or (hour_ist >= 13 and hour_ist < 17) or hour_ist >= 21.5"
  on_fail: DENY

- rule_id: RBI-EMANDATE-PDN-24H
  version: 2
  regulator: RBI
  citation: "Digital Payments – E-mandate Framework, 2026"
  as_of: 2026-04-21
  applies_to: [debit_attempt]
  predicate: "pdn_sent_at != null and hours_since(pdn_sent_at) >= 24"
  on_fail: DENY

- rule_id: RBI-EMANDATE-AFA-CAP
  version: 2
  regulator: RBI
  citation: "Digital Payments – E-mandate Framework, 2026"
  as_of: 2026-04-21
  applies_to: [debit_attempt]
  predicate: "amount_paise <= afa_free_cap(mcc)"
  on_fail: ESCALATE_HUMAN

- rule_id: RBI-FPC-CONTACT-WINDOW
  version: 1
  regulator: RBI
  citation: "Fair Practices Code, circular 12 Aug 2022"
  as_of: 2022-08-12
  applies_to: [sms, whatsapp, voice]
  predicate: "hour_ist >= 8 and hour_ist < 19"
  on_fail: DEFER

- rule_id: TRAI-DLT-TEMPLATE
  version: 4
  regulator: TRAI
  citation: "TCCCPR 2018, as amended 2025"
  as_of: 2025-01-01
  applies_to: [sms]
  predicate: "dlt_template_id != null and header_series == '160' and not dnd_registered"
  on_fail: DENY

- rule_id: DPDP-CONSENT-VALID
  version: 1
  regulator: MeitY
  citation: "DPDP Act 2023"
  as_of: 2023-08-11
  applies_to: [sms, whatsapp, voice, debit_attempt]
  predicate: "consent_ref != null and not consent_withdrawn"
  on_fail: DENY
```

### 11.2 Evaluation engine

```python
VERDICT_PRECEDENCE = {"DENY": 3, "ESCALATE_HUMAN": 2, "DEFER": 1, "ALLOW": 0}

def evaluate(action, ctx, as_of: datetime) -> GateResult:
    rules = rule_store.active_for(action.type, as_of)   # latest version ≤ as_of
    checks, worst = [], "ALLOW"
    for r in rules:                                    # evaluate ALL — no short-circuit
        try:
            ok = safe_eval(r.predicate, ctx)
        except PredicateError:
            ok = False                                 # fail closed
            metrics.incr("predicate_error", rule_id=r.rule_id)
        verdict = "ALLOW" if ok else r.on_fail
        checks.append({"rule_id": r.rule_id, "version": r.version,
                       "as_of": r.as_of, "citation": r.citation,
                       "verdict": verdict})
        if VERDICT_PRECEDENCE[verdict] > VERDICT_PRECEDENCE[worst]:
            worst = verdict
    return GateResult(verdict=worst, checks=checks)
```

**No short-circuiting on the first DENY.** The audit record should show every rule that was evaluated and every one that failed, not just the first. A compliance reviewer asking "was the DND check performed?" needs a positive answer either way.

### 11.3 Sandboxed predicate evaluation

`eval()` on rule text stored in a database is remote code execution with extra steps.

```python
ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp,
                 ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Call,
                 ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt,
                 ast.LtE, ast.Gt, ast.GtE, ast.Add, ast.Sub, ast.Mult, ast.Div)

ALLOWED_FUNCS = {"hours_since": _hours_since,
                 "afa_free_cap": _afa_free_cap,      # 15000, or 100000 for
                 "in_window":    _in_window}          # insurance/MF/CC-bill MCCs

def safe_eval(expr: str, ctx: dict):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(f"disallowed node {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
                raise PredicateError("disallowed call")
    return eval(compile(tree, "<rule>", "eval"),
                {"__builtins__": {}}, {**ctx, **ALLOWED_FUNCS})
```

Whitelist by AST node type, empty builtins, whitelisted function table. Every new rule gets a unit test before activation.

### 11.4 Shadow mode

The same engine evaluates the naive day-1/3/5 baseline without firing anything, producing the comparison slide:

> *"The industry-standard policy would have committed 213 compliance violations across this batch — 147 retries outside NPCI execution windows, 51 debits without valid 24-hour notice, 15 messages to DND-registered numbers. Prayas committed zero."*

---

## 12. Executor (⑧)

### 12.1 Idempotency key

Deterministic, so a logical retry of the same action always produces the same key:

```python
def idem_key(cycle_id, attempt_seq, action_type, amount_paise) -> str:
    raw = f"{cycle_id}:{attempt_seq}:{action_type}:{amount_paise}"
    return "prayas_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
```

Amount is included so a mid-cycle amount change cannot silently reuse a key.

### 12.2 Fire-time revalidation

The most important twenty lines in the system.

```python
async def fire(action_id: str):
    async with db.transaction():
        cycle = await db.fetchrow(
            "SELECT * FROM cycles WHERE cycle_id = "
            "  (SELECT cycle_id FROM scheduled_actions WHERE action_id = $1) "
            "FOR UPDATE", action_id)

        # 1. Still owed?  Catches late capture and customer self-serve payment.
        if cycle["state"] in TERMINAL:
            return await cancel(action_id, "cycle_terminal")

        # 2. Mandate still alive?
        mandate = await db.fetchrow("SELECT state FROM mandates WHERE mandate_id=$1",
                                    cycle["mandate_id"])
        if mandate["state"] != "active":
            return await cancel(action_id, "mandate_inactive")

        # 3. Budget — atomic, not advisory.
        updated = await db.execute(
            "UPDATE cycles SET attempts_used = attempts_used + 1, version = version + 1 "
            "WHERE cycle_id = $1 AND attempts_used < attempt_budget",
            cycle["cycle_id"])
        if updated == "UPDATE 0":
            return await cancel(action_id, "budget_exhausted")

        # 4. Re-gate with as_of = NOW. Rules may have changed since scheduling,
        #    and a delayed fire may now be outside its execution window.
        gate = await gate_svc.evaluate(action, ctx_now(cycle), as_of=now())
        if gate.verdict != "ALLOW":
            await ledger.write(action, gate, verdict=gate.verdict)   # log denials
            return await defer_or_cancel(action_id, gate)

        # 5. Record intent + enqueue. Same transaction — this is the outbox pattern.
        key = idem_key(cycle["cycle_id"], cycle["attempts_used"] + 1, "debit",
                       cycle["amount_paise"])
        await db.execute("INSERT INTO attempts (...) VALUES (...) "
                         "ON CONFLICT (idem_key) DO NOTHING", ...)
        await db.execute("INSERT INTO outbox (idem_key, target, request) "
                         "VALUES ($1,'razorpay',$2)", key, request)
    # transaction commits before any external call is made
```

**Why the outbox.** Committing the intent before the external call means a crash between the two loses nothing: the relay picks up the pending outbox row on restart and calls with the same idempotency key. Calling first and writing after would risk a debit with no local record — the worst possible ordering.

### 12.3 Outbox relay and the ambiguous case

```python
async def relay(row):
    try:
        resp = await razorpay.charge(**row.request,
                                     idempotency_key=row.idem_key)
        await mark(row, "completed", provider_ref=resp.id)
    except (Timeout, ServerError):
        # The debit MAY have happened. Never blind-retry as a new charge —
        # retry with the SAME key, which the provider treats as the same request.
        if row.attempts < MAX_RELAY_ATTEMPTS:
            await backoff_retry(row)                 # same idem_key
        else:
            await mark(row, "ambiguous")
            await enqueue_reconciliation(row.idem_key)
```

The reconciliation job queries the provider by idempotency key and resolves the attempt to `succeeded` or `failed`. Until it resolves, the attempt occupies its budget slot — pessimistic, and correct.

### 12.4 Timer polling (hackathon variant)

```sql
UPDATE scheduled_actions
SET state = 'claimed', locked_until = now() + interval '60 seconds'
WHERE action_id IN (
    SELECT action_id FROM scheduled_actions
    WHERE state = 'pending' AND fire_at <= now()
    ORDER BY fire_at
    FOR UPDATE SKIP LOCKED
    LIMIT 100)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` gives safe multi-worker claiming without a distributed lock. `locked_until` reclaims work from a crashed worker.

---

## 13. Audit ledger (⑨)

### 13.1 Hash chain

```python
def canonical(record: dict) -> bytes:
    """Deterministic serialisation. Sorted keys, no whitespace, UTF-8.
    Any variance here breaks verification for reasons that are painful to debug."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")

async def append(record: dict, merchant_id: str) -> str:
    async with db.transaction():
        # Advisory lock scoped to merchant → parallel chains, no global serialisation
        await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", merchant_id)
        head = await db.fetchrow(
            "SELECT chain_seq, record_hash FROM decisions "
            "WHERE merchant_id = $1 ORDER BY chain_seq DESC LIMIT 1", merchant_id)
        prev_hash = head["record_hash"] if head else GENESIS
        seq       = (head["chain_seq"] + 1) if head else 0

        body = {**record, "merchant_id": merchant_id,
                "chain_seq": seq, "prev_hash": prev_hash}
        body["record_hash"] = hashlib.sha256(canonical(body)).hexdigest()
        await db.execute("INSERT INTO decisions (...) VALUES (...)", **body)
    return body["record_hash"]
```

**Per-merchant chains** avoid a global write bottleneck. A nightly job chains all merchant heads into a single global checkpoint, whose hash is exported to write-once external storage — which is what converts "we do not edit the ledger" from a policy into a verifiable claim.

### 13.2 Verifier

```python
async def verify(merchant_id, since_seq=0) -> list[Break]:
    breaks, prev = [], None
    async for r in db.cursor("SELECT * FROM decisions WHERE merchant_id=$1 "
                             "AND chain_seq >= $2 ORDER BY chain_seq", merchant_id, since_seq):
        if prev is not None and r["prev_hash"] != prev["record_hash"]:
            breaks.append(Break(r["chain_seq"], "prev_hash_mismatch"))
        if prev is not None and r["chain_seq"] != prev["chain_seq"] + 1:
            breaks.append(Break(r["chain_seq"], "sequence_gap"))
        body = {k: v for k, v in r.items() if k != "record_hash"}
        if hashlib.sha256(canonical(body)).hexdigest() != r["record_hash"]:
            breaks.append(Break(r["chain_seq"], "content_tampered"))
        prev = r
    return breaks
```

Runs continuously; any break pages immediately.

### 13.3 Replay

`GET /v1/decisions/{decision_id}/replay` reconstructs, from stored artefacts only:

1. Trigger event (raw payload from `events_raw`)
2. Feature snapshot (fetched from object store by reference — the exact values the model saw)
3. Cause posterior with model version
4. Hazard curve and issuer health at decision time
5. **Every candidate action with its expected value**, including the ones not chosen
6. Compliance checks with rule versions, `as_of` dates, and citations
7. Chosen action, propensity, holdout arm
8. Outcome and recovered amount
9. Chain position and hash verification status

A judge clicks any single recovered rupee and sees why it happened. This is the demo money-shot.

---

## 14. LLM integration

Four narrow roles. **None on the money decision path.**

### 14.1 Inbound reply parsing — the strongest use

```python
SYSTEM = """Extract payment-timing intent from an Indian customer's reply.
Reply may code-switch between Hindi and English. Return ONLY JSON:
{"funding_day": int|null,     // day of month money is expected
 "defer_until": "ISO date"|null,
 "intent": "will_pay"|"dispute"|"cannot_pay"|"opt_out"|"unclear",
 "confidence": "high"|"medium"|"low"}
No prose, no markdown fences."""

# "salary 5 tarikh ko aati hai, uske baad try karna"
#   → {"funding_day": 5, "intent": "will_pay", "confidence": "high"}
```

The extracted value enters the hazard model as `declared_funding_day` — a **strong prior**, not an override, since customers are optimistic. Implemented as a Beta prior on the day-of-month hazard with pseudo-count weight scaled by confidence.

This is the answer to "why is this an AI project": the customer states their payday in free-form code-switched Hindi-English, and that natural-language signal becomes a feature in a calibrated survival model. `intent == "opt_out"` writes to the suppression list, which the gate then enforces on every subsequent action.

### 14.2 Explanation (grounded)

```python
def explain(decision_id) -> str:
    record = ledger.get(decision_id)
    return llm.complete(
        system="Explain this payment-recovery decision to a merchant in plain "
               "English. Use ONLY facts present in the record. Never invent "
               "numbers, rules, or citations. If a field is absent, say so.",
        user=json.dumps(record))
```

Narrator, never decision-maker. Output is not persisted to the ledger — the ledger holds facts; prose is regenerated on demand.

### 14.3 Policy parsing → proposed rules

Merchant types *"never chase invoices under ₹500, escalate above ₹50k, no messages on Sundays"* → structured rule proposals, presented in a diff view for **human confirmation before activation**. An LLM-authored rule is never active without a person approving it, which is the mitigation for hallucinated compliance logic.

### 14.4 Message composition

Fills variable slots inside DLT-approved templates with register-matched Hinglish. The template body is fixed by law; the LLM may not alter it, and the gate verifies `dlt_template_id` independently of whatever the model produced.

---

## 15. Measurement (⑩)

### 15.1 Deterministic assignment

No assignment table, no leakage, reproducible from a committed seed:

```python
def arm(customer_id: str, seed: str, control_pct: float) -> str:
    h = hashlib.sha256(f"{seed}:{customer_id}".encode()).hexdigest()
    bucket = int(h[:8], 16) % 10_000
    return "control" if bucket < control_pct * 10_000 else "treatment"
```

**Customer-level, not cycle-level.** One customer may hold mandates with several merchants; randomising per cycle leaks treatment across arms within a customer. Residual cross-customer interference (shared issuer load) is documented rather than ignored.

**Pre-registration** is the `experiment_config` row: seed plus git commit hash, written and frozen before the run. The commit timestamp in git history is the proof.

### 15.2 Primary estimate

```python
def incremental(treat, ctrl):
    p1, n1 = treat.recovered / treat.n, treat.n
    p0, n0 = ctrl.recovered / ctrl.n, ctrl.n
    lift = p1 - p0
    se = math.sqrt(p1*(1-p1)/n1 + p0*(1-p0)/n0)
    return lift, (lift - 1.96*se, lift + 1.96*se)
```

### 15.3 CUPED

Pre-period recovery rate is strongly correlated with post-period outcome, so removing that variance is what makes the effect detectable at hackathon batch sizes.

```python
def cuped(y, x):
    theta = np.cov(y, x)[0, 1] / np.var(x)
    return y - theta * (x - x.mean())          # unbiased, lower variance
```

Typical variance reduction of 30–50% translates directly into a narrower confidence interval on the same N.

### 15.4 Off-policy evaluation

Valid only because propensities were logged at decision time (ADR-07).

```python
def ips(logs, target_policy):
    return np.mean([(1.0 if l.action == target_policy(l.state) else 0.0)
                    / l.propensity * l.reward for l in logs])

def doubly_robust(logs, target_policy, q_hat):
    out = []
    for l in logs:
        a_star = target_policy(l.state)
        direct = q_hat(l.state, a_star)
        corr = ((1.0 if l.action == a_star else 0.0) / l.propensity) \
               * (l.reward - q_hat(l.state, l.action))
        out.append(direct + corr)
    return np.mean(out)
```

Propensities are clipped at 0.01 to bound variance; the clipping rate is reported, because an unreported clip is a silent bias.

### 15.5 Reported metrics

| Metric | Definition |
|---|---|
| Incremental recovery rate | `p_treat − p_ctrl`, with 95% CI, CUPED-adjusted |
| Incremental ₹ | `Σ recovered_treat − (n_treat/n_ctrl)·Σ recovered_ctrl` |
| Attempts per recovery | `Σ attempts / Σ recoveries`, per arm |
| Prevention rate | High-risk cycles succeeding on first execution after optimised PDN |
| Mandate revocation rate | **Guardrail.** Must not exceed control |
| Compliance violations | Must be 0; shadow baseline reported alongside |
| Cost per ₹ recovered | Fees + messaging + fines / recovered |
| Net revenue | Recovered − costs − churn-induced LTV loss |
| SRM check | χ² on arm sizes; a failure invalidates the experiment |

---

## 16. Simulator

Ground truth is the entire point — it is what lets cause inference be validated with a real confusion matrix.

```python
@dataclass
class SimConfig:
    payday_mix:      dict   # {"salaried_1st": .45, "salaried_7th": .２0,
                            #  "gig_irregular": .25, "chronically_dry": .10}
    outage_lambda:   float  # Poisson onset per issuer-day
    outage_duration: tuple  # LogNormal(mu, sigma) minutes
    mask_05_rate:    float  # P(surface as "05" | true cause = no_funds) ≈ 0.5
    revocation_beta: float  # revocation hazard per consecutive failure
    fatigue_decay:   float  # nudge efficacy decay per message
```

Each generated cycle emits `true_cause`, `true_funding_time`, and `issuer_state` alongside the observable event stream. Models see only the observables.

**Robustness suite — the move that pre-empts the sharpest critique.** Perturb parameters the model never trained on and re-run:

| Perturbation | Tests |
|---|---|
| `payday_mix` → 70% gig-irregular | Does the hazard model degrade gracefully off-distribution? |
| `outage_lambda` × 3 | Does the nowcast prevent wasted attempts under stress? |
| `mask_05_rate` → 0.8 | Does cause inference survive a noisier signal? |
| `revocation_beta` × 2 | Does the cost model correctly become more conservative? |

*"Under a payday-distribution shift the model never saw, Prayas still beats the calendar baseline by X pp"* answers **"you built a simulator and then beat your own simulator"** before it is asked.

---

## 17. API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/webhooks/razorpay` | Ingest. Signature-verified, deduped, 200 in <100 ms |
| `GET` | `/v1/cycles/{cycle_id}` | Current state, attempts used, next scheduled action |
| `GET` | `/v1/decisions/{decision_id}` | Raw ledger record |
| `GET` | `/v1/decisions/{decision_id}/replay` | Full reconstruction (§13.3) |
| `GET` | `/v1/decisions/{decision_id}/explain` | LLM narration, grounded |
| `POST` | `/v1/policy/simulate` | Re-run DP with modified `λ`, `μ`, cost — returns policy diff |
| `GET` | `/v1/batches/{batch_id}/results` | Holdout comparison, CIs, guardrails |
| `GET` | `/v1/issuers/health` | Current nowcast |
| `POST` | `/v1/rules/propose` | LLM or human rule proposal → pending review |
| `POST` | `/v1/rules/{rule_id}/activate` | Human confirmation; writes new version |
| `GET` | `/v1/ledger/verify` | Chain integrity report |
| `POST` | `/v1/killswitch` | Global / per-merchant / per-action-type halt |

**Policy simulator response** (drives the third console screen):

```json
{
  "baseline": {"lambda": 1.0, "attempts": 2.3, "recovery": 0.61, "net_paise": 118400},
  "modified": {"lambda": 3.0, "attempts": 1.4, "recovery": 0.54, "net_paise": 121900},
  "diff": {"n_cycles_changed": 1842,
           "example": {"cycle_id": "cyc_...",
                       "before": "retry@day+2 14:00, retry@day+5 09:00",
                       "after":  "retry@day+5 09:00 only (attempt 2 EV ₹4.20 < cost ₹6.50)"}}
}
```

Because the DP is ~8 ms, this recomputes across thousands of cycles interactively — which is what makes the economics tangible to a judge in a way no chart does.

---

## 18. Concurrency and race conditions

| # | Race | Scenario | Mitigation |
|---|---|---|---|
| 1 | Duplicate webhook | At-least-once delivery | `UNIQUE (event_id)` + `ON CONFLICT DO NOTHING` |
| 2 | Out-of-order webhook | `captured` before `failed` | Terminal states absorbing; version guards; `stale_transition` metric |
| 3 | **Double budget decrement** | Two workers claim the same action | `UPDATE ... WHERE attempts_used < budget`, rowcount checked; `CHECK` constraint as backstop |
| 4 | **Fire vs late capture** | Retry fires as customer pays | `SELECT FOR UPDATE` + revalidation inside the transaction; projector also cancels scheduled actions on capture |
| 5 | Rule change mid-flight | YAML deployed between schedule and fire | Gate re-evaluated at fire time with `as_of=now()`; both versions land in the ledger |
| 6 | Ambiguous provider response | Timeout on charge | `ambiguous` state; retry with same idem key; reconciliation by key; budget held pessimistically |
| 7 | Chain fork | Two ledger writers, same merchant | `pg_advisory_xact_lock(merchant_id)` |
| 8 | Clock skew | Scheduler fires outside window | Fire-time gate check is authoritative; NTP; monotonic clock for intervals |
| 9 | Timer worker crash | Action claimed, never fired | `locked_until` expiry reclaims; idem key makes re-fire safe |
| 10 | Model deploy mid-decision | Version changes between hazard and DP | Model versions pinned per decision and recorded in the ledger |
| 11 | Concurrent cycle creation | Duplicate billing events | `UNIQUE (mandate_id, seq_no)` |
| 12 | Outbox relay double-send | Relay retried after partial success | `UNIQUE (idem_key)` + provider-side idempotency key |

Rows 3, 4, and 6 are the money-safety triad — each is independently sufficient to prevent a double debit, and all three must fail simultaneously to produce one.

---

## 19. Test plan

**Unit**
- Every compliance predicate: boundary cases at 09:59:59 / 10:00:00 IST, 23:49 / 23:50 PDN cutoff, ₹14,999 / ₹15,000 / ₹15,001 AFA cap, ₹99,999 / ₹1,00,000 for exempt MCCs
- DP correctness against brute-force enumeration for `B ≤ 3`, `H ≤ 40`
- Wilson bound against a reference implementation
- Hash chain: tamper detection on every field
- `safe_eval`: rejects `__import__`, attribute access, comprehensions, lambdas

**Property-based (Hypothesis)**
- `attempts_used ≤ attempt_budget` holds under arbitrary interleavings
- Ledger chain verifies after any sequence of appends
- DP value is monotone non-decreasing in `B` and in `A`
- Any event permutation converges to the same projected state

**Integration**
- Razorpay test mode: full mandate → PDN → debit → failure → decision → retry → success
- Duplicate webhook storm (same event ×100) → exactly one state transition
- `payment.failed` then `payment.captured` → zero retries fired
- Gate outage → all actions deny, none fire

**Chaos**
- Kill the worker mid-transaction → no orphaned debits, no lost timers
- Kill after outbox insert, before provider call → relay recovers with the same key
- Inject provider timeouts at 10% → zero double debits, all reconciled
- Partition PG replica → failover, outbox replays safely

**Statistical**
- SRM check on arm assignment across 100 seeds
- A/A test: incremental lift CI must include zero
- Calibration: ECE < 0.05 on held-out simulator data
- Robustness suite (§16): does lift survive every perturbation?

**The single highest-value test:** inject the `failed → captured` sequence concurrently with a scheduled retry firing, a hundred times, and assert zero double debits. That is the catastrophic failure mode; it deserves a dedicated adversarial test rather than a line in a checklist.

---

## 20. Configuration

```yaml
sequencer:
  horizon_hours: 720
  slot_minutes: 60
  lambda_annoyance: 1.0        # ← policy simulator slider
  mu_revocation: 2.5           # ← policy simulator slider
  beta_fraud: 0.4
  min_ev_paise: 0              # stop threshold; 0 = pure EV

hazard:
  model: v1_gbm                # v0_lookup | v1_gbm
  shrinkage_kappa: 5
  calibration: isotonic
  ece_alert_threshold: 0.05
  leak_lambda: 0.02

nowcast:
  window_minutes: 5
  wilson_z: 1.96
  cusum_k: 0.05
  cusum_h: 5.0
  min_volume: 30

gate:
  fail_mode: closed            # never change this
  cache_ttl_seconds: 60

executor:
  poll_interval_seconds: 10
  lock_seconds: 60
  max_relay_attempts: 5
  jitter_seconds: 600          # spread load within execution windows

experiment:
  seed: "prayas-2026-08-22"    # committed to git before the run
  control_pct: 0.15
  frozen: true
```

---

*LLD for PRAYAS, Razorpay AI Buildathon Track 03. Every regulatory threshold appears in `compliance_rules` with an `as_of` date and citation, and must be verified at build time.*
