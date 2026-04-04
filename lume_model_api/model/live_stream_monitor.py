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

    import matplotlib

    matplotlib.use("Agg")
    import marimo as mo

    warnings.filterwarnings("ignore")

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from lume_visualizations.beam_monitor import StagedModelImageSource
    from lume_visualizations.config import (
        EXTRA_MACHINE_INPUTS,
        MANUAL_INPUT_PVS,
        SCREEN_CONFIGS,
        SCREEN_KEYS,
    )
    from lume_visualizations.dashboard import BeamDashboard, VisibilitySettings
    from lume_visualizations.epics_controls import EpicsInputProvider
    from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

    return (
        asyncio,
        datetime,
        EXTRA_MACHINE_INPUTS,
        FAKE_INPUT_SPECS,
        mo,
        MANUAL_INPUT_PVS,
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
def source_setup(EpicsInputProvider, EXTRA_MACHINE_INPUTS, FAKE_INPUT_SPECS, StagedModelImageSource):
    source = StagedModelImageSource.create_default()
    provider = EpicsInputProvider()
    model_input_names = source.get_model_input_names()
    # Drop the first name (CAMR:IN20:186:R_DIST — not used as EPICS input here)
    # and append extra camera measurement PVs fed as additional model inputs.
    model_input_names = model_input_names[1:] + EXTRA_MACHINE_INPUTS
    # Use FAKE_INPUT_SPECS defaults for initial slider positions — avoids an
    # expensive model.get() call and is robust to EXTRA_MACHINE_INPUTS not
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
        gap="0.6rem",
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
    FAKE_INPUT_SPECS, MANUAL_INPUT_PVS, initial_inputs, mo
):
    slider_labels = {
        "SOLN:IN20:121:BCTRL": "Solenoid 121",
        "QUAD:IN20:121:BCTRL": "Quad 121",
        "QUAD:IN20:122:BCTRL": "Quad 122",
        "ACCL:IN20:300:L0A_PDES": "L0A phase",
        "ACCL:IN20:400:L0B_PDES": "L0B phase",
        "QUAD:IN20:361:BCTRL": "Quad 361",
        "QUAD:IN20:371:BCTRL": "Quad 371",
        "QUAD:IN20:425:BCTRL": "Quad 425",
        "QUAD:IN20:441:BCTRL": "Quad 441",
        "QUAD:IN20:511:BCTRL": "Quad 511",
        "QUAD:IN20:525:BCTRL": "Quad 525",
    }
    slider_specs = {spec.pv_name: spec for spec in FAKE_INPUT_SPECS}

    def slider_step(pv_name: str) -> float:
        spec = slider_specs[pv_name]
        span = float(spec.maximum - spec.minimum)
        if span <= 0:
            return 0.01
        if span < 0.1:
            return span / 100.0
        if span < 1.0:
            return span / 200.0
        return span / 150.0

    interactive_sliders = {}
    slider_rows = []
    current_row = []
    for index, pv_name in enumerate(MANUAL_INPUT_PVS):
        spec = slider_specs[pv_name]
        slider = mo.ui.slider(
            start=float(spec.minimum),
            stop=float(spec.maximum),
            step=slider_step(pv_name),
            value=float(initial_inputs[pv_name]),
            label=slider_labels.get(pv_name, pv_name),
            show_value=True,
            include_input=True,
            full_width=True,
        )
        interactive_sliders[pv_name] = slider
        current_row.append(slider)
        if len(current_row) == 4 or index == len(MANUAL_INPUT_PVS) - 1:
            slider_rows.append(
                mo.hstack(current_row, widths="equal", gap="0.6rem")
            )
            current_row = []

    interactive_slider_controls_ui = mo.vstack(slider_rows, gap="0.3rem")
    return interactive_slider_controls_ui, interactive_sliders


