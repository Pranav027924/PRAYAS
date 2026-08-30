# Runbooks

Master Spec §45. Each is symptom → diagnosis → action → verification.

§45: "The emergency-stop runbook should be executable in under sixty seconds by
one person, and should be drilled quarterly rather than written and filed."

The drill is the part that decays. A runbook nobody has executed is a document,
not a control, and the emergency stop is the one where that distinction costs
the most. `tests/chaos/test_killswitch.py` executes it on every CI run and
asserts the sixty-second budget, so the timing claim in `emergency-stop.md` is
measured rather than asserted.

| Runbook | Trigger | Drilled by |
|---|---|---|
| [emergency-stop](emergency-stop.md) | Compliance or legal instruction | `test_the_emergency_stop_completes_inside_its_budget` |
| [suspected-double-debit](suspected-double-debit.md) | Duplicate `provider_ref` or customer report | `test_ten_percent_timeouts_produce_zero_double_debits` |
| [ledger-chain-break](ledger-chain-break.md) | Verifier alert | `tests/integration/test_ledger_chain.py` |
| [gate-misconfiguration](gate-misconfiguration.md) | Deny-rate anomaly | `tests/integration/test_gate.py` |
| [model-calibration-breach](model-calibration-breach.md) | ECE alert | `tests/unit/test_models_v1.py` |
| [provider-outage](provider-outage.md) | Elevated ambiguous responses | `tests/chaos/test_chaos_scenarios.py` |
| [consumer-lag-spike](consumer-lag-spike.md) | Lag alert | `tests/chaos/test_load.py` |
| [consent-withdrawal-at-scale](consent-withdrawal-at-scale.md) | Bulk DPDP request | `tests/isolation/test_forgetting.py` |
| [rule-change-deployment](rule-change-deployment.md) | Regulatory update | `tests/integration/test_gate_rails.py` |
