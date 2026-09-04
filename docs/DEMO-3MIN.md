# PRAYAS — the three-minute walkthrough

Four screens: login, console, portfolio, ledger. Figures read off the running
stack on 2026-09-04. Re-verify after any reseed:

```bash
uv run python scripts/verify_demo_numbers.py
```

**Open these four tabs before you start**, in this order, so you never type a
URL in the room:

1. `http://localhost:8010/console/login`
2. `http://localhost:8010/console/`
3. `http://localhost:8010/console/portfolio?tenant=fitfirst&window=90d`
4. `http://localhost:8010/console/ledger`

> **The `&window=90d` matters.** The page defaults to 30 days, and at 30 days
> the survival interval spans zero. The line you are about to say — *"the
> interval excludes zero"* — is only true on the 90-day view.

Timings are cumulative. `[PAUSE — n]` means stop talking for n seconds and let
them look. The pauses are not padding; they are where the reviewer reads the
number and forms the question you want them to ask.

---

## 1 · Login — 0:00 to 0:20

*On screen: the login page.*

> This is PRAYAS. It does one thing: when a recurring payment fails, it works
> out when to try again.

**[PAUSE — 2]** *(let the tagline land)*

> The line on the left is the whole thesis. A failed payment isn't a failed
> customer — about a fifth to two-fifths of Indian subscription churn is
> involuntary. The money was going to be paid. The debit just landed on the
> wrong hour.

*Paste the token, hit Enter console.*

---

## 2 · Console — 0:20 to 0:55

*On screen: the operator view.*

> This is a real pipeline, not a mock. Six thousand one hundred and twenty
> mandates. Twenty-seven thousand events ingested through the signed webhook
> path. Pipeline drained.

**[PAUSE — 3]** *(point at the Decisions cell)*

> Two thousand six hundred and nineteen decisions. Two thousand four hundred
> and twenty-four allowed — and **a hundred and ninety-five refused**. Hold on
> to that second number; I'll come back to it.

*Point at the adoption ladder.*

> Observe, shadow, canary, ramp, full. This merchant is at full — the engine
> fires on the whole portfolio, and it still holds fifteen percent back as a
> permanent control arm. Not a pilot. Permanent.

**[PAUSE — 2]**

---

## 3 · Portfolio — 0:55 to 2:00

*Switch to the portfolio tab. This is the screen that matters.*

> One card, two numbers, and they are never shown apart.

**[PAUSE — 3]** *(let them read both halves)*

> Twenty-five point seven lakh in incremental recovery. Ninety-five percent
> confidence interval, twenty-four point three to twenty-seven point two — it
> excludes zero.

**[PAUSE — 2]**

> Next to it, survival. Plus two point eight points, and that interval
> excludes zero too.

> These live in one card on purpose. Recovery without survival is how a dunning
> system destroys value while looking like it works — you can always collect
> more by hammering people, right up until they cancel. If we ever show you a
> recovery number, survival is beside it.

**[PAUSE — 3]**

*Point at the holdout chip.*

> Nine hundred and twenty-two mandates we deliberately never touch. That
> holdout is the only reason the number to its left means anything.

*Point at the efficiency strip.*

> One point two eight attempts per recovery, against five point seven five in
> the control arm. That's the actual product: the regulator gives you one
> execution and three retries, so this is a budget allocation problem, not a
> scheduling one.

**[PAUSE — 2]**

*Point at the two greyed metrics.*

> And two of these say **not measured**, because this build can't measure them
> honestly yet. They're not zeros.

---

## 4 · Ledger — 2:00 to 2:45

*Switch to the ledger tab.*

> Every decision the engine has ever made, append-only and hash-chained.
> Two thousand six hundred and nineteen rows, and that badge is a real walk of
> the chain on every page load — tamper with a record and it turns red.

**[PAUSE — 2]**

*Click the **Refused** filter.*

> Here are those hundred and ninety-five refusals. This is the part I'd look at
> if I were you. A demo that only shows success proves nothing.

**[PAUSE — 3]**

*Open the top row.*

> Every decision replays. Four rules, each with its regulator, its citation,
> and an as-of date. And the timestamp is the **fire** time, not the schedule
> time — rules and state both move between deciding and doing, so we re-check
> at the moment of firing.

**[PAUSE — 3]** *(this is the compliance reviewer's whole evaluation — let it sit)*

---

## 5 · The ask — 2:45 to 3:00

*Stop sharing, or go back to the portfolio.*

> Everything you've seen runs today. What it hasn't had is your data. The
> smallest useful step is Stage 0 — observe only, ingest webhooks, fire
> nothing, one merchant, two weeks. At the end you get a compliance audit on
> real cycles and a projection with a confidence interval.

> If the number isn't there, you've spent two weeks of webhook traffic.

**[STOP TALKING]**

---

## The two numbers that look like they disagree

The console says **₹2.59 crore recovered**. The portfolio says **₹25.7 lakh**.
Both are correct and they measure different things:

- **Console** — gross. Every rupee collected on cycles the engine touched.
- **Portfolio** — incremental. What the treated arm recovered *over what the
  same population would have recovered untreated*, measured against the
  holdout.

If someone catches this, that answer is the strongest thing you can say all
meeting. Do not blur them.

## If you're asked, in one line each

| Question | Answer |
|---|---|
| Doesn't this compete with Optimizer? | Different axis — Optimizer routes live traffic for success rate; this allocates a fixed retry budget in time on already-failed debits. |
| What if the model is wrong? | It degrades to a segment lookup, then a baseline calendar, and every rung still passes the gate and still writes to the ledger. |
| Could it double-charge? | Atomic budget decrement with the rowcount checked, idempotency key on the attempt, intent durable before any provider call. |
| Is the recovery real? | The holdout, and it's never retired. Assignment frozen against a seed committed before the run. |
| Integration cost? | Zero. Webhooks that already exist, APIs that already exist. |
| Production ready? | Engine paths are. Two things outstanding, both needing someone outside the code: a DLT-registered template, and a live authorised mandate. |

## Cut, in this order, if you run long

1. The adoption ladder on the console (say "full rollout, permanent holdout" instead)
2. The efficiency strip
3. The console screen entirely — go login → portfolio → ledger

**Never cut** the matched pair or the refusals. They are the two things that
make the rest credible.
