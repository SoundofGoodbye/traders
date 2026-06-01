"""Trial-aware deflation of the per-trade Sharpe proxy (PSR / DSR).

The slice-16 Optimizer proposes one parameter change at a time and the slice-24
gate backtests each proposal. But every proposal is a *trial*: try enough of
them and one will look good on noise alone. Bailey & López de Prado's
Probabilistic and Deflated Sharpe Ratios correct for exactly this — they ask
"given how many configurations were tried, and how short the track record is,
how confident are we the Sharpe is really positive?"

Leaf module: pure stdlib (``math`` + ``statistics.NormalDist`` for the normal
CDF / inverse-CDF), no imports from the rest of the package. It operates on the
same per-trade PnL list the rest of the system scores, and the Sharpe it uses is
the same ``mean / pstdev`` proxy as ``metrics.sharpe_per_trade`` — explicitly
**not** an annualized Sharpe. Treat the outputs as a relative, anti-overfitting
guardrail, not a calibrated probability.

References:

* Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (2012) — PSR.
* Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014) — DSR and the
  expected maximum Sharpe under the null across N trials.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

# Euler–Mascheroni constant, from the expected-maximum-of-N-Gaussians formula.
_EULER_MASCHERONI = 0.5772156649015329
_NORMAL = statistics.NormalDist()  # standard normal: cdf + inv_cdf, stdlib-only


def _standardized_moments(pnls: Sequence[float], mean: float, sd: float) -> tuple[float, float]:
    """Population skewness and (non-excess) kurtosis of ``pnls``."""
    n = len(pnls)
    skew = sum(((x - mean) / sd) ** 3 for x in pnls) / n
    kurtosis = sum(((x - mean) / sd) ** 4 for x in pnls) / n
    return skew, kurtosis


def sharpe_estimator_variance(sharpe: float, n: int) -> float:
    """Asymptotic sampling variance of the Sharpe estimator (Lo, 2002).

    ``Var(SR_hat) ≈ (1 + 0.5·SR²) / (n − 1)`` under i.i.d. normal returns. Used
    to scale the multiple-testing benchmark. Returns ``inf`` for ``n < 2`` — a
    Sharpe from fewer than two trades carries no information.
    """
    if n < 2:
        return float("inf")
    return (1.0 + 0.5 * sharpe * sharpe) / (n - 1)


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum of ``n_trials`` i.i.d. zero-mean Sharpe estimates.

    The benchmark a candidate must clear *because* it was selected as the best
    of N attempts. Bailey & López de Prado (2014)::

        E[max SR] ≈ sqrt(V) · [ (1−γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e)) ]

    where ``γ`` is the Euler–Mascheroni constant and ``Z⁻¹`` the inverse normal
    CDF. With a single trial there is no selection bias, so the benchmark is 0.
    """
    if n_trials <= 1:
        return 0.0
    sd = math.sqrt(max(0.0, sharpe_variance))
    z1 = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def probabilistic_sharpe_ratio(
    sharpe: float,
    n: int,
    benchmark: float = 0.0,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float | None:
    """P(true Sharpe > ``benchmark``) given the observed proxy and its moments.

    Bailey & López de Prado (2012), higher-moment aware: fat tails
    (``kurtosis`` > 3) and negative ``skew`` widen the estimator's error and
    pull the probability toward 0.5. ``kurtosis`` is non-excess (3 for a
    normal). Returns ``None`` when it is undefined — ``n < 2`` or a non-positive
    variance term.
    """
    if n < 2:
        return None
    denom = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    if denom <= 0.0:
        return None
    z = (sharpe - benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _NORMAL.cdf(z)


def deflated_sharpe_ratio(pnls: Sequence[float], n_trials: int) -> float | None:
    """Deflated Sharpe Ratio of a per-trade PnL series for ``n_trials`` trials.

    Combines the two corrections: the per-trade Sharpe proxy
    (``mean / pstdev``, matching ``metrics.sharpe_per_trade``) is tested via PSR
    against the ``expected_max_sharpe`` benchmark for ``n_trials``. More trials
    raise the benchmark, so the DSR falls — the anti-fishing property. Returns
    ``None`` when there are too few trades (< 2) or zero dispersion.
    """
    n = len(pnls)
    if n < 2:
        return None
    mean = statistics.fmean(pnls)
    sd = statistics.pstdev(pnls)
    if sd <= 0.0:
        return None
    sharpe = mean / sd
    skew, kurtosis = _standardized_moments(pnls, mean, sd)
    benchmark = expected_max_sharpe(n_trials, sharpe_estimator_variance(sharpe, n))
    return probabilistic_sharpe_ratio(sharpe, n, benchmark, skew=skew, kurtosis=kurtosis)
