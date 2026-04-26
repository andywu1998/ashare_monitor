"""Core ZigZag cycle algorithm shared by scripts/services."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


Pivot = Tuple[int, str, float]  # (index, type, price), type in {"H", "L"}


def _close_of(row: Dict[str, Optional[float]]) -> float:
    raw = row.get("close")
    if raw is None:
        return 0.0
    return float(raw)


def zigzag_pivots(
    rows: List[Dict[str, Optional[float]]], threshold: float, min_gap: int
) -> List[Pivot]:
    """Detect zigzag pivots from close-price rows.

    Args:
        rows: Daily rows containing at least `close`.
        threshold: Reversal threshold (e.g. 0.08 means 8%).
        min_gap: Minimum bar gap between confirmed pivots.
    """
    if not rows:
        return []

    pivot_idx = 0
    pivot_price = _close_of(rows[0])
    trend = 0  # 0 unknown, 1 up, -1 down
    cand_idx = 0
    cand_price = _close_of(rows[0])
    pivots: List[Pivot] = []

    for i in range(1, len(rows)):
        p = _close_of(rows[i])
        if p <= 0:
            continue

        if trend == 0:
            up = p / pivot_price - 1 if pivot_price else 0
            down = pivot_price / p - 1 if p else 0
            if up >= threshold:
                trend = 1
                cand_idx, cand_price = i, p
                pivots.append((pivot_idx, "L", pivot_price))
            elif down >= threshold:
                trend = -1
                cand_idx, cand_price = i, p
                pivots.append((pivot_idx, "H", pivot_price))
        elif trend == 1:
            if p >= cand_price:
                cand_idx, cand_price = i, p
            elif (cand_price / p - 1) >= threshold and (i - pivot_idx) >= min_gap:
                pivots.append((cand_idx, "H", cand_price))
                pivot_idx, pivot_price = cand_idx, cand_price
                trend = -1
                cand_idx, cand_price = i, p
        else:
            if p <= cand_price:
                cand_idx, cand_price = i, p
            elif (p / cand_price - 1) >= threshold and (i - pivot_idx) >= min_gap:
                pivots.append((cand_idx, "L", cand_price))
                pivot_idx, pivot_price = cand_idx, cand_price
                trend = 1
                cand_idx, cand_price = i, p

    if trend == 1:
        pivots.append((cand_idx, "H", cand_price))
    elif trend == -1:
        pivots.append((cand_idx, "L", cand_price))

    clean: List[Pivot] = []
    for p in pivots:
        if not clean or (clean[-1][0], clean[-1][1]) != (p[0], p[1]):
            clean.append(p)
    return clean


def current_trend(pivots: List[Pivot]) -> str:
    """Infer current cycle trend from the latest completed pivot leg.

    With the current zigzag implementation, the last appended pivot is the
    extreme of the ongoing leg at the right edge:
    - last pivot type "H" => the latest leg is upward (L -> H)
    - last pivot type "L" => the latest leg is downward (H -> L)
    """
    if len(pivots) < 2:
        return "unknown"
    last_type = pivots[-1][1]
    if last_type == "H":
        return "up"
    if last_type == "L":
        return "down"
    return "unknown"
