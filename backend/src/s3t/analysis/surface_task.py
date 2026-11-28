"""The surface fit as a task of the process pool (PRF-05).

A worker process imports this module and the pricing core, and nothing else of the backend.
Everything of a fit runs here: the parity forwards, the IV of the mids, SVI, SSVI and the checks
(``s3t.pricing.surface.fit_surface``). The times are taken in the worker, so that the cost of a
fit is known apart from the wait for a worker and the way through the process boundary (QA-08).
"""

import time
from collections.abc import Sequence
from typing import NamedTuple

from s3t.pricing.surface import Settings, SliceInput, Surface, Warm, fit_surface


class Fitted(NamedTuple):
    surface: Surface
    wall_s: float
    """Seconds the fit took in the worker."""
    cpu_s: float
    """CPU seconds of the worker process for it."""


def fit(inputs: Sequence[SliceInput], warm: Warm | None, settings: Settings | None) -> Fitted:
    started, used = time.perf_counter(), time.process_time()
    surface = fit_surface(inputs, warm, settings)
    return Fitted(surface, time.perf_counter() - started, time.process_time() - used)
