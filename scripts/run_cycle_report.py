#!/usr/bin/env python3
"""Generate a mobile-friendly cycle analysis HTML report for one A-share stock.

Usage:
  python scripts/run_cycle_report.py --name 中国铝业 --password 'YOUR_PASSWORD'
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class DbConfig:
    host: str
    user: str
    password: str
    database: str


def mysql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def run_mysql(sql: str, cfg: DbConfig) -> List[str]:
    cmd = [
        "mysql",
        "-h",
        cfg.host,
        "-u",
        cfg.user,
        f"-p{cfg.password}",
        cfg.database,
        "-N",
        "-B",
        "-e",
        sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "mysql command failed")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def resolve_stock(name: str, cfg: DbConfig) -> Tuple[str, str]:
    safe_name = mysql_escape(name)
    sql_exact = (
        "SELECT ts_code, name FROM stock_basic "
        f"WHERE name = '{safe_name}' ORDER BY ts_code;"
    )
    exact = run_mysql(sql_exact, cfg)
    if exact:
        ts_code, real_name = exact[0].split("\t", 1)
        return ts_code, real_name

    sql_like = (
        "SELECT ts_code, name FROM stock_basic "
        f"WHERE name LIKE '%{safe_name}%' ORDER BY ts_code LIMIT 1;"
    )
    fuzzy = run_mysql(sql_like, cfg)
    if fuzzy:
        ts_code, real_name = fuzzy[0].split("\t", 1)
        return ts_code, real_name
    raise RuntimeError(f"stock not found by name: {name}")


def latest_trade_date(cfg: DbConfig) -> str:
    rows = run_mysql("SELECT MAX(trade_date) FROM stock_daily;", cfg)
    if not rows:
        raise RuntimeError("no trade_date in stock_daily")
    return rows[0].strip()


def _parse_num(raw: str) -> Optional[float]:
    if raw == "" or raw.upper() == "NULL":
        return None
    return float(raw)


def fetch_daily(ts_code: str, start: str, end: str, cfg: DbConfig) -> List[Dict[str, Optional[float]]]:
    safe_code = mysql_escape(ts_code)
    sql = (
        "SELECT trade_date, open, high, low, close, pre_close, `change`, pct_chg, vol, amount "
        "FROM stock_daily "
        f"WHERE ts_code = '{safe_code}' "
        f"AND trade_date >= '{start}' AND trade_date <= '{end}' "
        "ORDER BY trade_date ASC;"
    )
    rows = run_mysql(sql, cfg)
    out: List[Dict[str, Optional[float]]] = []
    for ln in rows:
        (
            trade_date,
            open_p,
            high_p,
            low_p,
            close_p,
            pre_close_p,
            change_p,
            pct_chg_p,
            vol_p,
            amount_p,
        ) = ln.split("\t")
        open_num = _parse_num(open_p)
        high_num = _parse_num(high_p)
        low_num = _parse_num(low_p)
        close_v = _parse_num(close_p)
        pre_close_num = _parse_num(pre_close_p)
        if close_v is None:
            continue
        open_v = open_num if open_num is not None else close_v
        high_v = high_num if high_num is not None else max(open_v, close_v)
        low_v = low_num if low_num is not None else min(open_v, close_v)
        pre_close_v = pre_close_num if pre_close_num is not None else close_v
        out.append(
            {
                "trade_date": trade_date,
                "open": open_v,
                "high": high_v,
                "low": low_v,
                "close": close_v,
                "pre_close": pre_close_v,
                "change": _parse_num(change_p),
                "pct_chg": _parse_num(pct_chg_p),
                "vol": _parse_num(vol_p),
                "amount": _parse_num(amount_p),
            }
        )
    return out


def zigzag_pivots(rows: List[Dict[str, Optional[float]]], threshold: float, min_gap: int) -> List[Tuple[int, str, float]]:
    if not rows:
        return []
    pivot_idx = 0
    pivot_price = float(rows[0]["close"] or 0)
    trend = 0  # 0 unknown, 1 up, -1 down
    cand_idx = 0
    cand_price = float(rows[0]["close"] or 0)
    pivots: List[Tuple[int, str, float]] = []

    for i in range(1, len(rows)):
        p = float(rows[i]["close"] or 0)
        if trend == 0:
            up = p / pivot_price - 1
            down = pivot_price / p - 1
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

    clean: List[Tuple[int, str, float]] = []
    for p in pivots:
        if not clean or (clean[-1][0], clean[-1][1]) != (p[0], p[1]):
            clean.append(p)
    return clean


def make_html(
    stock_name: str,
    ts_code: str,
    rows: List[Dict[str, Optional[float]]],
    pivots: List[Tuple[int, str, float]],
    threshold: float,
    min_gap: int,
) -> str:
    if not rows:
        raise RuntimeError("no rows for html")

    def v(r: Dict[str, Optional[float]], key: str, default: float = 0.0) -> float:
        raw = r.get(key)
        return float(raw) if raw is not None else default

    def fmt_num(value: Optional[float], digits: int = 2) -> str:
        if value is None:
            return "n/a"
        return f"{value:.{digits}f}"

    def fmt_large(value: Optional[float], digits: int = 2) -> str:
        if value is None:
            return "n/a"
        av = abs(value)
        if av >= 1e8:
            return f"{value / 1e8:.{digits}f}亿"
        if av >= 1e4:
            return f"{value / 1e4:.{digits}f}万"
        return f"{value:.{digits}f}"

    cycles = []
    for i in range(len(pivots) - 1):
        i1, t1, p1 = pivots[i]
        i2, t2, p2 = pivots[i + 1]
        if i2 <= i1:
            continue
        d1 = str(rows[i1]["trade_date"])
        d2 = str(rows[i2]["trade_date"])
        y1, m1, dd1 = map(int, d1.split("-"))
        y2, m2, dd2 = map(int, d2.split("-"))
        pct = (p2 / p1 - 1) * 100
        cycles.append(
            {
                "idx": i + 1,
                "start_idx": i1,
                "end_idx": i2,
                "start_date": d1,
                "start_type": t1,
                "start_price": round(p1, 2),
                "end_date": d2,
                "end_type": t2,
                "end_price": round(p2, 2),
                "direction": "上行" if pct >= 0 else "下行",
                "chg_pct": round(pct, 2),
                "trade_days": i2 - i1,
                "cal_days": (date(y2, m2, dd2) - date(y1, m1, dd1)).days,
            }
        )

    closes = [v(r, "close") for r in rows]
    opens = [v(r, "open", v(r, "close")) for r in rows]
    highs = [v(r, "high", max(opens[i], closes[i])) for i, r in enumerate(rows)]
    lows = [v(r, "low", min(opens[i], closes[i])) for i, r in enumerate(rows)]
    pre_closes = [v(r, "pre_close", closes[i]) for i, r in enumerate(rows)]
    pct_chgs = [v(r, "pct_chg") for r in rows]
    changes = [v(r, "change") for r in rows]
    vols = [v(r, "vol") for r in rows]
    amounts = [v(r, "amount") for r in rows]
    dates = [str(r["trade_date"]) for r in rows]

    latest = rows[-1]
    summary = {
        "sample_days": len(rows),
        "min_close": round(min(closes), 2),
        "max_close": round(max(closes), 2),
        "latest_date": str(latest["trade_date"]),
        "latest_open": round(v(latest, "open", v(latest, "close")), 2),
        "latest_high": round(v(latest, "high", v(latest, "close")), 2),
        "latest_low": round(v(latest, "low", v(latest, "close")), 2),
        "latest_close": round(v(latest, "close"), 2),
        "latest_pct_chg": round(v(latest, "pct_chg"), 2),
        "latest_change": round(v(latest, "change"), 2),
        "latest_vol": v(latest, "vol"),
        "latest_amount": v(latest, "amount"),
        "pivot_count": len(pivots),
        "cycle_count": len(cycles),
        "avg_abs_cycle_pct": round(sum(abs(c["chg_pct"]) for c in cycles) / len(cycles), 2)
        if cycles
        else 0,
        "max_up_pct": max((c["chg_pct"] for c in cycles), default=0),
        "max_down_pct": min((c["chg_pct"] for c in cycles), default=0),
    }
    pivot_points = [
        {"i": i, "type": t, "price": round(p, 2), "date": str(rows[i]["trade_date"])}
        for i, t, p in pivots
    ]

    cycle_rows = []
    for c in cycles:
        sign = "+" if c["chg_pct"] >= 0 else ""
        cls = "up" if c["chg_pct"] >= 0 else "down"
        cycle_rows.append(
            f"<tr><td>C{c['idx']}</td><td>{c['start_date']} ({c['start_type']} {c['start_price']})</td>"
            f"<td>{c['end_date']} ({c['end_type']} {c['end_price']})</td><td class='{cls}'>{c['direction']}</td>"
            f"<td class='{cls}'>{sign}{c['chg_pct']}%</td><td>{c['trade_days']}</td><td>{c['cal_days']}</td></tr>"
        )

    pivot_rows = []
    for i, p in enumerate(pivot_points, 1):
        pivot_rows.append(
            f"<tr><td>P{i}</td><td>{p['date']}</td><td>{p['type']}</td><td>{p['price']}</td></tr>"
        )

    return f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>
  <title>{stock_name} 周期波动分析（移动版）</title>
  <style>
    :root {{
      --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --bd:#e2e8f0;
      --up:#b91c1c; --down:#065f46;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif; }}
    .container {{ max-width: 920px; margin:0 auto; padding:12px; }}
    h1 {{ font-size:20px; margin:0 0 8px 0; line-height:1.35; }}
    .sub {{ color:var(--muted); font-size:12px; margin:0 0 12px 0; line-height:1.5; }}
    .grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px; margin-bottom:12px; }}
    .card {{ background:var(--card); border:1px solid var(--bd); border-radius:12px; padding:10px; }}
    .k {{ color:var(--muted); font-size:11px; }}
    .v {{ margin-top:4px; font-weight:700; font-size:16px; }}
    .up {{ color:var(--up); font-weight:700; }}
    .down {{ color:var(--down); font-weight:700; }}
    .panel {{ background:var(--card); border:1px solid var(--bd); border-radius:12px; padding:10px; margin-bottom:12px; }}
    .chart-panel:fullscreen {{
      width:100vw; height:100vh; margin:0; border-radius:0; border:none; padding:8px;
      display:flex; flex-direction:column; background:#fff;
    }}
    .chart-panel:fullscreen .chart-wrap {{ flex:1; min-height:0; aspect-ratio:auto; }}
    .chart-panel:fullscreen .hint {{ margin-top:6px; }}
    .panel h3 {{ margin:0 0 8px 0; font-size:15px; }}
    .chart-wrap {{ width:100%; aspect-ratio: 16/10; min-height:260px; border:1px solid var(--bd); border-radius:10px; background:#fff; overflow:hidden; touch-action:none; display:flex; flex-direction:column; gap:8px; padding:8px; }}
    .main-pane {{ flex:1; min-height:180px; border:1px solid var(--bd); border-radius:8px; overflow:hidden; }}
    .sub-pane {{ height:32%; min-height:90px; border:1px solid var(--bd); border-radius:8px; overflow:hidden; }}
    canvas {{ width:100%; height:100%; display:block; }}
    .chart-tools {{ display:flex; gap:8px; margin-top:8px; }}
    .sub-switch {{ display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }}
    .sub-switch button.active {{ border-color:#2563eb; color:#2563eb; background:#eff6ff; }}
    .chart-tools button {{
      border:1px solid var(--bd); background:#fff; border-radius:8px; padding:4px 10px;
      font-size:12px; color:var(--text);
    }}
    .hint {{ margin-top:6px; color:var(--muted); font-size:11px; line-height:1.45; }}
    .table-scroll {{ overflow:auto; -webkit-overflow-scrolling:touch; border:1px solid var(--bd); border-radius:10px; }}
    table {{ width:100%; border-collapse:collapse; min-width:620px; background:#fff; }}
    th,td {{ border-bottom:1px solid var(--bd); padding:8px 9px; font-size:12px; text-align:left; white-space:nowrap; }}
    th {{ position:sticky; top:0; background:#f8fafc; z-index:1; }}
    tr:last-child td {{ border-bottom:none; }}
    @media (min-width: 768px) {{
      .container {{ padding:16px; }}
      h1 {{ font-size:24px; }}
      .sub {{ font-size:13px; }}
      .grid {{ grid-template-columns: repeat(4, minmax(0,1fr)); gap:10px; }}
      .card {{ padding:12px; }}
      .v {{ font-size:18px; }}
      .panel {{ padding:12px; }}
      .chart-wrap {{ aspect-ratio: 16/8; min-height:340px; }}
      th,td {{ font-size:13px; }}
    }}
  </style>
</head>
<body>
  <div class='container'>
    <h1>{stock_name}（{ts_code}）过去一年周期波动分析（移动版）</h1>
    <p class='sub'>区间：{rows[0]['trade_date']} ~ {rows[-1]['trade_date']} ｜ 方法：ZigZag（反转阈值 {int(threshold*100)}%，最小间隔 {min_gap} 个交易日）</p>

    <div class='grid'>
      <div class='card'><div class='k'>样本交易日</div><div class='v'>{summary['sample_days']}</div></div>
      <div class='card'><div class='k'>收盘价区间</div><div class='v'>{summary['min_close']} ~ {summary['max_close']}</div></div>
      <div class='card'><div class='k'>极值点数量</div><div class='v'>{summary['pivot_count']}</div></div>
      <div class='card'><div class='k'>完整周期数</div><div class='v'>{summary['cycle_count']}</div></div>
      <div class='card'><div class='k'>平均周期绝对波动</div><div class='v'>{summary['avg_abs_cycle_pct']}%</div></div>
      <div class='card'><div class='k'>最大上行周期</div><div class='v up'>+{summary['max_up_pct']}%</div></div>
      <div class='card'><div class='k'>最大下行周期</div><div class='v down'>{summary['max_down_pct']}%</div></div>
      <div class='card'><div class='k'>最新K线</div><div class='v'>{summary['latest_date']} / O{summary['latest_open']} H{summary['latest_high']} L{summary['latest_low']} C{summary['latest_close']}</div></div>
    </div>

    <div class='panel chart-panel' id='chartPanel'>
      <h3>{stock_name} K线走势（自适应）</h3>
      <div class='chart-wrap' id='chartWrap'>
        <div class='main-pane'><canvas id='mainChart'></canvas></div>
        <div class='sub-pane'><canvas id='subChart'></canvas></div>
      </div>
      <div class='sub-switch'>
        <button id='subVol' class='active'>成交量</button>
        <button id='subAmount'>成交额</button>
        <button id='subPct'>涨跌幅</button>
      </div>
      <div class='chart-tools'>
        <button id='zoomIn'>放大</button>
        <button id='zoomOut'>缩小</button>
        <button id='zoomReset'>重置</button>
        <button id='fullscreenToggle'>全屏</button>
      </div>
      <div class='hint'>触控板双指滑动=平移；双指捏合或 Alt+滚轮=缩放（Safari 也支持捏合缩放）。主图为K线+周期主波段，底部可切换成交量、成交额、涨跌幅。</div>
    </div>

    <div class='panel'>
      <h3>周期明细</h3>
      <div class='table-scroll'>
      <table>
        <thead><tr><th>周期</th><th>起点</th><th>终点</th><th>方向</th><th>涨跌幅</th><th>交易日</th><th>自然日</th></tr></thead>
        <tbody>{''.join(cycle_rows) if cycle_rows else "<tr><td colspan='7'>无周期</td></tr>"}</tbody>
      </table>
      </div>
    </div>

    <div class='panel'>
      <h3>极值点</h3>
      <div class='table-scroll'>
      <table style='min-width:420px'>
        <thead><tr><th>序号</th><th>日期</th><th>类型</th><th>价格</th></tr></thead>
        <tbody>{''.join(pivot_rows) if pivot_rows else "<tr><td colspan='4'>无</td></tr>"}</tbody>
      </table>
      </div>
    </div>
  </div>

  <script>
    const dates = {json.dumps(dates, ensure_ascii=False)};
    const opens = {json.dumps([round(x, 4) for x in opens], ensure_ascii=False)};
    const highs = {json.dumps([round(x, 4) for x in highs], ensure_ascii=False)};
    const lows = {json.dumps([round(x, 4) for x in lows], ensure_ascii=False)};
    const closes = {json.dumps([round(x, 4) for x in closes], ensure_ascii=False)};
    const preCloses = {json.dumps([round(x, 4) for x in pre_closes], ensure_ascii=False)};
    const pctChgs = {json.dumps([round(x, 4) for x in pct_chgs], ensure_ascii=False)};
    const vols = {json.dumps([round(x, 4) for x in vols], ensure_ascii=False)};
    const amounts = {json.dumps([round(x, 4) for x in amounts], ensure_ascii=False)};
    const changes = {json.dumps([round(x, 4) for x in changes], ensure_ascii=False)};
    const pivots = {json.dumps(pivot_points, ensure_ascii=False)};
    const cycleSegments = {json.dumps(
        [
            {
                "start_idx": c["start_idx"],
                "end_idx": c["end_idx"],
                "start_price": c["start_price"],
                "end_price": c["end_price"],
                "chg_pct": c["chg_pct"],
            }
            for c in cycles
        ],
        ensure_ascii=False,
    )};
    const chartPanel = document.getElementById('chartPanel');
    const wrap = document.getElementById('chartWrap');
    const mainCvs = document.getElementById('mainChart');
    const subCvs = document.getElementById('subChart');
    const mainCtx = mainCvs.getContext('2d');
    const subCtx = subCvs.getContext('2d');
    const zoomInBtn = document.getElementById('zoomIn');
    const zoomOutBtn = document.getElementById('zoomOut');
    const zoomResetBtn = document.getElementById('zoomReset');
    const fullscreenToggleBtn = document.getElementById('fullscreenToggle');
    const subVolBtn = document.getElementById('subVol');
    const subAmountBtn = document.getElementById('subAmount');
    const subPctBtn = document.getElementById('subPct');
    let subMode = 'vol';
    const view = {{
      left: 0,
      right: closes.length - 1,
      minBars: Math.max(20, Math.floor(closes.length * 0.06))
    }};

    function clampView() {{
      const maxRight = closes.length - 1;
      let span = view.right - view.left;
      const minSpan = Math.min(view.minBars, maxRight);
      const maxSpan = Math.max(minSpan, maxRight);
      span = Math.max(minSpan, Math.min(maxSpan, span));
      if (view.left < 0) {{
        view.left = 0;
        view.right = view.left + span;
      }}
      if (view.right > maxRight) {{
        view.right = maxRight;
        view.left = view.right - span;
      }}
      if (view.left < 0) view.left = 0;
      if (view.right <= view.left) view.right = Math.min(maxRight, view.left + minSpan);
    }}

    function setView(left, right) {{
      view.left = left;
      view.right = right;
      clampView();
    }}

    function zoomAt(scale, anchorIdx) {{
      const oldSpan = view.right - view.left;
      const maxSpan = closes.length - 1;
      const minSpan = Math.min(view.minBars, maxSpan);
      let newSpan = oldSpan * scale;
      newSpan = Math.max(minSpan, Math.min(maxSpan, newSpan));
      const ratio = (anchorIdx - view.left) / (oldSpan || 1);
      const newLeft = anchorIdx - ratio * newSpan;
      const newRight = newLeft + newSpan;
      setView(newLeft, newRight);
    }}

    function panBy(deltaIdx) {{
      setView(view.left + deltaIdx, view.right + deltaIdx);
    }}

    function idxFromClientX(clientX, rect) {{
      const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
      const frac = x / Math.max(1, rect.width);
      return view.left + frac * (view.right - view.left);
    }}

    function drawGrid(ctx, W, H, pad, yLabels, labelFormatter) {{
      const cw = W - pad.l - pad.r;
      const ch = H - pad.t - pad.b;
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#64748b';
      ctx.font = W < 640 ? '10px sans-serif' : '12px sans-serif';
      for (let i = 0; i < yLabels; i++) {{
        const k = i / (yLabels - 1);
        const y = pad.t + k * ch;
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(W - pad.r, y);
        ctx.stroke();
        const txt = labelFormatter(k);
        ctx.fillText(txt, 4, y + 4);
      }}
    }}

    function formatLarge(v) {{
      const av = Math.abs(v);
      if (av >= 1e8) return (v / 1e8).toFixed(2) + '亿';
      if (av >= 1e4) return (v / 1e4).toFixed(2) + '万';
      return v.toFixed(2);
    }}

    function draw() {{
      const dpr = window.devicePixelRatio || 1;
      const cssW = Math.max(280, wrap.clientWidth);
      const cssH = Math.max(260, wrap.clientHeight || Math.round(cssW * 0.62));
      const mainCssH = Math.max(180, Math.floor((cssH - 8) * 0.7));
      const subCssH = Math.max(90, cssH - 8 - mainCssH);

      mainCvs.width = Math.floor(cssW * dpr);
      mainCvs.height = Math.floor(mainCssH * dpr);
      subCvs.width = Math.floor(cssW * dpr);
      subCvs.height = Math.floor(subCssH * dpr);

      mainCtx.setTransform(1, 0, 0, 1, 0, 0);
      mainCtx.clearRect(0, 0, mainCvs.width, mainCvs.height);
      mainCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      subCtx.setTransform(1, 0, 0, 1, 0, 0);
      subCtx.clearRect(0, 0, subCvs.width, subCvs.height);
      subCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const mobile = cssW < 640;
      const mainPad = mobile ? {{l:44,r:12,t:18,b:12}} : {{l:62,r:14,t:22,b:16}};
      const subPad = mobile ? {{l:44,r:12,t:14,b:26}} : {{l:62,r:14,t:16,b:30}};
      const mainW = cssW, mainH = mainCssH;
      const subW = cssW, subH = subCssH;
      const mainCw = mainW - mainPad.l - mainPad.r;
      const mainCh = mainH - mainPad.t - mainPad.b;
      const subCw = subW - subPad.l - subPad.r;
      const subCh = subH - subPad.t - subPad.b;
      const leftIdx = Math.max(0, Math.floor(view.left));
      const rightIdx = Math.min(closes.length - 1, Math.ceil(view.right));
      const visibleHigh = highs.slice(leftIdx, rightIdx + 1);
      const visibleLow = lows.slice(leftIdx, rightIdx + 1);
      const rawMin = Math.min(...visibleLow);
      const rawMax = Math.max(...visibleHigh);
      const padPct = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.01, 0.01);
      const minP = rawMin - padPct;
      const maxP = rawMax + padPct;
      const xOf = i => mainPad.l + ((i - view.left) / (view.right - view.left || 1)) * mainCw;
      const yOf = p => mainPad.t + (maxP - p) / (maxP - minP || 1) * mainCh;

      mainCtx.fillStyle = '#fff';
      mainCtx.fillRect(0, 0, mainW, mainH);
      drawGrid(mainCtx, mainW, mainH, mainPad, 6, (k) => (maxP - k * (maxP - minP)).toFixed(2));

      function line(ctx, pts, color, w) {{
        if (!pts.length) return;
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.strokeStyle = color;
        ctx.lineWidth = w;
        ctx.stroke();
      }}

      function drawTag(x, y, text, color, mobile) {{
        mainCtx.font = mobile ? '10px sans-serif' : '11px sans-serif';
        const padX = mobile ? 5 : 6;
        const padY = mobile ? 2 : 3;
        const tw = mainCtx.measureText(text).width;
        const w = tw + padX * 2;
        const h = (mobile ? 12 : 14) + padY * 2;
        const x0 = x - w / 2;
        const y0 = y - h / 2;
        const r = 4;

        mainCtx.beginPath();
        mainCtx.moveTo(x0 + r, y0);
        mainCtx.lineTo(x0 + w - r, y0);
        mainCtx.quadraticCurveTo(x0 + w, y0, x0 + w, y0 + r);
        mainCtx.lineTo(x0 + w, y0 + h - r);
        mainCtx.quadraticCurveTo(x0 + w, y0 + h, x0 + w - r, y0 + h);
        mainCtx.lineTo(x0 + r, y0 + h);
        mainCtx.quadraticCurveTo(x0, y0 + h, x0, y0 + h - r);
        mainCtx.lineTo(x0, y0 + r);
        mainCtx.quadraticCurveTo(x0, y0, x0 + r, y0);
        mainCtx.closePath();

        mainCtx.fillStyle = 'rgba(255,255,255,0.86)';
        mainCtx.fill();
        mainCtx.strokeStyle = 'rgba(148,163,184,0.55)';
        mainCtx.lineWidth = 1;
        mainCtx.stroke();

        mainCtx.fillStyle = color;
        mainCtx.textBaseline = 'middle';
        mainCtx.fillText(text, x0 + padX, y0 + h / 2 + 0.5);
      }}

      const bars = Math.max(1, rightIdx - leftIdx + 1);
      const step = mainCw / Math.max(1, bars);
      const bodyW = Math.max(1, Math.min(12, step * 0.65));

      for (let i = leftIdx; i <= rightIdx; i++) {{
        const x = xOf(i);
        const o = opens[i];
        const h = highs[i];
        const l = lows[i];
        const c = closes[i];
        const up = c >= o;
        const color = up ? '#dc2626' : '#059669';
        const yH = yOf(h);
        const yL = yOf(l);
        const yO = yOf(o);
        const yC = yOf(c);
        mainCtx.strokeStyle = color;
        mainCtx.lineWidth = 1;
        mainCtx.beginPath();
        mainCtx.moveTo(x, yH);
        mainCtx.lineTo(x, yL);
        mainCtx.stroke();
        const top = Math.min(yO, yC);
        const hBody = Math.max(1, Math.abs(yO - yC));
        mainCtx.fillStyle = color;
        mainCtx.fillRect(x - bodyW / 2, top, bodyW, hBody);
      }}

      const zzPts = pivots
        .filter(p => p.i >= leftIdx && p.i <= rightIdx)
        .map(p=>[xOf(p.i), yOf(p.price)]);
      line(mainCtx, zzPts, '#f59e0b', mobile ? 2.2 : 2.8);

      cycleSegments.forEach(seg => {{
        if (seg.end_idx < leftIdx || seg.start_idx > rightIdx) return;
        const x1 = xOf(seg.start_idx), y1 = yOf(seg.start_price);
        const x2 = xOf(seg.end_idx), y2 = yOf(seg.end_price);
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2;
        const pct = Number(seg.chg_pct || 0);
        const txt = (pct > 0 ? '+' : '') + pct.toFixed(2) + '%';
        const color = pct >= 0 ? '#b91c1c' : '#065f46';
        const dy = y2 > y1 ? -12 : 12; // keep text away from segment center
        drawTag(mx, my + dy, txt, color, mobile);
      }});

      pivots.forEach((p,idx)=>{{
        if (p.i < leftIdx || p.i > rightIdx) return;
        const x=xOf(p.i), y=yOf(p.price);
        mainCtx.beginPath(); mainCtx.arc(x,y,mobile?3:4,0,Math.PI*2);
        mainCtx.fillStyle = p.type==='H' ? '#dc2626' : '#059669';
        mainCtx.fill();
        if (!mobile || idx % 2 === 0) {{
          mainCtx.fillStyle = '#0f172a';
          mainCtx.font = mobile ? '10px sans-serif' : '12px sans-serif';
          mainCtx.fillText('P'+(idx+1), x+5, y-5);
        }}
      }});

      mainCtx.fillStyle = '#475569';
      mainCtx.font = mobile ? '10px sans-serif' : '11px sans-serif';
      mainCtx.fillText('窗口: ' + bars + ' 日', mainW - (mobile ? 68 : 90), mainPad.t - 6);
      const latestIdx = rightIdx;
      const sign = changes[latestIdx] >= 0 ? '+' : '';
      mainCtx.fillText(
        '最新: C ' + closes[latestIdx].toFixed(2) + '  ' + sign + changes[latestIdx].toFixed(2) + ' (' + sign + pctChgs[latestIdx].toFixed(2) + '%)',
        mainPad.l + 4,
        mainPad.t - 6
      );

      subCtx.fillStyle = '#fff';
      subCtx.fillRect(0, 0, subW, subH);
      const vals = [];
      for (let i = leftIdx; i <= rightIdx; i++) {{
        if (subMode === 'vol') vals.push(vols[i] || 0);
        else if (subMode === 'amount') vals.push(amounts[i] || 0);
        else vals.push(pctChgs[i] || 0);
      }}
      let subMin = 0;
      let subMax = Math.max(...vals, 0);
      if (subMode === 'pct') {{
        subMin = Math.min(...vals, 0);
        subMax = Math.max(...vals, 0);
      }}
      const subPadVal = Math.max((subMax - subMin) * 0.1, Math.abs(subMax) * 0.04, 1e-6);
      subMin -= subPadVal;
      subMax += subPadVal;
      const subX = i => subPad.l + ((i - view.left) / (view.right - view.left || 1)) * subCw;
      const subY = v => subPad.t + (subMax - v) / (subMax - subMin || 1) * subCh;
      drawGrid(subCtx, subW, subH, subPad, 4, (k) => {{
        const cur = subMax - k * (subMax - subMin);
        if (subMode === 'pct') return cur.toFixed(2) + '%';
        return formatLarge(cur);
      }});

      if (subMode === 'pct') {{
        const pctPts = [];
        for (let i = leftIdx; i <= rightIdx; i++) {{
          pctPts.push([subX(i), subY(pctChgs[i] || 0)]);
        }}
        line(subCtx, pctPts, '#2563eb', mobile ? 1.5 : 1.8);
        const y0 = subY(0);
        subCtx.strokeStyle = '#94a3b8';
        subCtx.setLineDash([4, 3]);
        subCtx.beginPath();
        subCtx.moveTo(subPad.l, y0);
        subCtx.lineTo(subW - subPad.r, y0);
        subCtx.stroke();
        subCtx.setLineDash([]);
      }} else {{
        const subStep = subCw / Math.max(1, bars);
        const bw = Math.max(1, Math.min(10, subStep * 0.62));
        const y0 = subY(0);
        for (let i = leftIdx; i <= rightIdx; i++) {{
          const value = subMode === 'vol' ? (vols[i] || 0) : (amounts[i] || 0);
          const x = subX(i);
          const y = subY(value);
          const up = closes[i] >= preCloses[i];
          subCtx.fillStyle = up ? 'rgba(220,38,38,0.82)' : 'rgba(5,150,105,0.82)';
          subCtx.fillRect(x - bw / 2, Math.min(y, y0), bw, Math.max(1, Math.abs(y0 - y)));
        }}
      }}

      const ticks = Math.max(3, Math.min(8, Math.floor(cssW / 100)));
      subCtx.fillStyle = '#64748b';
      subCtx.font = mobile ? '10px sans-serif' : '12px sans-serif';
      for (let i=0;i<=ticks;i++) {{
        const idx = Math.round(view.left + i / ticks * (view.right - view.left));
        const x = subX(idx);
        const txt = mobile ? dates[idx].slice(5) : dates[idx];
        subCtx.fillText(txt, x-(mobile?16:30), subH-10);
      }}
      const subTitle = subMode === 'vol' ? '成交量(手)' : (subMode === 'amount' ? '成交额(千元)' : '涨跌幅(%)');
      subCtx.fillStyle = '#475569';
      subCtx.font = mobile ? '10px sans-serif' : '11px sans-serif';
      subCtx.fillText(subTitle, subPad.l + 4, subPad.t - 4);
    }}

    let drawRaf = 0;
    function scheduleDraw() {{
      if (drawRaf) return;
      drawRaf = requestAnimationFrame(() => {{
        drawRaf = 0;
        draw();
      }});
    }}

    function isFullscreenActive() {{
      return document.fullscreenElement === chartPanel;
    }}

    function updateFullscreenBtn() {{
      fullscreenToggleBtn.textContent = isFullscreenActive() ? '退出全屏' : '全屏';
    }}

    async function lockLandscape() {{
      if (screen.orientation && screen.orientation.lock) {{
        try {{ await screen.orientation.lock('landscape'); }} catch (_) {{}}
      }}
    }}

    function unlockOrientation() {{
      if (screen.orientation && screen.orientation.unlock) {{
        try {{ screen.orientation.unlock(); }} catch (_) {{}}
      }}
    }}

    async function enterFullscreen() {{
      if (!chartPanel.requestFullscreen) return;
      try {{
        await chartPanel.requestFullscreen();
        await lockLandscape();
      }} catch (_) {{}}
      updateFullscreenBtn();
      scheduleDraw();
    }}

    async function exitFullscreen() {{
      if (document.fullscreenElement && document.exitFullscreen) {{
        try {{ await document.exitFullscreen(); }} catch (_) {{}}
      }}
      unlockOrientation();
      updateFullscreenBtn();
      scheduleDraw();
    }}

    window.addEventListener('resize', scheduleDraw);
    window.addEventListener('orientationchange', () => setTimeout(scheduleDraw, 120));
    if (window.ResizeObserver) new ResizeObserver(scheduleDraw).observe(wrap);
    document.addEventListener('fullscreenchange', () => {{
      updateFullscreenBtn();
      if (!isFullscreenActive()) unlockOrientation();
      scheduleDraw();
    }});

    // touch: one-finger pan, two-finger pinch zoom
    let pinchDist = 0;
    let pinchSpan = 0;
    let pinchAnchor = 0;
    let gestureBaseSpan = 0;
    let gestureAnchor = 0;
    let panLastX = null;
    let panVelocity = 0;
    let panLastTs = 0;
    let inertiaRaf = 0;

    function stopInertia() {{
      if (inertiaRaf) cancelAnimationFrame(inertiaRaf);
      inertiaRaf = 0;
    }}

    function startInertia() {{
      stopInertia();
      function step() {{
        if (Math.abs(panVelocity) < 0.01) {{
          inertiaRaf = 0;
          return;
        }}
        panBy(panVelocity);
        panVelocity *= 0.92;
        scheduleDraw();
        inertiaRaf = requestAnimationFrame(step);
      }}
      inertiaRaf = requestAnimationFrame(step);
    }}

    function touchDistance(t1, t2) {{
      const dx = t1.clientX - t2.clientX;
      const dy = t1.clientY - t2.clientY;
      return Math.hypot(dx, dy);
    }}

    function bindGestureEvents(cvs) {{
      cvs.addEventListener('touchstart', (e) => {{
        const rect = cvs.getBoundingClientRect();
        stopInertia();
        if (e.touches.length === 2) {{
          pinchDist = touchDistance(e.touches[0], e.touches[1]);
          pinchSpan = view.right - view.left;
          pinchAnchor = idxFromClientX((e.touches[0].clientX + e.touches[1].clientX) / 2, rect);
          panLastX = null;
          panLastTs = 0;
        }} else if (e.touches.length === 1) {{
          panLastX = e.touches[0].clientX;
          panLastTs = performance.now();
          panVelocity = 0;
        }}
      }}, {{ passive: true }});

      cvs.addEventListener('touchmove', (e) => {{
        if (e.touches.length === 2 && pinchDist > 0) {{
          e.preventDefault();
          const curDist = touchDistance(e.touches[0], e.touches[1]);
          const scale = pinchDist / Math.max(1, curDist);
          const oldSpan = view.right - view.left;
          const targetSpan = pinchSpan * scale;
          const ratio = (pinchAnchor - view.left) / Math.max(1e-6, oldSpan);
          const newLeft = pinchAnchor - ratio * targetSpan;
          const newRight = newLeft + targetSpan;
          setView(newLeft, newRight);
          scheduleDraw();
        }} else if (e.touches.length === 1 && panLastX != null) {{
          e.preventDefault();
          const x = e.touches[0].clientX;
          const dx = x - panLastX;
          panLastX = x;
          const now = performance.now();
          const dt = Math.max(1, now - panLastTs);
          panLastTs = now;
          const barsPerPx = (view.right - view.left) / Math.max(1, cvs.clientWidth);
          const deltaIdx = -dx * barsPerPx;
          panBy(deltaIdx);
          panVelocity = (deltaIdx / dt) * 16;
          scheduleDraw();
        }}
      }}, {{ passive: false }});

      cvs.addEventListener('touchend', (e) => {{
        if (e.touches && e.touches.length < 2) pinchDist = 0;
        if (!e.touches || e.touches.length === 0) {{
          panLastX = null;
          if (Math.abs(panVelocity) > 0.02) startInertia();
        }}
      }});

      cvs.addEventListener('touchcancel', () => {{
        pinchDist = 0;
        panLastX = null;
        startInertia();
      }});

      cvs.addEventListener('gesturestart', (e) => {{
        e.preventDefault();
        const rect = cvs.getBoundingClientRect();
        gestureBaseSpan = view.right - view.left;
        const cx = typeof e.clientX === 'number' ? e.clientX : (rect.left + rect.width / 2);
        gestureAnchor = idxFromClientX(cx, rect);
      }}, {{ passive: false }});

      cvs.addEventListener('gesturechange', (e) => {{
        e.preventDefault();
        if (!gestureBaseSpan) return;
        const oldSpan = view.right - view.left;
        const scale = Math.max(0.2, Number(e.scale) || 1);
        const targetSpan = gestureBaseSpan / scale;
        const ratio = (gestureAnchor - view.left) / Math.max(1e-6, oldSpan);
        const newLeft = gestureAnchor - ratio * targetSpan;
        const newRight = newLeft + targetSpan;
        setView(newLeft, newRight);
        scheduleDraw();
      }}, {{ passive: false }});

      cvs.addEventListener('gestureend', () => {{
        gestureBaseSpan = 0;
      }});

      cvs.addEventListener('wheel', (e) => {{
        e.preventDefault();
        const absX = Math.abs(e.deltaX);
        const absY = Math.abs(e.deltaY);
        const barsPerPx = (view.right - view.left) / Math.max(1, cvs.clientWidth);
        const rect = cvs.getBoundingClientRect();

        const isPinchZoom = e.ctrlKey;
        const wantsWheelZoom = isPinchZoom || e.altKey;
        if (wantsWheelZoom && absY > 0) {{
          const factor = e.deltaY < 0 ? 0.9 : 1.1;
          zoomAt(factor, idxFromClientX(e.clientX, rect));
          scheduleDraw();
          return;
        }}

        const panPx = absX >= absY ? e.deltaX : e.deltaY;
        if (Math.abs(panPx) > 0) {{
          panBy(panPx * barsPerPx);
          scheduleDraw();
        }}
      }}, {{ passive: false }});
    }}

    bindGestureEvents(mainCvs);
    bindGestureEvents(subCvs);
    [subVolBtn, subAmountBtn, subPctBtn].forEach(btn => {{
      btn.addEventListener('click', () => {{
        subMode = btn === subVolBtn ? 'vol' : (btn === subAmountBtn ? 'amount' : 'pct');
        subVolBtn.classList.toggle('active', subMode === 'vol');
        subAmountBtn.classList.toggle('active', subMode === 'amount');
        subPctBtn.classList.toggle('active', subMode === 'pct');
        scheduleDraw();
      }});
    }});

    mainCvs.addEventListener('dblclick', () => {{
      setView(0, closes.length - 1);
      scheduleDraw();
    }});

    zoomInBtn.addEventListener('click', () => {{
      zoomAt(0.8, (view.left + view.right) / 2);
      scheduleDraw();
    }});
    zoomOutBtn.addEventListener('click', () => {{
      zoomAt(1.25, (view.left + view.right) / 2);
      scheduleDraw();
    }});
    zoomResetBtn.addEventListener('click', () => {{
      setView(0, closes.length - 1);
      scheduleDraw();
    }});
    fullscreenToggleBtn.addEventListener('click', async () => {{
      if (isFullscreenActive()) await exitFullscreen();
      else await enterFullscreen();
    }});
    updateFullscreenBtn();
    draw();
  </script>
</body>
</html>
"""


