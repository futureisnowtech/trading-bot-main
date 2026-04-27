"""
dashboard/ui.py — Premium UI primitives for the dashboard design system.

All functions return raw HTML strings.
Usage:  st.markdown(ui.hero_card(...), unsafe_allow_html=True)

No Streamlit imports — pure Python + inline HTML/CSS so fragments can call
these freely without any import-order issues.
"""

# ── Color tokens ──────────────────────────────────────────────────────────────
_BG_CARD = "#161b22"
_BG_CARD_HI = "#1c2333"
_BG_HERO = "linear-gradient(135deg, #1a1040 0%, #161b22 55%)"
_BORDER = "rgba(255,255,255,0.07)"
_BORDER_HERO = "rgba(188,140,255,0.22)"
_SHADOW = "0 2px 16px rgba(0,0,0,0.30)"
_SHADOW_HERO = "0 4px 28px rgba(188,140,255,0.12)"
_RADIUS = "20px"
_RADIUS_SM = "12px"
_TEXT_PRI = "#e6edf3"
_TEXT_SEC = "#8b949e"
_TEXT_CAP = "#484f58"

C_GREEN = "#3fb950"
C_AMBER = "#d29922"
C_RED = "#f85149"
C_MAG = "#bc8cff"
C_CYAN = "#58a6ff"
C_NEUTRAL = "#6e7681"

_CHIP = {
    "good": (C_GREEN, "rgba(63,185,80,0.12)"),
    "watch": (C_AMBER, "rgba(210,153,34,0.12)"),
    "problem": (C_RED, "rgba(248,81,73,0.12)"),
    "info": (C_CYAN, "rgba(88,166,255,0.12)"),
    "neutral": (C_NEUTRAL, "rgba(110,118,129,0.10)"),
    "archived": (C_NEUTRAL, "rgba(110,118,129,0.06)"),
    "crypto": (C_MAG, "rgba(188,140,255,0.12)"),
}


# ── Atoms ─────────────────────────────────────────────────────────────────────


def chip(label: str, status: str = "neutral") -> str:
    color, bg = _CHIP.get(status, (C_NEUTRAL, "rgba(110,118,129,0.10)"))
    return (
        f'<span style="display:inline-block;padding:3px 12px;border-radius:100px;'
        f"font-size:0.70em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;"
        f'color:{color};background:{bg};">{label}</span>'
    )


# alias kept for backwards compat calls
status_chip = chip


def metric_row(
    label: str,
    value: str,
    value_color: str = None,
    dot_color: str = None,
) -> str:
    vc = value_color if value_color else _TEXT_PRI
    dot = (
        f'<span style="color:{dot_color};margin-right:5px;font-size:0.72em;">●</span>'
        if dot_color
        else ""
    )
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.81em;">'
        f'<span style="color:{_TEXT_SEC};">{dot}{label}</span>'
        f'<span style="color:{vc};font-weight:600;">{value}</span>'
        f"</div>"
    )


def info_callout(text: str, level: str = "info") -> str:
    _map = {
        "info": (C_CYAN, "rgba(88,166,255,0.07)"),
        "warn": (C_AMBER, "rgba(210,153,34,0.09)"),
        "crit": (C_RED, "rgba(248,81,73,0.07)"),
        "good": (C_GREEN, "rgba(63,185,80,0.07)"),
    }
    color, bg = _map.get(level, (C_NEUTRAL, "rgba(110,118,129,0.07)"))
    return (
        f'<div style="background:{bg};border-left:3px solid {color};'
        f"padding:10px 14px;border-radius:0 8px 8px 0;margin:8px 0;"
        f'font-size:0.79em;color:{_TEXT_SEC};line-height:1.55;">{text}</div>'
    )


def section_header(title: str, subtitle: str = "") -> str:
    sub = (
        f'<div style="font-size:0.77em;color:{_TEXT_CAP};margin-top:3px;line-height:1.4;">{subtitle}</div>'
        if subtitle
        else ""
    )
    return (
        f'<div style="margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid {_BORDER};">'
        f'<div style="font-size:0.69em;color:{_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.12em;font-weight:700;">{title}</div>'
        f"{sub}"
        f"</div>"
    )


def empty_state(title: str, body: str) -> str:
    return (
        f'<div style="border:1px dashed rgba(255,255,255,0.08);'
        f'border-radius:{_RADIUS_SM};padding:20px 16px;text-align:center;margin:4px 0;">'
        f'<div style="font-size:0.84em;color:{_TEXT_SEC};font-weight:600;margin-bottom:4px;">{title}</div>'
        f'<div style="font-size:0.75em;color:{_TEXT_CAP};line-height:1.5;">{body}</div>'
        f"</div>"
    )


