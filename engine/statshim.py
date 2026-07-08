"""statshim — 純 numpy/stdlib 取代 scipy.stats(只取本引擎用到的幾個)。

引擎要在瀏覽器 Pyodide 內跑,不能扛 scipy。這裡自備:
- norm_cdf : 標準常態 CDF,用 math.erf(stdlib) 逼近,精度 ~機器精度。
- norm_ppf : 標準常態逆 CDF(quantile),Acklam 的 rational 逼近 + 一步 Halley 修正,
             |誤差| < 1e-9(對照 scipy.stats.norm.ppf 已驗)。
- skew     : 樣本偏度(母體/有偏估計,mean-based 三階動差),對齊 scipy.stats.skew 預設(bias=True)。
- kurtosis : 樣本峰度,非超額(normal=3),對齊 scipy.stats.kurtosis(fisher=False, bias=True)。

全部純函數,無檔案/網路,可在 CPython(==Pyodide 純碼)直接 import。
自測見檔尾 `if __name__ == "__main__"`,對照已知值。
"""
import math

import numpy as np

_SQRT2 = math.sqrt(2.0)


# ---------------------------------------------------------------------------
# 常態 CDF / PPF
# ---------------------------------------------------------------------------
def norm_cdf(x):
    """標準常態 CDF Φ(x)。純量或 array 皆可。用 erf: Φ(x)=½(1+erf(x/√2))。"""
    arr = np.asarray(x, dtype=float)
    if arr.ndim == 0:
        return 0.5 * (1.0 + math.erf(float(arr) / _SQRT2))
    vec = np.vectorize(math.erf, otypes=[float])
    return 0.5 * (1.0 + vec(arr / _SQRT2))


# Acklam(2003)逆常態 CDF 的 rational 逼近係數
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def _ppf_scalar(p):
    if not (0.0 < p < 1.0):
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        return math.nan
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= _P_HIGH:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    # 一步 Halley 修正把精度拉到 ~1e-15
    e = norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


def norm_ppf(p):
    """標準常態逆 CDF Φ⁻¹(p)。純量或 array 皆可。|誤差|<1e-9 對照 scipy。"""
    arr = np.asarray(p, dtype=float)
    if arr.ndim == 0:
        return _ppf_scalar(float(arr))
    vec = np.vectorize(_ppf_scalar, otypes=[float])
    return vec(arr)


# ---------------------------------------------------------------------------
# 動差:偏度 / 峰度(對齊 scipy 預設 bias=True）
# ---------------------------------------------------------------------------
def skew(a):
    """樣本偏度(有偏,mean-based)。對齊 scipy.stats.skew(a, bias=True)。"""
    x = np.asarray(a, dtype=float).ravel()
    n = x.size
    if n < 1:
        return float("nan")
    m = x.mean()
    d = x - m
    m2 = np.mean(d ** 2)
    m3 = np.mean(d ** 3)
    if m2 == 0:
        return 0.0
    return float(m3 / m2 ** 1.5)


def kurtosis(a, fisher=False):
    """樣本峰度(有偏)。預設 fisher=False → 非超額,normal=3。
    對齊 scipy.stats.kurtosis(a, fisher=False, bias=True)。"""
    x = np.asarray(a, dtype=float).ravel()
    n = x.size
    if n < 1:
        return float("nan")
    m = x.mean()
    d = x - m
    m2 = np.mean(d ** 2)
    m4 = np.mean(d ** 4)
    if m2 == 0:
        return 0.0 if fisher else 3.0
    k = m4 / m2 ** 2
    return float(k - 3.0) if fisher else float(k)


if __name__ == "__main__":
    # 對照已知值(不需 scipy)
    assert abs(norm_cdf(0.0) - 0.5) < 1e-15, norm_cdf(0.0)
    assert abs(norm_ppf(0.975) - 1.959963984540054) < 1e-9, norm_ppf(0.975)
    assert abs(norm_ppf(0.5) - 0.0) < 1e-12, norm_ppf(0.5)
    assert abs(norm_cdf(1.959963984540054) - 0.975) < 1e-12
    # 對稱性
    assert abs(norm_ppf(0.025) + 1.959963984540054) < 1e-9
    # skew/kurtosis 對稱樣本
    sym = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    assert abs(skew(sym)) < 1e-12, skew(sym)
    print("statshim self-test OK",
          "norm_ppf(0.975)=%.9f" % norm_ppf(0.975),
          "norm_cdf(0)=%.1f" % norm_cdf(0.0))
