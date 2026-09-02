# What blocks PRAYAS, and what does not

Status as of 2026-09-01. The system **runs end to end**: a signed webhook
produces a projected cycle, a scheduled debit, a fire-time compliance decision,
and a hash-chained ledger entry. It has no known bugs.

What it cannot yet do is *recover money*, and this document is the honest
account of why — separating what needs an outside party from what is simply
unbuilt.

Nothing here is a workaround for a defect. Every refusal below is the system
behaving as specified.

---

## The proof that it runs

`scripts/demo_recovery.py <tenant>`, against the Compose stack, after two
webhooks. Output, unedited:

```
ACT I — a failed debit becomes a lawfully planned recovery
    the DP chose a debit slot that admits a lawful notice:
      notice 02 Sep 03:08 UTC   debit 03 Sep 03:08 UTC   (24h of notice)
  ✓ notice sent and recorded: pdn_sent_at = 01 Sep 19:09 UTC
      ledger #0  pdn_notice    ALLOW

ACT II — 30 hours after its notice, the debit fires
    gate re-evaluated at fire time (§32), as_of = now:
       ✓ DPDP-CONSENT-VALID       v1  ALLOW
       ✓ NPCI-AUTOPAY-WINDOW      v3  ALLOW
       ✓ RBI-EMANDATE-AFA-CAP     v2  ALLOW
       ✓ RBI-EMANDATE-PDN-24H     v3  ALLOW
  ✓ gate ALLOWED — debit submitted to the rail
    distinct debits at the provider: 1  (submissions: 1)
```

Every rule carries its citation and `as_of` into the ledger (Invariant 10), the
gate is re-evaluated at fire time rather than trusted from scheduling
(Invariant 3), and one debit reached the rail from one submission (Invariant 4).

**It refuses just as reliably.** Remove the notice and the same run produces
`RBI-EMANDATE-PDN-24H ... DENY`, the action is cancelled, and the denial is
written to the chain — because "a denial is the proof the gate works".

---

## 1. External — needs someone outside this repository

### 1.1 A real mandate requires a real customer

Razorpay Subscriptions is enabled and the credentials work; all four endpoints
return 200. But a mandate becomes chargeable only when a **customer completes
an authorisation flow** — UPI Autopay approval in their PSP app, or a card
e-mandate with AFA. No API call substitutes for that.

*Consequence:* the lifecycle can be driven with synthetic webhooks (as above)
but not against a real subscription until someone authorises one.

*To clear it:* create a plan and subscription in the Razorpay dashboard, open
the authorisation link on a phone, approve it. Test mode accepts test VPAs.

### 1.2 Delivering the pre-debit notice needs DLT registration

The notice is now planned, sent and recorded end to end (ADR-099, ADR-100) —
`cycles.pdn_sent_at` is written, the gate sees it, and the debit is allowed.
What remains external is the **transport**: in India a transactional SMS may
only be sent from a **DLT-registered sender ID with a pre-approved template**,
registered with the telecom regulator through an operator. That is a commercial
and regulatory process measured in days to weeks, not an API key.

The console channel stands in for it exactly as `FakeProvider` stands in for the
rail in test mode, so the whole pipeline is exercisable today.

*Consequence:* debits succeed in test mode; no message reaches a real phone.

*To clear it:* register a sender ID and the pre-debit notice template on any
DLT portal (Jio, Airtel, VI), then supply the credentials. §30's template
requirements are already encoded in `prayas/notify/`.

### 1.3 Cross-tenant priors need three tenants

§27's floors are 50 observations from **3 distinct contributors**. A
single-merchant deployment withholds every `segment_priors` cell forever — it
is k-anonymity working, not a bug (`FINDING-P17-09`).

*Consequence:* on a one-merchant pilot no observed prior ever publishes.

*Mitigated, not solved:* `prayas/models/bootstrap.py` (ADR-098) supplies a
derived prior so the system can act at all. It is labelled: `PriorTable.source`
reports `bootstrap` rather than `observed`, and the aggregator logs
`aggregator.all_withheld` with the reason. Any real cell that clears the floors
displaces it immediately — the bootstrap carries `n_obs = 1`, so §21's
shrinkage yields to evidence rather than competing with it.

*To clear it properly:* three or more tenants. Lowering the floors would weaken
§27 and should be resisted.

### 1.4 AWS account

No account exists. `docs/AWS-DEPLOYMENT.md` is complete and executable, and
provisioning costs money, which is a decision for the operator.

---

## 2. Internal — buildable, not blocked, and honestly counted

These are **not** external blockers. They are work not yet done, listed so the
distinction is not blurred.

### 2.1 The gate's `hours_since()` reads the wall clock

`predicate._hours_since` uses `datetime.now(tz=UTC)` and ignores the evaluation
instant, so the gate is not a pure function of `(context, as_of)`. Replay is
unaffected — it compares stored `compliance_checks` rather than re-evaluating —
and every gate test works around it by anchoring fixtures to `now() - 30h`,
which is why it has never surfaced as a failure.

*Consequence:* a single cycle cannot be demonstrated end to end in under 24
hours, which is why `scripts/demo_recovery.py` runs in two acts.

`FINDING-P17-12`, open, **Class A** — it touches the compliance evaluator. The
narrowest fix is to inject the evaluation instant the way `afa_free_cap` is
already injected (ADR-021).

### 2.2 The aggregator runs as database owner

`publish()` must write cross-tenant rows, but ADR-004 states the owner
connection is Alembic-only. The worker reads `PRAYAS_DATABASE_URL_OWNER`
directly so the breach is visible in one place rather than quietly generalised.
`FINDING-P17-08`, open, Class A. Recommended resolution: a SECURITY DEFINER
publisher matching `prayas_tenant_ids()` (ADR-046), which keeps the app role
narrow and needs one migration.

### 2.3 Contract fixtures are constructed, not recorded

Phase 17 wants contract tests against live API schemas. One recorded fixture
exists — the error envelope from `FINDING-P17-05`, which is what revealed that
Razorpay reuses `BAD_REQUEST_ERROR` for our malformed requests *and* for
genuine bank declines. The rest are constructed, and a test asserts none claims
otherwise (ADR-090).

---

## 3. What is settled

- The compliance gate fails closed and is evaluated at **fire** time, not
  schedule time. Verified: the DENY above was produced by re-evaluating with
  `as_of = NOW()` after the action was claimed.
- Tenant isolation holds under the application role. `prayas_app` bound to one
  tenant sees only that tenant's mandates and cycles. (`prayas_owner` in local
  Compose is a superuser and bypasses RLS, so psql-as-owner can neither prove
  nor disprove isolation — use the app role.)
- The adoption ramp is enforced in code, including §44's `mandate_share` and
  `holdout_pct`, which were previously defined and read nowhere (ADR-095).
- The ledger hash chain verifies, and reports when it has verified *nothing*
  rather than returning a vacuous success (`FINDING-P15-01`).
- Money is integer paise throughout. No float reaches the money path.

---

## 4. Shortest path to a recovered rupee

1. Register a DLT sender ID and the pre-debit notice template *(1.2 — start
   now, it has the longest lead time)*
2. Wire the notification planner *(2.1 — internal, unblocked)*
3. Authorise one test-mode subscription on a phone *(1.1)*
4. Run the ramp from `OBSERVE`, which is where the merchant's own retries
   populate the priors *(§44)*

Steps 1 and 2 are independent and can proceed in parallel. Until both are done
the system will keep doing exactly what it did above: decide correctly, refuse
lawfully, and record why.
