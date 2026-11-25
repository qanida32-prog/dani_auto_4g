"""Self-contained HTML reports with inline SVG charts (bonus: visualization).

No plotting library: the charts are plain SVG, so the report opens in any browser,
needs no network and adds no dependency.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence
from typing import Any

# Chart geometry
W, H = 560, 240
PAD_L, PAD_R, PAD_T, PAD_B = 64, 16, 16, 36

COLORS = {
    "series": "#2563eb",
    "mean": "#dc2626",
    "outlier": "#f59e0b",
    "failure": "#9ca3af",
    "bar": "#3b82f6",
    "grid": "#e5e7eb",
    "axis": "#6b7280",
}

CSS = """
body{font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;
     color:#111827;background:#f9fafb}
main{max-width:1200px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px}
h3{font-size:15px;margin:16px 0 6px}
.muted{color:#6b7280}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px}
.card b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
.card span{color:#6b7280;font-size:12px}
.charts{display:flex;flex-wrap:wrap;gap:12px}
.chart{margin:0;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px;
       max-width:100%;overflow-x:auto}
.chart svg{display:block;max-width:100%;height:auto}
.wrap{overflow-x:auto}
table{border-collapse:collapse;background:#fff;font-variant-numeric:tabular-nums}
th,td{border:1px solid #e5e7eb;padding:5px 10px;text-align:right}
th:first-child,td:first-child{text-align:left}
th{background:#f3f4f6;font-weight:600}
.ok{color:#15803d;font-weight:600} .bad{color:#b91c1c;font-weight:600}
.badge{display:inline-block;padding:1px 10px;border-radius:999px;font-size:13px;font-weight:700;
       vertical-align:middle;margin-left:6px}
.badge.pass{background:#dcfce7;color:#166534} .badge.fail{background:#fee2e2;color:#991b1b}
code{background:#f3f4f6;padding:1px 4px;border-radius:4px}
"""


# ---------------------------------------------------------------------- formatting
def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "-"
        return f"{value:.{digits}g}"
    return html.escape(str(value))


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + (abs(lo) or 1.0)
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.floor(lo / step + 1e-9)
    last = math.ceil(hi / step - 1e-9)  # the top tick must cover the maximum
    return [round(i * step, 12) for i in range(first, last + 1)]


class _Frame:
    """Maps data coordinates to SVG pixels inside the padded plot area."""

    def __init__(self, x_lo: float, x_hi: float, y_lo: float, y_hi: float) -> None:
        self.y_ticks = _nice_ticks(y_lo, y_hi)
        self.y_lo, self.y_hi = self.y_ticks[0], self.y_ticks[-1]
        self.x_lo, self.x_hi = x_lo, x_hi if x_hi > x_lo else x_lo + 1

    def x(self, v: float) -> float:
        return PAD_L + (v - self.x_lo) / (self.x_hi - self.x_lo) * (W - PAD_L - PAD_R)

    def y(self, v: float) -> float:
        return H - PAD_B - (v - self.y_lo) / (self.y_hi - self.y_lo) * (H - PAD_T - PAD_B)

    def axes(self, x_label: str, y_label: str) -> str:
        parts = []
        for t in self.y_ticks:
            yy = self.y(t)
            parts.append(
                f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" '
                f'stroke="{COLORS["grid"]}"/>'
                f'<text x="{PAD_L - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="11" '
                f'fill="{COLORS["axis"]}">{t:g}</text>'
            )
        parts.append(
            f'<text x="{(PAD_L + W - PAD_R) / 2}" y="{H - 6}" text-anchor="middle" '
            f'font-size="11" fill="{COLORS["axis"]}">{html.escape(x_label)}</text>'
            f'<text x="12" y="{(PAD_T + H - PAD_B) / 2}" text-anchor="middle" font-size="11" '
            f'fill="{COLORS["axis"]}" transform="rotate(-90 12 {(PAD_T + H - PAD_B) / 2})">'
            f"{html.escape(y_label)}</text>"
        )
        return "".join(parts)


def _svg(title: str, body: str) -> str:
    return (
        f'<figure class="chart"><svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{html.escape(title)}" font-family="system-ui,sans-serif">'
        f'{body}</svg><figcaption class="muted">{html.escape(title)}</figcaption></figure>'
    )


# ---------------------------------------------------------------------- charts
def time_series_chart(samples: Sequence[dict], title: str, outliers: Sequence[int] = ()) -> str:
    ok = [s for s in samples if s["value"] is not None]
    failed = [s for s in samples if s["value"] is None]
    if not ok:
        return _svg(title, '<text x="20" y="40">no valid measurements</text>')
    xs = [s["actual_offset_s"] for s in samples]
    vals = [s["value"] for s in ok]
    f = _Frame(min(xs), max(xs), min(vals), max(vals))
    mean = sum(vals) / len(vals)
    points = " ".join(f"{f.x(s['actual_offset_s']):.1f},{f.y(s['value']):.1f}" for s in ok)
    outlier_set = set(outliers)
    dots = "".join(
        f'<circle cx="{f.x(s["actual_offset_s"]):.1f}" cy="{f.y(s["value"]):.1f}" '
        f'r="{4 if s["index"] in outlier_set else 2.5}" '
        f'fill="{COLORS["outlier"] if s["index"] in outlier_set else COLORS["series"]}">'
        f"<title>#{s['index']}: {s['value']:.6g} A</title></circle>"
        for s in ok
    )
    fails = "".join(
        f'<line x1="{f.x(s["actual_offset_s"]):.1f}" x2="{f.x(s["actual_offset_s"]):.1f}" '
        f'y1="{PAD_T}" y2="{H - PAD_B}" stroke="{COLORS["failure"]}" stroke-dasharray="3 3">'
        f"<title>#{s['index']} failed: {html.escape(s['error'] or '')}</title></line>"
        for s in failed
    )
    mean_line = (
        f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{f.y(mean):.1f}" y2="{f.y(mean):.1f}" '
        f'stroke="{COLORS["mean"]}" stroke-dasharray="6 4"><title>mean {mean:.6g} A</title></line>'
    )
    line = (
        f'<polyline points="{points}" fill="none" stroke="{COLORS["series"]}" stroke-width="1.5"/>'
    )
    return _svg(title, f.axes("time (s)", "current (A)") + fails + mean_line + line + dots)


def histogram_chart(values: Sequence[float], title: str) -> str:
    if not values:
        return _svg(title, '<text x="20" y="40">no valid measurements</text>')
    n = len(values)
    bins = max(1, min(20, math.ceil(math.log2(n) + 1)))  # Sturges' rule
    lo, hi = min(values), max(values)
    width = (hi - lo) / bins or 1.0
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    f = _Frame(lo, lo + bins * width, 0, max(counts))
    bars = "".join(
        f'<rect x="{f.x(lo + i * width) + 1:.1f}" y="{f.y(c):.1f}" '
        f'width="{max(1.0, f.x(lo + (i + 1) * width) - f.x(lo + i * width) - 2):.1f}" '
        f'height="{f.y(0) - f.y(c):.1f}" fill="{COLORS["bar"]}">'
        f"<title>{lo + i * width:.4g} - {lo + (i + 1) * width:.4g} A: {c}</title></rect>"
        for i, c in enumerate(counts)
    )
    x_ticks = "".join(
        f'<text x="{f.x(t):.1f}" y="{H - PAD_B + 14}" text-anchor="middle" font-size="10" '
        f'fill="{COLORS["axis"]}">{t:.3g}</text>'
        for t in (lo, (lo + hi) / 2, hi)
    )
    return _svg(title, f.axes("current (A)", "count") + bars + x_ticks)


def box_plot_chart(groups: dict[str, Sequence[float]], title: str, normalize: bool = False) -> str:
    """One box per group. ``normalize`` divides by each group's mean, so devices on very
    different scales can be compared on one axis (spread relative to the mean)."""
    groups = {k: list(v) for k, v in groups.items() if v}
    if not groups:
        return _svg(title, '<text x="20" y="40">no valid measurements</text>')
    if normalize:
        groups = {k: [x / (sum(v) / len(v)) for x in v] for k, v in groups.items()}
    all_vals = [x for v in groups.values() for x in v]
    f = _Frame(0, len(groups), min(all_vals), max(all_vals))
    slot = (W - PAD_L - PAD_R) / len(groups)
    parts = []
    for i, (name, vals) in enumerate(groups.items()):
        vals.sort()
        q1, med, q3 = _quartiles(vals)
        iqr = q3 - q1
        lo_w = min(v for v in vals if v >= q1 - 1.5 * iqr)
        hi_w = max(v for v in vals if v <= q3 + 1.5 * iqr)
        cx = PAD_L + slot * (i + 0.5)
        bw = min(60, slot * 0.5)
        parts.append(
            f'<line x1="{cx}" x2="{cx}" y1="{f.y(lo_w):.1f}" y2="{f.y(hi_w):.1f}" '
            f'stroke="{COLORS["axis"]}"/>'
            f'<rect x="{cx - bw / 2}" y="{f.y(q3):.1f}" width="{bw}" '
            f'height="{max(1.0, f.y(q1) - f.y(q3)):.1f}" fill="#dbeafe" '
            f'stroke="{COLORS["series"]}">'
            f"<title>{html.escape(name)}: Q1 {q1:.4g}, median {med:.4g}, Q3 {q3:.4g}</title></rect>"
            f'<line x1="{cx - bw / 2}" x2="{cx + bw / 2}" y1="{f.y(med):.1f}" y2="{f.y(med):.1f}" '
            f'stroke="{COLORS["mean"]}" stroke-width="2"/>'
            f'<text x="{cx}" y="{H - PAD_B + 14}" text-anchor="middle" font-size="11" '
            f'fill="{COLORS["axis"]}">{html.escape(name)}</text>'
        )
        parts.extend(
            f'<circle cx="{cx}" cy="{f.y(v):.1f}" r="3" fill="{COLORS["outlier"]}"/>'
            for v in vals
            if v < lo_w or v > hi_w
        )
    y_label = "current / mean" if normalize else "current (A)"
    return _svg(title, f.axes("", y_label) + "".join(parts))


def bar_chart(values: dict[str, float], title: str, y_label: str) -> str:
    values = {k: v for k, v in values.items() if v is not None and math.isfinite(v)}
    if not values:
        return _svg(title, '<text x="20" y="40">no data</text>')
    f = _Frame(0, len(values), 0, max(values.values()))
    slot = (W - PAD_L - PAD_R) / len(values)
    bars = "".join(
        f'<rect x="{PAD_L + slot * i + slot * 0.2:.1f}" y="{f.y(v):.1f}" width="{slot * 0.6:.1f}" '
        f'height="{f.y(0) - f.y(v):.1f}" fill="{COLORS["bar"]}"><title>{html.escape(k)}: '
        f"{v:.4g}</title></rect>"
        f'<text x="{PAD_L + slot * (i + 0.5):.1f}" y="{H - PAD_B + 14}" text-anchor="middle" '
        f'font-size="11" fill="{COLORS["axis"]}">{html.escape(k)}</text>'
        for i, (k, v) in enumerate(values.items())
    )
    return _svg(title, f.axes("", y_label) + bars)


def _quartiles(sorted_vals: list[float]) -> tuple[float, float, float]:
    def q(p: float) -> float:
        pos = p * (len(sorted_vals) - 1)
        lo = math.floor(pos)
        hi = min(lo + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)

    return q(0.25), q(0.5), q(0.75)


# ---------------------------------------------------------------------- pages
def _page(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _cards(items: list[tuple[str, str]]) -> str:
    return (
        '<div class="cards">'
        + "".join(
            f'<div class="card"><b>{v}</b><span>{html.escape(k)}</span></div>' for k, v in items
        )
        + "</div>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="wrap">{table}</div>'


def verdict_badge(passed: bool | None) -> str:
    if passed is None:
        return ""
    return (
        '<span class="badge pass">PASS</span>' if passed else '<span class="badge fail">FAIL</span>'
    )


def _verdict_table(verdict: dict[str, Any]) -> str:
    rows = [
        [
            html.escape(c["name"]),
            fmt(c["actual"], 4) if c["actual"] is not None else "no data",
            fmt(c["limit"], 4),
            '<span class="ok">pass</span>' if c["passed"] else '<span class="bad">fail</span>',
        ]
        for c in verdict["checks"]
    ]
    return _table(["Acceptance check", "actual", "limit", "result"], rows)


def _run_section(run: dict[str, Any], heading_level: int = 2) -> str:
    a = run["analysis"]
    stats, timing, cons, acc = a["statistics"], a["timing"], a["consistency"], a["accuracy"]
    meta = run["metadata"]
    h = f"h{heading_level}"
    sampling = meta["sampling"]
    verdict = run.get("verdict")
    plan = f" &middot; plan <code>{html.escape(meta['plan'])}</code>" if meta.get("plan") else ""
    parts = [
        f"<{h}>{html.escape(run['ammeter'])}{verdict_badge(verdict and verdict['passed'])} "
        f'<span class="muted"><code>{html.escape(run["run_id"])}</code></span></{h}>',
        f'<p class="muted">{html.escape(run["created_at"])} &middot; '
        f"{sampling['measurements_count']} samples at {fmt(sampling['sampling_frequency_hz'])} Hz "
        f"over {fmt(sampling['total_duration_seconds'])} s &middot; "
        f"<code>{html.escape(meta['host'])}:{meta['port']}</code>{plan}</p>",
    ]
    if stats:
        parts.append(
            _cards(
                [
                    ("mean (A)", fmt(stats["mean"])),
                    ("median (A)", fmt(stats["median"])),
                    ("std dev (A)", fmt(stats["std_dev"])),
                    ("min (A)", fmt(stats["min"])),
                    ("max (A)", fmt(stats["max"])),
                    ("CV", fmt(stats["cv_percent"], 3) + " %"),
                    (
                        "95% CI of mean",
                        f"{fmt(stats['ci95_low'], 4)} - {fmt(stats['ci95_high'], 4)}",
                    ),
                    ("failures", fmt(cons["failure_rate_percent"], 3) + " %"),
                ]
            )
        )
    else:
        parts.append('<p class="bad">Every measurement failed.</p>')

    indexed = [s for s in run["samples"]]
    parts.append(
        '<div class="charts">'
        + time_series_chart(indexed, "Measurements over time", cons["outlier_indices"])
        + histogram_chart([s["value"] for s in indexed if s["value"] is not None], "Distribution")
        + box_plot_chart(
            {run["ammeter"]: [s["value"] for s in indexed if s["value"] is not None]}, "Box plot"
        )
        + "</div>"
    )
    rows = [
        [
            "Achieved / target frequency (Hz)",
            f"{fmt(timing['achieved_frequency_hz'], 4)} / {fmt(timing['target_frequency_hz'], 4)}",
        ],
        [
            "Jitter mean / max (ms)",
            f"{fmt(timing['mean_jitter_ms'], 3)} / {fmt(timing['max_jitter_ms'], 3)}",
        ],
        [
            "Request latency mean / max (ms)",
            f"{fmt(timing['mean_latency_ms'], 3)} / {fmt(timing['max_latency_ms'], 3)}",
        ],
        ["Outliers (Tukey 1.5 IQR)", fmt(cons["outlier_count"])],
        ["Drift over the run (% of mean)", fmt(cons["drift_percent_of_mean"], 3)],
        [
            "Mean, first / second half (A)",
            f"{fmt(cons['first_half_mean'])} / {fmt(cons['second_half_mean'])}",
        ],
    ]
    if acc:
        rows += [
            ["Reference current (A)", fmt(acc["reference_current"])],
            [
                "Bias (A) / relative error (%)",
                f"{fmt(acc['bias'])} / {fmt(acc['relative_error_percent'], 3)}",
            ],
            ["Reference inside 95% CI", fmt(acc["reference_within_ci95"])],
        ]
    if verdict:
        parts.append(_verdict_table(verdict))
    parts.append(_table(["Timing and consistency", "value"], rows))
    errors = [s for s in run["samples"] if s["value"] is None]
    if errors:
        parts.append(
            "<h3>Failed requests</h3>"
            + _table(
                ["#", "time (s)", "error"],
                [
                    [str(s["index"]), fmt(s["actual_offset_s"], 4), html.escape(s["error"] or "")]
                    for s in errors
                ],
            )
        )
    return "".join(parts)


def render_run_report(run: dict[str, Any]) -> str:
    return _page(
        f"Ammeter test {run['run_id']}",
        f"<h1>Ammeter test report</h1>{_run_section(run)}",
    )


def render_suite_report(suite: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    comp = suite["comparison"]
    passed_by_run = {r["run_id"]: (r.get("verdict") or {}).get("passed") for r in runs}
    rows = [
        [
            f"{r['rank']}. {html.escape(r['ammeter'])}",
            verdict_badge(passed_by_run.get(r["run_id"])),
            fmt(r["mean"]),
            fmt(r["cv_percent"], 3),
            fmt(r["relative_error_percent"], 3),
            fmt(r["failure_rate_percent"], 3),
            fmt(r["score"], 4),
        ]
        for r in comp["ranking"]
    ]
    values = {
        r["ammeter"]: [s["value"] for s in r["samples"] if s["value"] is not None] for r in runs
    }
    cvs = {r["ammeter"]: r["cv_percent"] for r in comp["ranking"]}
    best = comp["most_reliable"]
    suite_passed = suite.get("metadata", {}).get("passed")
    plan = suite.get("metadata", {}).get("plan")
    body = (
        f"<h1>Ammeter comparison{verdict_badge(suite_passed)}</h1>"
        f'<p class="muted"><code>{html.escape(suite["suite_id"])}</code> &middot; '
        f"{html.escape(suite['created_at'])}"
        + (f" &middot; plan <code>{html.escape(plan)}</code>" if plan else "")
        + "</p>"
        + (
            f'<p>Most reliable: <span class="ok">{html.escape(best)}</span></p>'
            if best
            else '<p class="bad">No device produced valid measurements.</p>'
        )
        + _table(
            ["Ammeter", "verdict", "mean (A)", "CV %", "rel. error %", "failures %", "score"], rows
        )
        + f'<p class="muted">{html.escape(comp["method"])}</p>'
        + '<div class="charts">'
        + bar_chart(cvs, "Precision: coefficient of variation (lower is better)", "CV %")
        + box_plot_chart(values, "Spread relative to each device's mean", normalize=True)
        + "</div>"
        + "".join(_run_section(r, heading_level=2) for r in runs)
    )
    return _page(f"Ammeter comparison {suite['suite_id']}", body)
