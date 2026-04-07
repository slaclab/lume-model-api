"""
live_stream_monitor.py
----------------------
Live digital-twin monitor with a real-time EPICS tab and an interactive
offline tab that produces a fake stream from manual inputs.

Run with:
    marimo edit lume_visualizations/live_stream_monitor.py
"""

import marimo

__generated_with = "0.22.0"

app = marimo.App(
    width="full",
    app_title="LUME Live Stream Monitor",
    css_file="live_stream_monitor.css",
    html_head_file="live_stream_monitor.head.html",
)


@app.cell
def imports():
    import asyncio
    import sys
    import warnings
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["timezone"] = "US/Pacific"
    import marimo as mo

    warnings.filterwarnings("ignore")

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from lume_visualizations.beam_monitor import StagedModelImageSource
    from lume_visualizations.config import (
        EPICS_INPUT_PVS,
        MANUAL_INPUT_PVS,
        SCREEN_CONFIGS,
        SCREEN_KEYS,
    )
    from lume_visualizations.dashboard import BeamDashboard, VisibilitySettings
    from lume_visualizations.epics_controls import EpicsInputProvider
    from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

    PACIFIC_TZ = ZoneInfo("US/Pacific")

    return (
        asyncio,
        datetime,
        EPICS_INPUT_PVS,
        FAKE_INPUT_SPECS,
        mo,
        MANUAL_INPUT_PVS,
        PACIFIC_TZ,
        SCREEN_CONFIGS,
        SCREEN_KEYS,
        BeamDashboard,
        EpicsInputProvider,
        StagedModelImageSource,
        VisibilitySettings,
    )


@app.cell
def header(mo):
    mo.md("# LUME Live Stream Monitor")


@app.cell
def source_setup(EpicsInputProvider, EPICS_INPUT_PVS, FAKE_INPUT_SPECS, StagedModelImageSource):
    source = StagedModelImageSource.create_default()
    provider = EpicsInputProvider()
    # Keep fake and real EPICS deployments on the same explicit PV contract.
    model_input_names = list(EPICS_INPUT_PVS)
    # Use FAKE_INPUT_SPECS defaults for initial slider positions — avoids an
    # expensive model.get() call and is robust to non-model EPICS inputs not
    # being model-writable variables.
    _default_map = {spec.pv_name: spec.default for spec in FAKE_INPUT_SPECS}
    initial_inputs = {name: float(_default_map.get(name, 0.0)) for name in model_input_names}
    return initial_inputs, model_input_names, provider, source


@app.cell
def live_dashboard_setup(BeamDashboard):
    live_dashboard = BeamDashboard("LUME Live Stream Monitor")
    return (live_dashboard,)


@app.cell
def interactive_dashboard_setup(BeamDashboard):
    interactive_dashboard = BeamDashboard("LUME Interactive Offline Monitor")
    return (interactive_dashboard,)


