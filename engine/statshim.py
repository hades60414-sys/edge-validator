"""statshim — 純 numpy/stdlib 取代 scipy.stats(只取本引擎用到的幾個)。

引擎要在瀏覽器 Pyodide 內跑,不能扛 scipy。這裡自備:
- norm_cdf : 標準常態 CDF,用 math.erfc(stdlib),兩尾皆 ~機器精度(左尾為相對精度)。
- norm_ppf : 標準常態逆 CDF(quantile),Acklam 的 rational 逼近 + 一步 Halley 修正,
             |誤差| < 1e-9(對照 scipy.stats.norm.ppf 已驗,p 低至 1e-300;
             次正規數 p 不丟例外。見 engine/reference_check.py)。
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
    # ★reference-check 修正★:舊式 ½(1+erf(x/√2)) 在左尾有災難性抵銷——x=-8 相對誤差 2%,
    #   x≤-8.3 直接變 0(scipy: Φ(-10)=7.6e-24)。改用互補誤差函數 Φ(x)=½·erfc(-x/√2),
    #   左尾保有完整相對精度、右尾絕對誤差不變。
    if arr.ndim == 0:
        return 0.5 * math.erfc(-float(arr) / _SQRT2)
    vec = np.vectorize(math.erfc, otypes=[float])
    return 0.5 * vec(-arr / _SQRT2)


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
    # ★reference-check 修正★:上半部以對稱 Φ⁻¹(p)=-Φ⁻¹(1-p) 轉到下尾(p∈[½,1) 時 1-p 精確),
    #   只在下尾做 Halley 修正,讓修正步的 Φ(x) 有完整相對精度。
    if p > 0.5:
        return -_ppf_lower(1.0 - p)
    return _ppf_lower(p)


def _ppf_lower(p):
    """0 < p ≤ 0.5 的 Φ⁻¹(p)。"""
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
    # 一步 Halley 修正把精度拉到 ~1e-15。
    # ★reference-check 修正★:舊式 u=e·√(2π)·exp(x²/2) 在 p<~1e-308(x<-37.6)時 exp 溢位
    #   丟 OverflowError,且 e=Φ(x)-p 依賴左尾已抵銷的 Φ → p=1e-17 時誤差 0.08。
    #   改寫成等價的 u=(Φ(x)/p-1)·√(2π)·exp(ln p + x²/2):指數項有界、Φ(x)/p 相對精確。
    c = norm_cdf(x)
    u = (c / p - 1.0) * math.sqrt(2.0 * math.pi) * math.exp(math.log(p) + x * x / 2.0)
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
# 動差:偏度 / 峰度
#   ★reference-check 修正★:近常數序列(如 [0.01]*10)的 m2 不是 0 而是 ~1e-36 的浮點
#   捨入雜訊,舊版 m2==0 判斷漏接 → 偏度/峰度變成雜訊比值(任意大數)。改與 judge_web
#   同一退化門檻:std ≤ 1e-9·|mean| 視為無波動,回常態值(偏度 0、峰度 3)。
#   (scipy 此時回 NaN 並警告「catastrophic cancellation」——刻意分歧,已文件化。)
# ---------------------------------------------------------------------------
def _degenerate(m2, mean):
    return (not m2 > 0) or math.sqrt(m2) <= 1e-9 * abs(mean)


# ---------------------------------------------------------------------------(對齊 scipy 預設 bias=True）
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
    if _degenerate(m2, m):
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
    if _degenerate(m2, m):
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
