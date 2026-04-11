from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

PACIFIC_TZ = ZoneInfo("US/Pacific")

SIGMA_X_LABEL = r"$\sigma_x\ (\mu\mathrm{m})$"
SIGMA_Y_LABEL = r"$\sigma_y\ (\mu\mathrm{m})$"
SIGMA_Z_LABEL = r"$\sigma_z\ (\mu\mathrm{m})$"
EMIT_X_LABEL = r"$\epsilon_{n,x}\ (\mu\mathrm{m}\cdot\mathrm{rad})$"
EMIT_Y_LABEL = r"$\epsilon_{n,y}\ (\mu\mathrm{m}\cdot\mathrm{rad})$"
EMIT_LABEL = r"$\epsilon_n\ (\mu\mathrm{m}\cdot\mathrm{rad})$"
UM_LABEL = r"$\mu\mathrm{m}$"
BETA_LABEL = r"$\beta$"
BETA_X_LABEL = r"$\beta_x$"
BETA_Y_LABEL = r"$\beta_y$"


@dataclass(frozen=True)
class VisibilitySettings:
    show_sigma_x: bool = True
    show_sigma_y: bool = True
    show_sigma_z: bool = True
    show_emit_x: bool = True
    show_emit_y: bool = True
    show_twiss_a_beta: bool = True
    show_twiss_b_beta: bool = True