@app.cell
def live_controls(mo, SCREEN_KEYS):
    live_screen_dropdown = mo.ui.dropdown(
        options=SCREEN_KEYS, value="OTR4", label="Screen"
    )
    live_poll_period_slider = mo.ui.slider(
        start=0.2,
        stop=5.0,
        step=0.1,
        value=1.0,
        label="Poll period (s)",
        show_value=True,
    )
    live_image_scale_mode = mo.ui.dropdown(
        options=["robust", "fixed", "auto"],
        value="robust",
        label="Image scale",
    )
    live_show_sigma_x = mo.ui.checkbox(value=True, label="sigma_x")
    live_show_sigma_y = mo.ui.checkbox(value=True, label="sigma_y")
    live_show_sigma_z = mo.ui.checkbox(value=True, label="sigma_z")
    live_show_emit_x = mo.ui.checkbox(value=True, label="eps_n,x")
    live_show_emit_y = mo.ui.checkbox(value=True, label="eps_n,y")
    live_show_twiss_a_beta = mo.ui.checkbox(value=True, label="a.beta")
    live_show_twiss_b_beta = mo.ui.checkbox(value=True, label="b.beta")
    live_controls_ui = mo.vstack(
        [
            mo.hstack(
                [
                    live_screen_dropdown,
                    live_poll_period_slider,
                    live_image_scale_mode,
                ],
                gap="1.0rem",
                justify="start",
            ),
            mo.hstack(
                [
                    mo.md("**Show:**"),
                    live_show_sigma_x,
                    live_show_sigma_y,
                    live_show_sigma_z,
                    live_show_emit_x,
                    live_show_emit_y,
                    live_show_twiss_a_beta,
                    live_show_twiss_b_beta,
                ],
                gap="1.0",
                justify="start",
            ),
        ],
        gap="0.8rem",
    )
    return (
        live_controls_ui,
        live_image_scale_mode,
        live_poll_period_slider,
        live_screen_dropdown,
        live_show_emit_x,
        live_show_emit_y,
        live_show_sigma_x,
        live_show_sigma_y,
        live_show_sigma_z,
        live_show_twiss_a_beta,
        live_show_twiss_b_beta,
    )


@app.cell
def interactive_controls(mo, SCREEN_KEYS):
    interactive_screen_dropdown = mo.ui.dropdown(
        options=SCREEN_KEYS, value="OTR4", label="Screen"
    )
    interactive_image_scale_mode = mo.ui.dropdown(
        options=["robust", "fixed", "auto"],
        value="robust",
        label="Image scale",
    )
    interactive_show_sigma_x = mo.ui.checkbox(value=True, label="sigma_x")
    interactive_show_sigma_y = mo.ui.checkbox(value=True, label="sigma_y")
    interactive_show_sigma_z = mo.ui.checkbox(value=True, label="sigma_z")
    interactive_show_emit_x = mo.ui.checkbox(value=True, label="eps_n,x")
    interactive_show_emit_y = mo.ui.checkbox(value=True, label="eps_n,y")
    interactive_show_twiss_a_beta = mo.ui.checkbox(value=True, label="a.beta")
    interactive_show_twiss_b_beta = mo.ui.checkbox(value=True, label="b.beta")
    # All display controls in a single compact row above the dashboard
    interactive_controls_ui = mo.hstack(
        [
            interactive_screen_dropdown,
            interactive_image_scale_mode,
            mo.md("**Show:**"),
            interactive_show_sigma_x,
            interactive_show_sigma_y,
            interactive_show_sigma_z,
            interactive_show_emit_x,
            interactive_show_emit_y,
            interactive_show_twiss_a_beta,
            interactive_show_twiss_b_beta,
        ],
        gap="1.0",
        justify="start",
    )
    return (
        interactive_controls_ui,
        interactive_image_scale_mode,
        interactive_screen_dropdown,
        interactive_show_emit_x,
        interactive_show_emit_y,
        interactive_show_sigma_x,
        interactive_show_sigma_y,
        interactive_show_sigma_z,
        interactive_show_twiss_a_beta,
        interactive_show_twiss_b_beta,
    )