def funnel_bar(
    stage: str, count: int, max_count: int, color: str, note: str = ""
) -> str:
    bar_w = max(2, int(count / max_count * 100)) if max_count else 2
    note_html = (
        f'<span style="color:{_TEXT_CAP};font-size:0.78em;margin-left:5px;">{note}</span>'
        if note
        else ""
    )
    return (
        f'<div style="margin-bottom:4px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f"padding:3px 8px;background:rgba(0,0,0,0.15);border-left:3px solid {color};"
        f'border-radius:0 4px 4px 0;">'
        f'<span style="color:{color};font-size:0.78em;">{stage}{note_html}</span>'
        f'<strong style="color:{color};font-size:0.82em;">{count}</strong>'
        f"</div>"
        f'<div style="height:2px;width:{bar_w}%;background:{color};opacity:0.30;'
        f'border-radius:0 0 2px 2px;"></div>'
        f"</div>"
    )


# ── Card molecules ─────────────────────────────────────────────────────────────


def hero_card(
    title: str,
    primary_value: str,
    stats: list,
    subtitle: str,
    gradient: bool = False,
) -> str:
    """
    Most visually prominent card.
    stats = [(label, value, color_or_None), ...]  — 3–4 items
    gradient = True adds violet/magenta hero treatment
    """
    bg = _BG_HERO if gradient else _BG_CARD_HI
    border = _BORDER_HERO if gradient else _BORDER
    shadow = _SHADOW_HERO if gradient else _SHADOW

    rows = "".join(
        f'<div style="margin-bottom:10px;">'
        f'<div style="font-size:0.67em;color:{_TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.09em;margin-bottom:2px;">{lbl}</div>'
        f'<div style="font-size:1.0em;font-weight:700;color:{c if c else _TEXT_PRI};">{val}</div>'
        f"</div>"
        for lbl, val, c in stats
    )

    return (
        f'<div style="background:{bg};border:1px solid {border};border-radius:{_RADIUS};'
        f'padding:22px 24px;box-shadow:{shadow};height:100%;box-sizing:border-box;">'
        f'<div style="font-size:0.68em;color:{_TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.11em;margin-bottom:10px;">{title}</div>'
        f'<div style="font-size:2.15em;font-weight:800;color:{_TEXT_PRI};line-height:1.05;'
        f'margin-bottom:16px;">{primary_value}</div>'
        f'<div style="border-top:1px solid {border};padding-top:14px;">{rows}</div>'
        f'<div style="font-size:0.70em;color:{_TEXT_CAP};margin-top:8px;line-height:1.5;">'
        f"{subtitle}</div>"
        f"</div>"
    )


def summary_card(
    title: str,
    primary_value: str,
    chip_label: str,
    chip_status: str,
    explanation: str,
) -> str:
    c_chip = chip(chip_label, chip_status)
    accent = _CHIP.get(chip_status, (C_NEUTRAL,))[0]
    return (
        f'<div style="background:{_BG_CARD};border:1px solid {_BORDER};'
        f"border-top:2px solid {accent};border-radius:{_RADIUS};"
        f'padding:20px 22px;box-shadow:{_SHADOW};height:100%;box-sizing:border-box;">'
        f'<div style="font-size:0.68em;color:{_TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.11em;margin-bottom:10px;">{title}</div>'
        f'<div style="font-size:1.8em;font-weight:800;color:{_TEXT_PRI};line-height:1.1;'
        f'margin-bottom:10px;">{primary_value}</div>'
        f'<div style="margin-bottom:12px;">{c_chip}</div>'
        f'<div style="font-size:0.75em;color:{_TEXT_SEC};line-height:1.55;">{explanation}</div>'
        f"</div>"
    )


def detail_card(
    title: str,
    subtitle: str,
    body_html: str,
    footer: str = "",
) -> str:
    footer_part = (
        f'<div style="border-top:1px solid {_BORDER};padding-top:8px;margin-top:8px;'
        f'font-size:0.69em;color:{_TEXT_CAP};">{footer}</div>'
        if footer
        else ""
    )
    return (
        f'<div style="background:{_BG_CARD};border:1px solid {_BORDER};border-radius:{_RADIUS};'
        f'padding:16px 20px;box-shadow:{_SHADOW};margin-bottom:12px;">'
        f'<div style="font-size:0.68em;color:{_TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.11em;margin-bottom:3px;">{title}</div>'
        f'<div style="font-size:0.75em;color:{_TEXT_CAP};margin-bottom:10px;line-height:1.4;">'
        f"{subtitle}</div>"
        f'<div style="border-top:1px solid {_BORDER};padding-top:10px;">{body_html}</div>'
        f"{footer_part}"
        f"</div>"
    )


