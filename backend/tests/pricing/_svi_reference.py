"""The penalty part of the SVI fit as `volcalc` had it before its fix F12, frozen: one parameter
vector per penalty evaluation, six evaluations per Jacobian. svi._penalty takes the six as one
stack; test_svi.py holds it to these functions' bits.

Source: `volcalc` tests/_svi_reference.py at commit f53be83, unchanged but for the import."""

import numpy as np

from s3t.pricing import svi


def penalty(x, tau, kg, scale):
    vg, vpg, vppg = svi.w_derivs(x, kg)
    gg = svi.g(kg, tau * vg, tau * vpg, tau * vppg)
    lee = max(tau * x[1] * (1 + abs(x[2])) - svi.LEE, 0.0)
    return scale * np.concatenate([np.minimum(gg, 0.0), np.minimum(vg, 0.0) / tau, [lee]])


def residuals(x, k, iv, sw, tau, kg, scale):
    data = sw * (np.sqrt(np.maximum(svi.w_derivs(x, k)[0], svi.W_MIN)) - iv)
    return np.concatenate([data, penalty(x, tau, kg, scale)])


def jac(x, k, iv, sw, tau, kg, scale):
    p0 = penalty(x, tau, kg, scale)
    cols = []
    for i in range(x.size):
        h = svi.FD_STEP * max(1.0, abs(x[i]))
        xi = x.copy()
        xi[i] += h
        cols.append((penalty(xi, tau, kg, scale) - p0) / h)
    return np.vstack([svi._jac_data(x, k, sw), np.column_stack(cols)])
