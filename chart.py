"""
Genera docs/price-history-{light,dark}.svg desde data/history.csv:
el precio mas bajo CON STOCK de cada tienda a lo largo del tiempo.

    python chart.py         # SVGs para el README
    python chart.py --png   # ademas un PNG (para compartir en redes)
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from history import read_events  # noqa: E402

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "docs"

# Paleta categorica validada (orden fijo; el color sigue a la tienda, no a su rango).
STORE_ORDER = ["Exito", "Falabella", "Alkosto", "Ktronix", "Homecenter", "MercadoLibre"]
THEMES = {
    "light": {
        "surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"],
    },
    "dark": {
        "surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7", "grid": "#33332f",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"],
    },
}
STORE_LABELS = {"Exito": "Éxito"}
# Una tienda con menos de esto de stock acumulado se reporta como "sin stock".
MIN_AVAILABLE = timedelta(days=1)
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def cop_millions(v, _pos=None):
    return f"${v / 1e6:.1f} M".replace(".", ",")


def cop_full(v):
    return f"${v:,.0f}".replace(",", ".")


def build_series(events):
    """store -> [(datetime, precio minimo con stock o None)]"""
    current = {}  # (store, product_id) -> (price, in_stock)
    series = defaultdict(list)
    by_time = defaultdict(list)
    for e in events:
        by_time[e["timestamp"]].append(e)
    stores = sorted({e["store"] for e in events})
    for ts in sorted(by_time):
        for e in by_time[ts]:
            current[(e["store"], e["product_id"])] = (e["price"], e["in_stock"])
        when = datetime.fromisoformat(ts)
        for store in stores:
            prices = [p for (s, _), (p, ok) in current.items() if s == store and ok]
            value = min(prices) if prices else None
            points = series[store]
            if not points or points[-1][1] != value:
                points.append((when, value))
    return series


def available_time(points, end):
    total = timedelta()
    for (start, value), (stop, _) in zip(points, points[1:] + [(end, None)]):
        if value is not None:
            total += stop - start
    return total


def render(series, theme_name, end, fmt="svg"):
    t = THEMES[theme_name]
    plt.rcParams.update({
        "svg.hashsalt": "ps5-pro-watcher",  # ids estables -> diffs limpios en git
        "font.family": ["DejaVu Sans"],
        "font.size": 11,
    })
    fig, ax = plt.subplots(figsize=(10, 5), dpi=100)
    fig.patch.set_facecolor(t["surface"])
    ax.set_facecolor(t["surface"])

    plotted = [
        s for s in STORE_ORDER
        if s in series and available_time(series[s], end) >= MIN_AVAILABLE
    ]
    never = [s for s in STORE_ORDER if s in series and s not in plotted]

    for store in plotted:
        color = t["series"][STORE_ORDER.index(store)]
        pts = series[store] + [(end, series[store][-1][1])]
        xs = [p[0] for p in pts]
        ys = [float("nan") if p[1] is None else p[1] for p in pts]
        label = STORE_LABELS.get(store, store)
        ax.step(xs, ys, where="post", color=color, linewidth=2, label=label,
                solid_capstyle="round")
        last = ys[-1]
        if last == last:  # no es NaN: etiqueta directa al final de la linea
            ax.annotate(
                f"{label}  {cop_full(last)}", xy=(end, last), xytext=(8, 0),
                textcoords="offset points", va="center", color=t["text"], fontsize=10,
            )

    ax.yaxis.set_major_formatter(FuncFormatter(cop_millions))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda x, _p: (lambda d: f"{d.day} {MONTHS[d.month - 1]}")(mdates.num2date(x))
    ))
    ax.grid(axis="y", color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0, pad=6)

    fig.suptitle("PS5 Pro en Colombia: precio más bajo disponible por tienda",
                 x=0.1, ha="left", color=t["text"], fontsize=14, fontweight="bold")
    subtitle = "Solo productos con stock. Huecos = todo agotado en esa tienda."
    if never:
        names = " y ".join(STORE_LABELS.get(s, s) for s in never)
        subtitle += f"\n{names}: prácticamente sin stock en todo el período."
    ax.set_title(subtitle, loc="left", color=t["muted"], fontsize=10, pad=24)
    legend = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(plotted),
                       frameon=False, fontsize=10, borderaxespad=0.2, handlelength=1.5)
    for txt in legend.get_texts():
        txt.set_color(t["text"])

    fig.subplots_adjust(left=0.1, right=0.8, top=0.8, bottom=0.1)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"price-history-{theme_name}.{fmt}"
    if fmt == "svg":
        fig.savefig(out, format="svg", metadata={"Date": None}, facecolor=t["surface"])
    else:
        fig.savefig(out, format=fmt, dpi=200, facecolor=t["surface"])
    plt.close(fig)
    return out


def main():
    events = read_events()
    if not events:
        print("Sin historial todavia, no hay grafica que generar.")
        return
    series = build_series(events)
    # La linea se extiende hasta hoy (granularidad diaria: maximo un cambio de SVG por dia).
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    last_event = max(p[0] for pts in series.values() for p in pts)
    end = max(today, last_event)
    for theme in THEMES:
        print(render(series, theme, end))
    if "--png" in sys.argv:
        print(render(series, "light", end, fmt="png"))


if __name__ == "__main__":
    main()