@app.cell
def interactive_slider_controls(
    FAKE_INPUT_SPECS,
    MANUAL_INPUT_PVS,
    initial_inputs,
    mo,
    set_interactive_eval_trigger,
    slider_display_values,
):
    slider_labels = {
        "SOLN:IN20:121:BCTRL": "SOLN:IN20:121:BCTRL",
        "QUAD:IN20:121:BCTRL": "QUAD:IN20:121:BCTRL",
        "QUAD:IN20:122:BCTRL": "QUAD:IN20:122:BCTRL",
        "ACCL:IN20:300:L0A_PDES": "ACCL:IN20:300:L0A_PDES",
        "ACCL:IN20:400:L0B_PDES": "ACCL:IN20:400:L0B_PDES",
        "QUAD:IN20:361:BCTRL": "QUAD:IN20:361:BCTRL",
        "QUAD:IN20:371:BCTRL": "QUAD:IN20:371:BCTRL",
        "QUAD:IN20:425:BCTRL": "QUAD:IN20:425:BCTRL",
        "QUAD:IN20:441:BCTRL": "QUAD:IN20:441:BCTRL",
        "QUAD:IN20:511:BCTRL": "QUAD:IN20:511:BCTRL",
        "QUAD:IN20:525:BCTRL": "QUAD:IN20:525:BCTRL",
    }
    slider_specs = {spec.pv_name: spec for spec in FAKE_INPUT_SPECS}
    # Resolve display overrides once (set by the "apply machine values" button).
    _display_vals = slider_display_values()

    interactive_sliders = {}
    slider_rows = []
    current_row = []
    for index, pv_name in enumerate(MANUAL_INPUT_PVS):
        spec = slider_specs[pv_name]
        _range = float(spec.maximum) - float(spec.minimum)
        _step = max(round(_range / 1000, 3), 0.001)
        slider = mo.ui.slider(
            start=float(spec.minimum),
            stop=float(spec.maximum),
            step=_step,
            value=round(float(_display_vals.get(pv_name, initial_inputs[pv_name])), 3),
            label=slider_labels.get(pv_name, pv_name),
            full_width=False,
            show_value=True,
            on_change=lambda v: set_interactive_eval_trigger(lambda x: x + 1),
        )
        interactive_sliders[pv_name] = slider
        current_row.append(slider)
        if len(current_row) == 4 or index == len(MANUAL_INPUT_PVS) - 1:
            slider_rows.append(
                mo.hstack(current_row, widths=None, gap="1rem")
            )
            current_row = []

    interactive_slider_controls_ui = mo.vstack(slider_rows, gap="0.3rem", justify="start", align="start")
    return interactive_slider_controls_ui, interactive_sliders


@app.cell
def apply_machine_values_button(mo):
    apply_machine_btn = mo.ui.run_button(label="Apply current machine values")
    return (apply_machine_btn,)


@app.cell
def state(mo):
    live_status_text, set_live_status = mo.state(
        "Waiting for live monitoring tab."
    )
    interactive_status_text, set_interactive_status = mo.state(
        "Ready for interactive exploration."
    )
    live_run_token, set_live_run_token = mo.state(0)
    active_tab, set_active_tab = mo.state("Live monitoring")
    # Incremented by slider on_change callbacks — triggers interactive_eval.
    interactive_eval_trigger, set_interactive_eval_trigger = mo.state(0)
    # Updated by the "apply machine values" button — causes slider cell to
    # re-run with new default values so the browser re-renders them.
    slider_display_values, set_slider_display_values = mo.state({})
    return (
        active_tab,
        interactive_eval_trigger,
        interactive_status_text,
        live_run_token,
        live_status_text,
        set_active_tab,
        set_interactive_eval_trigger,
        set_interactive_status,
        set_live_run_token,
        set_live_status,
        set_slider_display_values,
        slider_display_values,
    )


@app.cell
def live_dashboard_view(live_dashboard, mo):
    live_dashboard_widget = mo.mpl.interactive(live_dashboard.fig)
    return (live_dashboard_widget,)


@app.cell
def interactive_dashboard_view(interactive_dashboard, mo):
    interactive_dashboard_widget = mo.mpl.interactive(
        interactive_dashboard.fig
    )
    return (interactive_dashboard_widget,)


@app.cell
def live_visibility_sync(
    VisibilitySettings,
    live_dashboard,
    live_show_emit_x,
    live_show_emit_y,
    live_show_sigma_x,
    live_show_sigma_y,
    live_show_sigma_z,
    live_show_twiss_a_beta,
    live_show_twiss_b_beta,
):
    live_dashboard.set_visibility(
        VisibilitySettings(
            show_sigma_x=live_show_sigma_x.value,
            show_sigma_y=live_show_sigma_y.value,
            show_sigma_z=live_show_sigma_z.value,
            show_emit_x=live_show_emit_x.value,
            show_emit_y=live_show_emit_y.value,
            show_twiss_a_beta=live_show_twiss_a_beta.value,
            show_twiss_b_beta=live_show_twiss_b_beta.value,
        )
    )