def build_output_path(output_arg: str, stock_name: str, ts_code: str, end_date: str) -> Path:
    if output_arg:
        return Path(output_arg).expanduser().resolve()
    safe_name = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fa5-]+", "_", stock_name).strip("_")
    filename = f"cycle_report_{safe_name}_{ts_code}_{end_date}.html"
    return (Path(__file__).resolve().parents[1] / "reports" / filename).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate cycle report HTML by stock name.")
    parser.add_argument("--name", required=True, help="Stock name, e.g. 中国铝业")
    parser.add_argument("--host", default="192.168.1.15")
    parser.add_argument("--user", default="myuser")
    parser.add_argument("--password", required=True, help="MySQL password")
    parser.add_argument("--database", default="mydb")
    parser.add_argument(
        "--end-date",
        default="",
        help="End trade date (YYYY-MM-DD). Default: latest trade date in DB.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Lookback calendar days from end date. Default: 365",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.08,
        help="ZigZag reversal threshold, e.g. 0.08 for 8%%",
    )
    parser.add_argument(
        "--min-gap",
        type=int,
        default=5,
        help="Minimum trade-day gap between pivots.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output html path. Default: <repo>/reports/auto filename",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = DbConfig(host=args.host, user=args.user, password=args.password, database=args.database)

    ts_code, real_name = resolve_stock(args.name, cfg)
    end_date = args.end_date.strip() or latest_trade_date(cfg)
    start_date = (date.fromisoformat(end_date) - timedelta(days=args.lookback_days)).isoformat()

    rows = fetch_daily(ts_code=ts_code, start=start_date, end=end_date, cfg=cfg)
    if len(rows) < 20:
        raise RuntimeError(f"not enough daily data for {real_name}({ts_code}) in range")

    pivots = zigzag_pivots(rows, threshold=args.threshold, min_gap=args.min_gap)
    html = make_html(
        stock_name=real_name,
        ts_code=ts_code,
        rows=rows,
        pivots=pivots,
        threshold=args.threshold,
        min_gap=args.min_gap,
    )
    out_path = build_output_path(args.output, real_name, ts_code, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print(f"stock={real_name} ({ts_code})")
    print(f"range={rows[0]['trade_date']} ~ {rows[-1]['trade_date']}, days={len(rows)}")
    print(f"output={out_path}")


if __name__ == "__main__":
    main()
