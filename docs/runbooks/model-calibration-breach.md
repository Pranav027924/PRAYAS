# Model calibration breach

**Trigger.** Expected calibration error above 0.05 (Appendix C, §43).

## Why this matters more than accuracy

§21: "Calibration is the property that matters, not AUC. The sequencer consumes
these probabilities as expected rupees. A model that ranks well but is
miscalibrated does not merely order badly — it computes the wrong money and
stops at the wrong time."

A calibration breach is therefore a *money* problem, not a model-quality one.

## Diagnosis

```python
from prayas.inference.calibration import expected_calibration_error, reliability_curve
ece = expected_calibration_error(y_true, y_prob)
curve = reliability_curve(y_true, y_prob)
```

Read the reliability curve, not just the scalar. Systematic over-prediction in
the high-confidence bins is the expensive shape: it makes the sequencer spend
attempts it should not.

Check whether the input distribution moved before blaming the model:

```python
from prayas.models.drift import population_stability_index
psi = population_stability_index(reference, current)
```

## Action

1. **Refit the isotonic stage first** (§21: "isotonic recalibration weekly on a
   held-out fold"). It is cheap and is the intended remedy.
2. If PSI shows the population moved, the model is not broken — its inputs
   changed. Refit on recent data.
3. If neither helps, fall back to V0: `prayas.models.serving.fallback_provider`.
   The switch takes effect on the next decision, not the next deploy.

## Verification

- ECE back under 0.05 on a held-out fold.
- The reliability curve is monotone and near the diagonal in the populated bins.
- `test_a_deliberately_miscalibrated_model_exceeds_the_threshold` still passes,
  so the metric cannot pass vacuously.