@app.cell
def interactive_visibility_sync(
    VisibilitySettings,
    interactive_dashboard,
    interactive_show_emit_x,
    interactive_show_emit_y,
    interactive_show_sigma_x,
    interactive_show_sigma_y,
    interactive_show_sigma_z,
    interactive_show_twiss_a_beta,
    interactive_show_twiss_b_beta,
):
    interactive_dashboard.set_visibility(
        VisibilitySettings(
            show_sigma_x=interactive_show_sigma_x.value,
            show_sigma_y=interactive_show_sigma_y.value,
            show_sigma_z=interactive_show_sigma_z.value,
            show_emit_x=interactive_show_emit_x.value,
            show_emit_y=interactive_show_emit_y.value,
            show_twiss_a_beta=interactive_show_twiss_a_beta.value,
            show_twiss_b_beta=interactive_show_twiss_b_beta.value,
        )
    )


@app.cell
def layout(
    active_tab,
    apply_machine_btn,
    interactive_controls_ui,
    interactive_dashboard_widget,
    interactive_slider_controls_ui,
    interactive_status_text,
    live_controls_ui,
    live_dashboard_widget,
    live_status_text,
    mo,
    set_active_tab,
):
    live_status = mo.md(f"_Status: {live_status_text()}_")
    interactive_status = mo.md(f"_Status: {interactive_status_text()}_")
    live_content = mo.vstack(
        [live_controls_ui, live_dashboard_widget, live_status],
        gap="0.75rem",
    )
    interactive_content = mo.vstack(
        [
            mo.hstack(
                [interactive_controls_ui, apply_machine_btn],
                widths=[0.65, 0.35],
                gap="5rem",
            ),
            interactive_dashboard_widget,
            interactive_slider_controls_ui,
            interactive_status,
        ],
        gap="0.8",
    )
    tabs = mo.ui.tabs(
        {
            "Live monitoring": live_content,
            "Interactive offline changes": interactive_content,
        },
        value=active_tab(),
        on_change=set_active_tab,
    )
    tabs
    return (tabs,)


@app.cell
def stop_live_when_hidden(active_tab, live_run_token, set_live_run_token):
    if active_tab() != "Live monitoring":
        set_live_run_token(live_run_token() + 1)


@app.cell
async def live_stream_task(
    PACIFIC_TZ,
    SCREEN_CONFIGS,
    active_tab,
    asyncio,
    datetime,
    live_dashboard,
    live_image_scale_mode,
    live_poll_period_slider,
    live_run_token,
    live_screen_dropdown,
    model_input_names,
    mo,
    provider,
    set_live_run_token,
    set_live_status,
    source,
):
    mo.stop(active_tab() != "Live monitoring")

    live_token_value = live_run_token() + 1
    set_live_run_token(live_token_value)
    live_screen_config = SCREEN_CONFIGS[live_screen_dropdown.value]
    live_dashboard.reset(
        live_screen_config.label,
        "Time",
        "",
        "time",
        image_placeholder=live_screen_config.image_message,
    )
    source.reset()

    async def _run_live_stream(active_token: int) -> None:
        shot_index = 0
        while (
            active_token == live_run_token()
            and active_tab() == "Live monitoring"
        ):
            try:
                inputs = provider.read_inputs(model_input_names)
                now = datetime.now(tz=PACIFIC_TZ)
                frame = source.snapshot(
                    live_screen_dropdown.value,
                    control_updates=inputs,
                    x_axis_value=now,
                    frame_index=shot_index,
                    image_caption=now.strftime("%I:%M:%S %p"),
                )
                live_dashboard.update(frame, live_image_scale_mode.value)
                set_live_status(
                    f"Read {len(inputs)} EPICS inputs at {now.strftime('%I:%M:%S %p')} for {live_screen_dropdown.value}."
                )
                shot_index += 1
            except Exception as exc:
                set_live_status(f"Live EPICS update failed: {exc}")
            await asyncio.sleep(max(0.1, float(live_poll_period_slider.value)))

    asyncio.create_task(_run_live_stream(live_token_value))