@app.cell
def state(mo):
    live_status_text, set_live_status = mo.state(
        "Waiting for live monitoring tab."
    )
    interactive_status_text, set_interactive_status = mo.state(
        "Open the interactive tab to start the fake stream."
    )
    live_run_token, set_live_run_token = mo.state(0)
    interactive_run_token, set_interactive_run_token = mo.state(0)
    active_tab, set_active_tab = mo.state("Live monitoring")
    return (
        active_tab,
        interactive_run_token,
        interactive_status_text,
        live_run_token,
        live_status_text,
        set_active_tab,
        set_interactive_run_token,
        set_interactive_status,
        set_live_run_token,
        set_live_status,
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
    # Controls and sliders sit above the full-width dashboard so the
    # dashboard is not squeezed by a side panel.
    interactive_content = mo.vstack(
        [
            interactive_controls_ui,
            interactive_slider_controls_ui,
            interactive_status,
            interactive_dashboard_widget,
        ],
        gap="0.5rem",
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
def stop_interactive_when_hidden(
    active_tab,
    interactive_run_token,
    set_interactive_run_token,
):
    if active_tab() != "Interactive offline changes":
        set_interactive_run_token(interactive_run_token() + 1)


@app.cell
async def live_stream_task(
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
                now = datetime.now()
                frame = source.snapshot(
                    live_screen_dropdown.value,
                    control_updates=inputs,
                    x_axis_value=now,
                    frame_index=shot_index,
                    image_caption=now.strftime("%H:%M:%S"),
                )
                live_dashboard.update(frame, live_image_scale_mode.value)
                set_live_status(
                    f"Read {len(inputs)} EPICS inputs at {now.strftime('%H:%M:%S')} for {live_screen_dropdown.value}."
                )
                shot_index += 1
            except Exception as exc:
                set_live_status(f"Live EPICS update failed: {exc}")
            await asyncio.sleep(max(0.1, float(live_poll_period_slider.value)))

    asyncio.create_task(_run_live_stream(live_token_value))


@app.cell
async def interactive_stream_task(
    MANUAL_INPUT_PVS,
    SCREEN_CONFIGS,
    active_tab,
    asyncio,
    datetime,
    interactive_dashboard,
    interactive_image_scale_mode,
    interactive_run_token,
    interactive_screen_dropdown,
    interactive_sliders,
    mo,
    set_interactive_run_token,
    set_interactive_status,
    source,
):
    mo.stop(active_tab() != "Interactive offline changes")

    interactive_token_value = interactive_run_token() + 1
    set_interactive_run_token(interactive_token_value)
    interactive_screen_config = SCREEN_CONFIGS[interactive_screen_dropdown.value]
    interactive_dashboard.reset(
        interactive_screen_config.label,
        "Time",
        "",
        "time",
        image_placeholder=interactive_screen_config.image_message,
    )
    source.reset()

    async def _run_interactive_stream(active_token: int) -> None:
        shot_index = 0
        last_emitted_at = None
        last_manual_values = None
        while (
            active_token == interactive_run_token()
            and active_tab() == "Interactive offline changes"
        ):
            manual_values = {
                name: float(interactive_sliders[name].value)
                for name in MANUAL_INPUT_PVS
            }
            should_emit = False
            now = datetime.now()
            if last_manual_values is None or manual_values != last_manual_values:
                should_emit = True
            elif (
                last_emitted_at is None
                or (now - last_emitted_at).total_seconds() >= 1.0
            ):
                should_emit = True

            if should_emit:
                frame = source.snapshot(
                    interactive_screen_dropdown.value,
                    control_updates=manual_values,
                    x_axis_value=now,
                    frame_index=shot_index,
                    image_caption=now.strftime("%H:%M:%S"),
                    title_suffix=None,
                )
                interactive_dashboard.update(
                    frame, interactive_image_scale_mode.value
                )
                set_interactive_status(
                    f"Offline stream updated at {now.strftime('%H:%M:%S')} using manual controls for {interactive_screen_dropdown.value}."
                )
                shot_index += 1
                last_emitted_at = now
                last_manual_values = manual_values

            await asyncio.sleep(0.2)

    asyncio.create_task(_run_interactive_stream(interactive_token_value))


if __name__ == "__main__":
    app.run()