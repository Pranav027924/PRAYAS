"""A typed `solve()` invocation, shared by the sequencer tests.

Defined once because CI type-checks `tests/` under `--strict`. The earlier
`dict[str, object]` version required a `type: ignore` on nearly every use, and
those ignores were suppressing genuine errors alongside the noise — which is
how five CI runs stayed red while the local `mypy prayas` looked clean.
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]


class Scenario(TypedDict):
    """Exactly the keyword arguments `prayas.sequencer.dp.solve` accepts."""

    survival: FloatArray
    legal: BoolArray
    cost: IntArray
    amount_paise: int
    continuation_value_paise: int
    dr: FloatArray
    health: FloatArray
    budget: int
    lead_slots: int
    p_recoverable: float
