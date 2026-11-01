"""Kubernetes resource-quantity parsing shared by the live components.

Implements the suffix grammar of ``k8s.io/apimachinery`` quantities: decimal
SI suffixes (``n u m k M G T P E``), binary suffixes (``Ki Mi Gi Ti Pi Ei``),
and decimal exponents (``1e3``, ``1E-3``).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_BINARY = {
    "Ki": Decimal(2) ** 10,
    "Mi": Decimal(2) ** 20,
    "Gi": Decimal(2) ** 30,
    "Ti": Decimal(2) ** 40,
    "Pi": Decimal(2) ** 50,
    "Ei": Decimal(2) ** 60,
}
_DECIMAL = {
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "": Decimal(1),
    "k": Decimal("1e3"),
    "M": Decimal("1e6"),
    "G": Decimal("1e9"),
    "T": Decimal("1e12"),
    "P": Decimal("1e15"),
    "E": Decimal("1e18"),
}
_MIB = Decimal(2) ** 20


def parse_quantity(raw) -> Decimal:
    """Return the base-unit value of a Kubernetes quantity string."""
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    text = str(raw).strip()
    if not text:
        raise ValueError("empty Kubernetes quantity")
    for suffix, multiplier in _BINARY.items():
        if text.endswith(suffix):
            return _number(text[:-len(suffix)], raw) * multiplier
    # A trailing exponent such as 1e3 is not a suffix; Decimal parses it.
    if text[-1].isdigit() or text[-1] == ".":
        return _number(text, raw)
    suffix = text[-1]
    if suffix not in _DECIMAL:
        raise ValueError(f"unknown Kubernetes quantity suffix in {raw!r}")
    return _number(text[:-1], raw) * _DECIMAL[suffix]


def _number(text: str, raw) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid Kubernetes quantity {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"invalid Kubernetes quantity {raw!r}")
    return value


def cpu_cores(raw, default: float = 0.0) -> float:
    """CPU quantity in cores (``500m`` -> 0.5)."""
    if raw is None or raw == "":
        return default
    return float(parse_quantity(raw))


def memory_mib(raw, default: float = 0.0) -> float:
    """Memory quantity in MiB (``1Gi`` -> 1024, ``1G`` -> 953.67...)."""
    if raw is None or raw == "":
        return default
    return float(parse_quantity(raw) / _MIB)