class BeamDashboard:
    DARK = "#0d1117"
    PANEL = "#161b22"
    GRID = "#30363d"
    TXT = "#c9d1d9"
    BLUE = "#58a6ff"
    CORAL = "#f78166"
    GOLD = "#e3b341"
    GREEN = "#3fb950"
    PURPLE = "#d2a8ff"
    CYAN = "#79c0ff"
    IMAGE_POWER_GAMMA = 0.55
    IMAGE_PERCENTILE_LOW = 2.0
    IMAGE_PERCENTILE_HIGH = 99.7
    IMAGE_SCALE_WARMUP_FRAMES = 4
    # Rolling time-window width for the scalar timeseries plot (seconds).
    TIMESERIES_WINDOW_SECONDS = 120.0
    # Rolling window for index/numeric x-axis mode (number of most recent points).
    TIMESERIES_WINDOW_POINTS = 30
    # Hard cap on history length to avoid unbounded memory growth.
    MAX_HISTORY_POINTS = 2000

    def __init__(self, app_title: str):
        self.app_title = app_title
        self.x_axis_mode = "value"
        self.x_axis_label = ""
        self.x_axis_unit = ""
        self.screen_label = "OTR4"
        self.image_scale_state = {
            "mode": None,
            "sample_count": 0,
            "frozen_vmin": None,
            "frozen_vmax": None,
        }
        self.history_data = {
            "x": [],
            "xrms": [],
            "yrms": [],
            "sigmaz": [],
            "emx": [],
            "emy": [],
        }
        self.visibility = VisibilitySettings()
        self._build_figure()
        self.reset("OTR4", "Scan value", "", "value")

    def _build_figure(self) -> None:
        self.fig = plt.figure(figsize=(14, 9), facecolor=self.DARK)
        gs = gridspec.GridSpec(
            2,
            2,
            figure=self.fig,
            hspace=0.40,
            wspace=0.28,
            left=0.06,
            right=0.97,
            top=0.93,
            bottom=0.09,
        )

        self.ax_img = self.fig.add_subplot(gs[0, 0])
        self._style_ax(self.ax_img)
        self.image_artist = self.ax_img.imshow(
            np.zeros((2, 2)),
            cmap="inferno",
            aspect="auto",
            origin="upper",
            interpolation="nearest",
            norm=mcolors.PowerNorm(
                gamma=self.IMAGE_POWER_GAMMA,
                vmin=0.0,
                vmax=1.0,
                clip=True,
            ),
            visible=False,
        )
        self.colorbar = self.fig.colorbar(
            self.image_artist, ax=self.ax_img, fraction=0.046, pad=0.04
        )
        self.colorbar.ax.tick_params(colors=self.TXT, labelsize=7)
        self.colorbar.formatter = ticker.ScalarFormatter(useMathText=True)
        self.colorbar.formatter.set_scientific(True)
        self.colorbar.formatter.set_powerlimits((0, 0))
        self.colorbar.update_ticks()
        self.image_placeholder = self.ax_img.text(
            0.5,
            0.5,
            "Waiting for first shot...",
            ha="center",
            va="center",
            color=self.TXT,
            transform=self.ax_img.transAxes,
            fontsize=11,
        )
        self.ax_img.set_xlabel("x  (pixel)", fontsize=8)
        self.ax_img.set_ylabel("y  (pixel)", fontsize=8)
        self.ax_img.tick_params(labelsize=7)

        self.ax_ps = self.fig.add_subplot(gs[0, 1])
        self._style_ax(self.ax_ps)
        self.scatter_artist = self.ax_ps.scatter(
            [], [], s=0.8, alpha=0.35, color=self.BLUE, rasterized=True
        )
        self.scatter_placeholder = self.ax_ps.text(
            0.5,
            0.5,
            "Waiting for first shot...",
            ha="center",
            va="center",
            color=self.TXT,
            transform=self.ax_ps.transAxes,
            fontsize=11,
        )
        self.ax_ps.set_xlabel(f"x  ({UM_LABEL})", fontsize=8)
        self.ax_ps.set_ylabel("px  (eV/c)", fontsize=8)

        self.ax_ts = self.fig.add_subplot(gs[1, 0])
        self._style_ax(self.ax_ts)
        self.line_x = self.ax_ts.plot(
            [], [], color=self.BLUE, lw=1.8, marker="o", ms=5, label=SIGMA_X_LABEL
        )[0]
        self.line_y = self.ax_ts.plot(
            [], [], color=self.CORAL, lw=1.8, marker="s", ms=5, label=SIGMA_Y_LABEL
        )[0]
        self.line_z = self.ax_ts.plot(
            [], [], color=self.GOLD, lw=1.8, marker="D", ms=4, label=SIGMA_Z_LABEL
        )[0]
        self.vline = self.ax_ts.axvline(
            0.0, color="white", lw=1.0, alpha=0.5, linestyle=":"
        )
        self.vline.set_visible(False)
        self.ax_em = self.ax_ts.twinx()
        self.ax_em.set_facecolor(self.PANEL)
        self.ax_em.tick_params(colors=self.TXT, labelsize=8)
        self.ax_em.yaxis.label.set_color(self.TXT)
        for spine in self.ax_em.spines.values():
            spine.set_color(self.GRID)
        self.line_emx = self.ax_em.plot(
            [],
            [],
            color=self.GREEN,
            lw=1.8,
            marker="^",
            ms=5,
            linestyle="--",
            label=EMIT_X_LABEL,
        )[0]
        self.line_emy = self.ax_em.plot(
            [],
            [],
            color=self.PURPLE,
            lw=1.8,
            marker="v",
            ms=5,
            linestyle="--",
            label=EMIT_Y_LABEL,
        )[0]
        self.ax_em.set_ylabel(EMIT_LABEL, fontsize=8)
        self.timeseries_placeholder = self.ax_ts.text(
            0.5,
            0.5,
            "Scalar history will appear here after the first shot...",
            ha="center",
            va="center",
            color=self.TXT,
            transform=self.ax_ts.transAxes,
            fontsize=11,
        )

        self.ax_twiss = self.fig.add_subplot(gs[1, 1])
        self._style_ax(self.ax_twiss)
        self.line_twiss_a = self.ax_twiss.plot(
            [], [], color=self.CYAN, lw=2.0, label=BETA_X_LABEL
        )[0]
        self.line_twiss_b = self.ax_twiss.plot(
            [], [], color=self.GOLD, lw=2.0, label=BETA_Y_LABEL
        )[0]
        self.ax_twiss.set_xlabel("s  (m)", fontsize=8)
        self.ax_twiss.set_ylabel(BETA_LABEL + "  (m)", fontsize=8)
        self.twiss_placeholder = self.ax_twiss.text(
            0.5,
            0.5,
            "Twiss parameters will appear here after the first shot...",
            ha="center",
            va="center",
            color=self.TXT,
            transform=self.ax_twiss.transAxes,
            fontsize=11,
        )

        self.title_text = self.fig.suptitle(
            f"{self.app_title}  ·  idle", color=self.TXT, fontsize=11, y=0.98
        )

    def _style_ax(self, ax, title: str = "") -> None:
        ax.set_facecolor(self.PANEL)
        for spine in ax.spines.values():
            spine.set_color(self.GRID)
        ax.tick_params(colors=self.TXT, labelsize=8)
        ax.xaxis.label.set_color(self.TXT)
        ax.yaxis.label.set_color(self.TXT)
        if title:
            ax.set_title(title, color=self.TXT, fontsize=9, pad=6)
        ax.grid(color=self.GRID, linewidth=0.5, linestyle="--", alpha=0.6)

    def set_visibility(self, settings: VisibilitySettings) -> None:
        self.visibility = settings
        self.line_x.set_visible(settings.show_sigma_x)
        self.line_y.set_visible(settings.show_sigma_y)
        self.line_z.set_visible(settings.show_sigma_z)
        self.line_emx.set_visible(settings.show_emit_x)
        self.line_emy.set_visible(settings.show_emit_y)
        self.line_twiss_a.set_visible(settings.show_twiss_a_beta)
        self.line_twiss_b.set_visible(settings.show_twiss_b_beta)
        self._refresh_scalar_legend()
        self._refresh_twiss_legend()
        self._draw()

    def reset(
        self,
        screen_label: str,
        x_axis_label: str,
        x_axis_unit: str,
        x_axis_mode: str,
        image_placeholder: str = "Waiting for first shot...",
        clear_history: bool = True,
    ) -> None:
        self.screen_label = screen_label
        self.x_axis_label = x_axis_label
        self.x_axis_unit = x_axis_unit
        self.x_axis_mode = x_axis_mode
        if clear_history:
            for values in self.history_data.values():
                values.clear()

        self.image_artist.set_visible(False)
        self.image_artist.set_data(np.zeros((2, 2)))
        self.image_placeholder.set_visible(True)
        self.image_placeholder.set_text(image_placeholder)
        self.ax_img.set_title(f"{screen_label} Beam Image", color=self.TXT, fontsize=9, pad=6)
        self.ax_img.set_xlabel("", fontsize=8)

        self.scatter_artist.set_offsets(np.empty((0, 2)))
        self.scatter_placeholder.set_visible(True)
        self.scatter_placeholder.set_text("Waiting for first shot...")
        self.ax_ps.set_title(
            f"Beam Phase-Space  x - px at {screen_label}",
            color=self.TXT,
            fontsize=9,
            pad=6,
        )

        self.line_x.set_data([], [])
        self.line_y.set_data([], [])
        self.line_z.set_data([], [])
        self.line_emx.set_data([], [])
        self.line_emy.set_data([], [])
        self.line_twiss_a.set_data([], [])
        self.line_twiss_b.set_data([], [])
        self.vline.set_visible(False)
        self.timeseries_placeholder.set_visible(True)
        self.twiss_placeholder.set_visible(True)
        self.title_text.set_text(f"{self.app_title}  ·  idle")

        self.ax_ts.set_title(
            self._timeseries_title(screen_label, x_axis_label, x_axis_mode),
            color=self.TXT,
            fontsize=9,
            pad=6,
        )
        self.ax_ts.set_ylabel(f"RMS beam size  ({UM_LABEL})", fontsize=8)
        self.ax_em.set_ylabel(f"Norm. emittance  ({UM_LABEL}.rad)", fontsize=8)
        self._configure_x_axis()

        self.ax_ts.set_xlim(-1.0, 1.0)
        self.ax_ts.set_ylim(-1.0, 1.0)
        self.ax_em.set_ylim(-1.0, 1.0)
        self.ax_ps.set_xlim(-1.0, 1.0)
        self.ax_ps.set_ylim(-1.0, 1.0)
        self.ax_twiss.set_xlim(-1.0, 1.0)
        self.ax_twiss.set_ylim(-1.0, 1.0)

        self._reset_image_scale_state()
        self._set_image_norm(0.0, 1.0, "robust")
        self.set_visibility(self.visibility)

    def update(self, frame, image_scale_mode: str) -> None:
        if frame.image is not None:
            self.image_placeholder.set_visible(False)
            self.image_artist.set_visible(True)
            display_image = np.asarray(frame.image, dtype=float)
            self.image_artist.set_data(display_image)
            self._update_image_scale(display_image, image_scale_mode)
        else:
            self.image_artist.set_visible(False)
            self.image_placeholder.set_visible(True)
            if frame.image_message:
                self.image_placeholder.set_text(frame.image_message)
        self.ax_img.set_xlabel(frame.image_caption, fontsize=8)

        if frame.beam_x_um is not None and frame.beam_px_evc is not None and len(frame.beam_x_um) > 0:
            self.scatter_placeholder.set_visible(False)
            offsets = np.column_stack((frame.beam_x_um, frame.beam_px_evc))
            self.scatter_artist.set_offsets(offsets)
            xpad = max((float(np.max(frame.beam_x_um)) - float(np.min(frame.beam_x_um))) * 0.08, 1.0)
            ypad = max((float(np.max(frame.beam_px_evc)) - float(np.min(frame.beam_px_evc))) * 0.08, 1.0)
            self.ax_ps.set_xlim(
                float(np.min(frame.beam_x_um)) - xpad,
                float(np.max(frame.beam_x_um)) + xpad,
            )
            self.ax_ps.set_ylim(
                float(np.min(frame.beam_px_evc)) - ypad,
                float(np.max(frame.beam_px_evc)) + ypad,
            )

        self.history_data["x"].append(frame.x_axis_value)
        self.history_data["xrms"].append(frame.xrms_um)
        self.history_data["yrms"].append(frame.yrms_um)
        self.history_data["sigmaz"].append(frame.sigma_z_um)
        self.history_data["emx"].append(frame.norm_emit_x_um_rad)
        self.history_data["emy"].append(frame.norm_emit_y_um_rad)
        # Trim to hard cap so history doesn't grow unboundedly.
        if len(self.history_data["x"]) > self.MAX_HISTORY_POINTS:
            for key in self.history_data:
                self.history_data[key] = self.history_data[key][-self.MAX_HISTORY_POINTS:]

        self.timeseries_placeholder.set_visible(False)
        x_values = self.history_data["x"]
        self.line_x.set_data(x_values, self.history_data["xrms"])
        self.line_y.set_data(x_values, self.history_data["yrms"])
        self.line_z.set_data(x_values, self.history_data["sigmaz"])
        self.line_emx.set_data(x_values, self.history_data["emx"])
        self.line_emy.set_data(x_values, self.history_data["emy"])
        self.vline.set_xdata([frame.x_axis_value, frame.x_axis_value])
        self.vline.set_visible(True)

        self._update_timeseries_limits()

        if (
            frame.twiss_s is not None
            and frame.twiss_a_beta is not None
            and frame.twiss_b_beta is not None
            and len(frame.twiss_s) == len(frame.twiss_a_beta) == len(frame.twiss_b_beta)
        ):
            self.twiss_placeholder.set_visible(False)
            self.line_twiss_a.set_data(frame.twiss_s, frame.twiss_a_beta)
            self.line_twiss_b.set_data(frame.twiss_s, frame.twiss_b_beta)
            self.ax_twiss.set_xlim(*self._pad_numeric_bounds(frame.twiss_s, minimum=0.5))
            self.ax_twiss.set_ylim(
                *self._pad_numeric_bounds(
                    list(frame.twiss_a_beta) + list(frame.twiss_b_beta),
                    minimum=0.1,
                )
            )

        if frame.title_suffix:
            self.title_text.set_text(f"{self.app_title}  ·  {frame.title_suffix}")
        else:
            self.title_text.set_text(f"{self.app_title}")
        self._draw()

    def _configure_x_axis(self) -> None:
        if self.x_axis_mode == "time":
            self.ax_ts.xaxis.set_major_formatter(
                mdates.DateFormatter("%I:%M:%S %p", tz=PACIFIC_TZ)
            )
            self.ax_ts.tick_params(axis='x', labelrotation=30)
            self.ax_ts.set_xlabel("Time (Pacific)", fontsize=8)
        else:
            unit_suffix = f"  ({self.x_axis_unit})" if self.x_axis_unit else ""
            self.ax_ts.set_xlabel(f"{self.x_axis_label}{unit_suffix}", fontsize=8)
            self.ax_ts.xaxis.set_major_formatter(ticker.ScalarFormatter())

    def _update_timeseries_limits(self) -> None:
        if self.x_axis_mode == "time":
            # Rolling window: always show the most recent TIMESERIES_WINDOW_SECONDS.
            now = datetime.now(tz=PACIFIC_TZ)
            window = timedelta(seconds=self.TIMESERIES_WINDOW_SECONDS)
            x_left = now - window
            self.ax_ts.set_xlim(x_left, now)
            # Compute y limits only from points inside the visible window.
            xs = self.history_data["x"]
            in_window = [i for i, xv in enumerate(xs) if xv >= x_left]
            def _win_t(key):
                d = self.history_data[key]
                return [d[i] for i in in_window]
            self.ax_ts.set_ylim(
                *self._pad_numeric_bounds(_win_t("xrms") + _win_t("yrms") + _win_t("sigmaz"), minimum=5.0)
            )
            self.ax_em.set_ylim(
                *self._pad_numeric_bounds(_win_t("emx") + _win_t("emy"), minimum=0.05)
            )
        else:
            # Rolling window: show last TIMESERIES_WINDOW_POINTS evaluations,
            # scrolling left as new points arrive.
            xs = self.history_data["x"]
            n = len(xs)
            win_start = max(0, n - self.TIMESERIES_WINDOW_POINTS)
            if n > 1:
                lo = float(xs[win_start])
                hi = float(xs[-1])
                span = hi - lo if hi != lo else 1.0
                self.ax_ts.set_xlim(lo - span * 0.05, hi + span * 0.15)
            else:
                self.ax_ts.set_xlim(*self._pad_numeric_bounds([float(x) for x in xs] if xs else [0.0], minimum=0.5))
            def _win_n(key):
                return self.history_data[key][win_start:]
            self.ax_ts.set_ylim(
                *self._pad_numeric_bounds(_win_n("xrms") + _win_n("yrms") + _win_n("sigmaz"), minimum=5.0)
            )
            self.ax_em.set_ylim(
                *self._pad_numeric_bounds(_win_n("emx") + _win_n("emy"), minimum=0.05)
            )

    def _timeseries_title(self, screen_label: str, x_axis_label: str, x_axis_mode: str) -> str:
        if x_axis_mode == "time":
            return f"Scalar Diagnostics vs Time at {screen_label}"
        return f"Scalar Diagnostics vs {x_axis_label} at {screen_label}"

    def _reset_image_scale_state(self) -> None:
        self.image_scale_state["mode"] = None
        self.image_scale_state["sample_count"] = 0
        self.image_scale_state["frozen_vmin"] = None
        self.image_scale_state["frozen_vmax"] = None

    def _compute_image_bounds(self, display_image: np.ndarray) -> tuple[float, float]:
        finite = display_image[np.isfinite(display_image)]
        if finite.size == 0:
            return (0.0, 1.0)

        positive = finite[finite > 0.0]
        if positive.size > 0:
            low = 0.0
            high = float(np.percentile(positive, self.IMAGE_PERCENTILE_HIGH))
            high = max(high, float(np.max(positive)))
        else:
            low = float(np.percentile(finite, self.IMAGE_PERCENTILE_LOW))
            high = float(np.percentile(finite, self.IMAGE_PERCENTILE_HIGH))
            low = max(low, 0.0)

        if high <= low:
            high = low + max(max(abs(low), abs(high)) * 0.1, 1e-18)
        return (low, max(high, low + 1e-18))

    def _set_image_norm(self, vmin: float, vmax: float, mode: str) -> None:
        if mode == "robust":
            norm = mcolors.PowerNorm(
                gamma=self.IMAGE_POWER_GAMMA,
                vmin=vmin,
                vmax=vmax,
                clip=True,
            )
        else:
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
        self.image_artist.set_norm(norm)
        self.colorbar.update_normal(self.image_artist)
        self.colorbar.update_ticks()

    def _update_image_scale(self, display_image: np.ndarray, mode: str) -> None:
        if self.image_scale_state["mode"] != mode:
            self._reset_image_scale_state()
            self.image_scale_state["mode"] = mode
        current_vmin, current_vmax = self._compute_image_bounds(display_image)
        if mode == "auto":
            self._set_image_norm(current_vmin, current_vmax, mode)
            return
        if self.image_scale_state["sample_count"] < self.IMAGE_SCALE_WARMUP_FRAMES:
            frozen_vmin = self.image_scale_state["frozen_vmin"]
            frozen_vmax = self.image_scale_state["frozen_vmax"]
            self.image_scale_state["frozen_vmin"] = current_vmin if frozen_vmin is None else min(frozen_vmin, current_vmin)
            self.image_scale_state["frozen_vmax"] = current_vmax if frozen_vmax is None else max(frozen_vmax, current_vmax)
            self.image_scale_state["sample_count"] += 1
        self._set_image_norm(
            self.image_scale_state["frozen_vmin"],
            self.image_scale_state["frozen_vmax"],
            mode,
        )

    def _refresh_scalar_legend(self) -> None:
        handles = []
        labels = []
        for line, label in [
            (self.line_x, SIGMA_X_LABEL),
            (self.line_y, SIGMA_Y_LABEL),
            (self.line_z, SIGMA_Z_LABEL),
            (self.line_emx, EMIT_X_LABEL),
            (self.line_emy, EMIT_Y_LABEL),
        ]:
            if line.get_visible():
                handles.append(line)
                labels.append(label)
        legend = self.ax_ts.get_legend()
        if legend is not None:
            legend.remove()
        if handles:
            self.ax_ts.legend(
                handles,
                labels,
                loc="upper right",
                fontsize=7,
                facecolor=self.DARK,
                edgecolor=self.GRID,
                labelcolor=self.TXT,
            )

    def _refresh_twiss_legend(self) -> None:
        handles = []
        labels = []
        for line, label in [
            (self.line_twiss_a, BETA_X_LABEL),
            (self.line_twiss_b, BETA_Y_LABEL),
        ]:
            if line.get_visible():
                handles.append(line)
                labels.append(label)
        legend = self.ax_twiss.get_legend()
        if legend is not None:
            legend.remove()
        if handles:
            self.ax_twiss.legend(
                handles,
                labels,
                loc="upper right",
                fontsize=7,
                facecolor=self.DARK,
                edgecolor=self.GRID,
                labelcolor=self.TXT,
            )

    def _pad_numeric_bounds(self, values, fraction: float = 0.08, minimum: float = 1.0):
        if len(values) == 0:
            return (-1.0, 1.0)
        vmin = float(np.min(values))
        vmax = float(np.max(values))
        if np.isclose(vmin, vmax):
            pad = max(abs(vmin) * fraction, minimum)
            return (vmin - pad, vmax + pad)
        pad = max((vmax - vmin) * fraction, minimum)
        return (vmin - pad, vmax + pad)

    def _pad_time_bounds(self, values):
        if len(values) == 0:
            now = datetime.now(tz=PACIFIC_TZ)
            return (now - timedelta(seconds=1), now + timedelta(seconds=1))
        vmin = min(values)
        vmax = max(values)
        if vmin == vmax:
            pad = timedelta(seconds=5)
            return (vmin - pad, vmax + pad)
        pad = max((vmax - vmin) / 12, timedelta(seconds=1))
        return (vmin - pad, vmax + pad)

    def _draw(self) -> None:
        self.fig.canvas.draw_idle()
        if hasattr(self.fig.canvas, "flush_events"):
            self.fig.canvas.flush_events()