@app.cell
def interactive_eval(
    MANUAL_INPUT_PVS,
    SCREEN_CONFIGS,
    interactive_dashboard,
    interactive_eval_trigger,
    interactive_image_scale_mode,
    interactive_screen_dropdown,
    interactive_sliders,
    set_interactive_status,
    source,
):
    """Reactively evaluate the model whenever slider values change.

    History accumulates across evals so the scalar timeseries scrolls left
    like a live plot.  History is cleared only when the screen selection
    changes (detected by comparing interactive_dashboard.screen_label).
    The x-axis is the integer eval counter, ticking up with each change.
    """
    eval_idx = interactive_eval_trigger()
    screen = interactive_screen_dropdown.value
    interactive_screen_config = SCREEN_CONFIGS[screen]

    # Reset (clearing history) when screen changes or on the very first eval.
    if (interactive_dashboard.screen_label != screen
            or not interactive_dashboard.history_data["x"]):
        interactive_dashboard.reset(
            interactive_screen_config.label,
            "Eval #",
            "",
            "value",
            image_placeholder=interactive_screen_config.image_message,
            clear_history=True,
        )

    manual_values = {
        name: float(interactive_sliders[name].value)
        for name in MANUAL_INPUT_PVS
    }
    frame = source.snapshot(
        screen,
        control_updates=manual_values,
        x_axis_value=float(eval_idx),
        frame_index=eval_idx,
        image_caption=f"Eval {eval_idx}",
        title_suffix="manual",
    )
    interactive_dashboard.update(frame, interactive_image_scale_mode.value)
    set_interactive_status(
        f"Eval #{eval_idx} for {screen}."
    )


@app.cell
def apply_machine_values(
    MANUAL_INPUT_PVS,
    apply_machine_btn,
    mo,
    model_input_names,
    provider,
    set_interactive_eval_trigger,
    set_interactive_status,
    set_slider_display_values,
):
    """Fetch current EPICS values and re-render sliders with those values.

    Setting slider_display_values causes interactive_slider_controls to
    re-run, which re-creates the mo.ui.number widgets with new default values.
    The browser re-renders them, and interactive_eval re-runs because its
    interactive_sliders dependency changed.
    """
    mo.stop(not apply_machine_btn.value)
    try:
        live_values = provider.read_inputs(model_input_names)
        new_display = {
            pv: round(float(live_values[pv]), 3) for pv in MANUAL_INPUT_PVS if pv in live_values
        }
        set_slider_display_values(new_display)
        set_interactive_eval_trigger(lambda x: x + 1)
        set_interactive_status(
            f"Applied {len(new_display)} machine values to sliders."
        )
    except Exception as exc:
        set_interactive_status(f"Failed to read machine values: {exc}")


if __name__ == "__main__":
    app.run()

# Preload torch at module-import time so it is in sys.modules before any
# marimo kernel cell runs.  Placing the import here (after the run guard)
# avoids a marimo notebook-format violation while still ensuring torch is
# loaded before virtual_accelerator reaches RpcBackendOptions.
import os as _os  # noqa: E402
import torch  # noqa: F401

# Pin torch thread count to match cgroup CPU allocation.  Without this,
# torch sees all host CPUs and spawns too many threads, causing contention
# inside containers with a cgroup CPU limit.
_num_threads = int(_os.environ.get("TORCH_NUM_THREADS", _os.environ.get("OMP_NUM_THREADS", "0")))
if _num_threads > 0:
    torch.set_num_threads(_num_threads)
    torch.set_num_interop_threads(max(1, _num_threads))