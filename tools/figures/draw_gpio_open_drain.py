#!/usr/bin/env python3
"""Draw an explanatory GPIO open-drain/I2C diagram."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle


matplotlib.rcParams["font.sans-serif"] = [
    "STHeiti",
    "PingFang HK",
    "Arial Unicode MS",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
]
matplotlib.rcParams["axes.unicode_minus"] = True

BLUE = "#2563EB"
RED = "#DC2626"
GREEN = "#16A34A"
GRAY = "#475569"
LIGHT = "#F8FAFC"
WIRE = "#0F172A"


def box(ax, x, y, w, h, text, color=BLUE, size=12):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.04",
        facecolor="white", edgecolor=color, linewidth=2,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=size, color=WIRE)


def resistor(ax, x, y_top, y_bottom):
    points = [(x, y_top)]
    segments = 7
    dy = (y_top - y_bottom) / (segments + 1)
    for index in range(1, segments + 1):
        dx = 0.07 if index % 2 else -0.07
        points.append((x + dx, y_top - index * dy))
    points.append((x, y_bottom))
    xs, ys = zip(*points)
    ax.plot(xs, ys, color=WIRE, linewidth=2)


def transistor(ax, x, y, conducting):
    color = GREEN if conducting else GRAY
    ax.add_patch(Circle((x, y), 0.18, facecolor="white", edgecolor=color,
                        linewidth=2))
    ax.plot([x, x], [y + 0.18, y + 0.38], color=color, linewidth=2)
    ax.plot([x, x], [y - 0.18, y - 0.38], color=color, linewidth=2)
    if conducting:
        ax.plot([x, x], [y - 0.11, y + 0.11], color=GREEN, linewidth=4)
    else:
        ax.plot([x - 0.08, x + 0.08], [y - 0.08, y + 0.08],
                color=RED, linewidth=3)
    ax.text(x + 0.28, y, "导通" if conducting else "关闭",
            va="center", color=color, fontsize=11)


def draw_state(ax, released):
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    title = "输出 1：释放线路" if released else "输出 0：主动拉低"
    ax.set_title(title, fontsize=15, fontweight="bold", pad=8)

    ax.text(2.0, 4.0, "3.3 V", ha="center", color=RED, fontsize=12,
            fontweight="bold")
    ax.plot([2.0, 2.0], [3.9, 3.65], color=RED, linewidth=2)
    resistor(ax, 2.0, 3.65, 2.85)
    ax.text(2.25, 3.25, "上拉电阻", va="center", fontsize=11)

    line_color = RED if released else GREEN
    ax.plot([0.55, 3.35], [2.65, 2.65], color=line_color, linewidth=4)
    ax.text(0.48, 2.65, "SDA/SCL", ha="right", va="center", fontsize=11)
    ax.text(3.42, 2.65, "高电平" if released else "低电平",
            va="center", color=line_color, fontsize=12, fontweight="bold")

    ax.plot([2.0, 2.0], [2.85, 2.65], color=WIRE, linewidth=2)
    ax.plot([2.0, 2.0], [2.65, 1.75], color=WIRE, linewidth=2)
    transistor(ax, 2.0, 1.38, conducting=not released)
    ax.plot([2.0, 2.0], [1.0, 0.62], color=WIRE, linewidth=2)
    ax.plot([1.65, 2.35], [0.62, 0.62], color=WIRE, linewidth=2)
    ax.plot([1.75, 2.25], [0.50, 0.50], color=WIRE, linewidth=2)
    ax.plot([1.88, 2.12], [0.38, 0.38], color=WIRE, linewidth=2)
    ax.text(2.0, 0.12, "GND", ha="center", fontsize=11)

    explanation = (
        "GPIO 不输出高电平\n线路由电阻拉到 3.3 V"
        if released else
        "GPIO 接通到 GND\n线路被拉到 0 V"
    )
    ax.text(0.28, 1.45, explanation, ha="left", va="center", fontsize=11,
            bbox=dict(boxstyle="round,pad=0.35", facecolor=LIGHT,
                      edgecolor="#CBD5E1"))


def draw_shared_bus(ax):
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4.3)
    ax.axis("off")
    ax.set_title("I²C 为什么使用开漏：多个设备可以安全共享同一条线",
                 fontsize=15, fontweight="bold", pad=8)

    ax.plot([1.0, 9.1], [2.35, 2.35], color=GREEN, linewidth=5)
    ax.text(9.25, 2.35, "总线为低电平", va="center", color=GREEN,
            fontsize=12, fontweight="bold")

    ax.text(5.0, 4.05, "3.3 V", ha="center", color=RED, fontsize=12,
            fontweight="bold")
    ax.plot([5.0, 5.0], [3.92, 3.72], color=RED, linewidth=2)
    resistor(ax, 5.0, 3.72, 2.55)
    ax.plot([5.0, 5.0], [2.55, 2.35], color=WIRE, linewidth=2)
    ax.text(5.25, 3.2, "公共上拉", fontsize=11, va="center")

    devices = [
        (1.5, "M4\n释放", False),
        (4.0, "MPU6050\n拉低", True),
        (6.5, "其他设备\n释放", False),
    ]
    for x, label, pulling in devices:
        box(ax, x - 0.65, 0.25, 1.3, 0.75, label,
            color=GREEN if pulling else BLUE, size=11)
        ax.plot([x, x], [1.0, 2.35], color=GREEN if pulling else GRAY,
                linewidth=3, linestyle="-" if pulling else "--")
        if pulling:
            arrow = Polygon(
                [(x - 0.10, 1.25), (x + 0.10, 1.25), (x, 1.08)],
                closed=True, facecolor=GREEN, edgecolor=GREEN,
            )
            ax.add_patch(arrow)

    ax.text(8.3, 0.65,
            "只要任一设备拉低，\n整条总线就是低电平；\n全部释放时才是高电平。",
            ha="center", va="center", fontsize=11,
            bbox=dict(boxstyle="round,pad=0.4", facecolor=LIGHT,
                      edgecolor="#CBD5E1"))


def main():
    output = Path(__file__).resolve().parents[2] / "assets" / "gpio_open_drain.png"
    figure = plt.figure(figsize=(14, 9), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=[1, 1.05], hspace=0.32,
                               wspace=0.16)
    draw_state(figure.add_subplot(grid[0, 0]), released=True)
    draw_state(figure.add_subplot(grid[0, 1]), released=False)
    draw_shared_bus(figure.add_subplot(grid[1, :]))
    figure.suptitle("GPIO 开漏输出与 I²C 总线原理", fontsize=20,
                    fontweight="bold", y=0.98)
    figure.text(0.5, 0.015,
                "本项目：M4 将 PB12(SDA) 与 PB13(SCL) 配置为开漏输出",
                ha="center", fontsize=12, color=GRAY)
    figure.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    print(output)


if __name__ == "__main__":
    main()