def position_card(
    symbol: str,
    direction: str,
    pnl: float,
    entry: float,
    current: float,
    stop_pct: float,
    setup: str,
    risk_note: str,
    age: str,
    direction_label: str | None = None,
) -> str:
    is_long = direction.upper() == "LONG"
    dir_color = C_GREEN if is_long else C_RED
    dir_label = direction_label or ("▲ LONG" if is_long else "▼ SHORT")
    pnl_color = C_GREEN if pnl >= 0 else C_RED
    pnl_sign = "+" if pnl >= 0 else ""
    move_pct = ((current - entry) / entry * 100) if entry else 0
    if not is_long:
        move_pct = -move_pct
    mv_color = C_GREEN if move_pct >= 0 else C_RED
    mv_sign = "+" if move_pct >= 0 else ""

    setup_html = (
        f'<div style="font-size:0.71em;color:{_TEXT_CAP};margin-top:4px;">{setup}</div>'
        if setup
        else ""
    )
    risk_html = (
        f'<div style="font-size:0.70em;color:{C_RED};margin-top:2px;">{risk_note}</div>'
        if risk_note
        else ""
    )

    return (
        f'<div style="background:{_BG_CARD};border:1px solid {_BORDER};border-radius:{_RADIUS_SM};'
        f'padding:14px 16px;margin-bottom:8px;border-left:3px solid {dir_color};">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'margin-bottom:8px;">'
        f"<div>"
        f'<span style="font-weight:800;color:{_TEXT_PRI};font-size:1.05em;">{symbol}</span>&nbsp;'
        f'<span style="color:{dir_color};font-size:0.75em;font-weight:700;">{dir_label}</span>'
        f'&nbsp;&nbsp;<span style="font-size:0.70em;color:{_TEXT_CAP};">{age}</span>'
        f"</div>"
        f'<div style="text-align:right;">'
        f'<div style="font-size:1.1em;font-weight:800;color:{pnl_color};">{pnl_sign}${abs(pnl):.2f}</div>'
        f'<div style="font-size:0.70em;color:{mv_color};">{mv_sign}{abs(move_pct):.2f}%</div>'
        f"</div>"
        f"</div>"
        f'<div style="display:flex;gap:16px;font-size:0.74em;color:{_TEXT_SEC};margin-bottom:4px;">'
        f'<span>Entry <strong style="color:{_TEXT_PRI};">${entry:,.4g}</strong></span>'
        f'<span>Now <strong style="color:{_TEXT_PRI};">${current:,.4g}</strong></span>'
        f'<span>Stop risk <strong style="color:{C_RED};">−{stop_pct:.1f}%</strong></span>'
        f"</div>"
        f"{setup_html}{risk_html}"
        f"</div>"
    )


def risk_bar(
    label: str,
    entry: float,
    stop: float,
    target: float,
) -> str:
    lo = min(stop, entry, target)
    hi = max(stop, entry, target)
    span = hi - lo if hi != lo else 1.0

    def _p(v):
        return max(2, min(97, int((v - lo) / span * 100)))

    ep, sp, tp = _p(entry), _p(stop), _p(target)
    stop_l = min(ep, sp)
    tgt_l = min(ep, tp)
    stop_w = abs(ep - sp)
    tgt_w = abs(tp - ep)

    return (
        f'<div style="margin-bottom:10px;font-size:0.78em;">'
        f'<div style="color:{_TEXT_SEC};margin-bottom:6px;font-size:0.88em;font-weight:600;">{label}</div>'
        f'<div style="position:relative;height:8px;background:rgba(255,255,255,0.06);'
        f'border-radius:4px;margin-bottom:6px;overflow:hidden;">'
        f'<div style="position:absolute;left:{stop_l}%;width:{stop_w}%;height:100%;'
        f'background:{C_RED};opacity:0.35;"></div>'
        f'<div style="position:absolute;left:{tgt_l}%;width:{tgt_w}%;height:100%;'
        f'background:{C_GREEN};opacity:0.35;"></div>'
        f'<div style="position:absolute;left:{ep}%;top:-2px;width:3px;height:12px;'
        f'background:{C_CYAN};border-radius:2px;transform:translateX(-50%);"></div>'
        f'<div style="position:absolute;left:{sp}%;top:-2px;width:3px;height:12px;'
        f'background:{C_RED};border-radius:2px;transform:translateX(-50%);"></div>'
        f'<div style="position:absolute;left:{tp}%;top:-2px;width:3px;height:12px;'
        f'background:{C_GREEN};border-radius:2px;transform:translateX(-50%);"></div>'
        f"</div>"
        f'<div style="display:flex;justify-content:space-between;color:{_TEXT_CAP};font-size:0.82em;">'
        f'<span style="color:{C_RED};">Stop ${stop:,.4g}</span>'
        f'<span style="color:{C_CYAN};">Entry ${entry:,.4g}</span>'
        f'<span style="color:{C_GREEN};">Target ${target:,.4g}</span>'
        f"</div>"
        f"</div>"
    )
