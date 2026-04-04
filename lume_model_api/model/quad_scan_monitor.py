"""
quad_scan_monitor.py
--------------------
Quadrupole scan monitor with selectable OTR2 / OTR3 / OTR4 views.

Run with:
    marimo run lume_visualizations/quad_scan_monitor.py
"""

import marimo

__generated_with = "0.22.0"
app = marimo.App(width="full", app_title="LUME Quad Scan Monitor")


@app.cell
def imports():
    import asyncio
    import sys
    import warnings
    from pathlib import Path

    import matplotlib

    matplotlib.use("Agg")
    import marimo as mo
    import numpy as np

    warnings.filterwarnings("ignore")

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from lume_visualizations.beam_monitor import StagedModelImageSource
    from lume_visualizations.config import SCAN_PV, SCREEN_CONFIGS, SCREEN_KEYS
    from lume_visualizations.dashboard import BeamDashboard, VisibilitySettings

    return (
        asyncio,
        mo,
        np,
        SCAN_PV,
        SCREEN_CONFIGS,
        SCREEN_KEYS,
        BeamDashboard,
        StagedModelImageSource,
        VisibilitySettings,
    )


@app.cell
def header(mo):
    mo.md("# LUME Quad Scan Monitor")


@app.cell
def source_setup(StagedModelImageSource):
    source = StagedModelImageSource.create_default()
    return (source,)


@app.cell
def controls(mo, SCREEN_KEYS):
    screen_dropdown = mo.ui.dropdown(
        options=SCREEN_KEYS,
        value="OTR4",
        label="Statistics view",
    )
    scan_min_slider = mo.ui.slider(
        start=-15.0, stop=0.0, step=0.5, value=-15.0, label="Min (kG)", show_value=True
    )
    scan_max_slider = mo.ui.slider(
        start=0.0, stop=15.0, step=0.5, value=15.0, label="Max (kG)", show_value=True
    )
    scan_steps_slider = mo.ui.slider(
        start=3, stop=50, step=1, value=5, label="Steps", show_value=True
    )
    image_scale_mode = mo.ui.dropdown(
        options=["robust", "fixed", "auto"],
        value="robust",
        label="Image scale",
    )
    show_sigma_x = mo.ui.checkbox(value=True, label="sigma_x")
    show_sigma_y = mo.ui.checkbox(value=True, label="sigma_y")
    show_sigma_z = mo.ui.checkbox(value=True, label="sigma_z")
    show_emit_x = mo.ui.checkbox(value=True, label="eps_n,x")
    show_emit_y = mo.ui.checkbox(value=True, label="eps_n,y")
    show_twiss_a_beta = mo.ui.checkbox(value=True, label="a.beta")
    show_twiss_b_beta = mo.ui.checkbox(value=True, label="b.beta")
    run_button = mo.ui.run_button(label="Run scan")
    stop_button = mo.ui.button(label="Stop", kind="danger")

    controls_ui = mo.vstack(
        [
            mo.hstack(
                [
                    screen_dropdown,
                    scan_min_slider,
                    scan_max_slider,
                    scan_steps_slider,
                    image_scale_mode,
                    run_button,
                    stop_button,
                ],
                gap="1.0rem",
                justify="start",
            ),
            mo.hstack(
                [
                    mo.md("**Show:**"),
                    show_sigma_x,
                    show_sigma_y,
                    show_sigma_z,
                    show_emit_x,
                    show_emit_y,
                    show_twiss_a_beta,
                    show_twiss_b_beta,
                ],
                gap="0.8rem",
                justify="start",
            ),
        ],
        gap="0.8rem",
    )
    controls_ui

    return (
        image_scale_mode,
        run_button,
        scan_max_slider,
        scan_min_slider,
        scan_steps_slider,
        screen_dropdown,
        show_emit_x,
        show_emit_y,
        show_sigma_x,
        show_sigma_y,
        show_sigma_z,
        show_twiss_a_beta,
        show_twiss_b_beta,
        stop_button,
    )


@app.cell
def state(mo):
    status_text, set_status = mo.state("Idle - press Run scan to start.")
    scan_running, set_scan_running = mo.state(False)
    run_token, set_run_token = mo.state(0)
    return (
        run_token,
        scan_running,
        set_run_token,
        set_scan_running,
        set_status,
        status_text,
    )


@app.cell
def dashboard_setup(BeamDashboard):
    dashboard = BeamDashboard("LUME Quad Scan Monitor")
    return (dashboard,)


@app.cell
def dashboard_view(dashboard, mo):
    mo.mpl.interactive(dashboard.fig)


@app.cell
def dashboard_sync(
    SCREEN_CONFIGS,
    SCAN_PV,
    dashboard,
    scan_running,
    screen_dropdown,
):
    if not scan_running():
        dashboard_screen_config = SCREEN_CONFIGS[screen_dropdown.value]
        dashboard.reset(
            dashboard_screen_config.label,
            SCAN_PV,
            "kG",
            "value",
            image_placeholder=dashboard_screen_config.image_message,
        )


@app.cell
def visibility_sync(
    VisibilitySettings,
    dashboard,
    show_emit_x,
    show_emit_y,
    show_sigma_x,
    show_sigma_y,
    show_sigma_z,
    show_twiss_a_beta,
    show_twiss_b_beta,
):
    dashboard.set_visibility(
        VisibilitySettings(
            show_sigma_x=show_sigma_x.value,
            show_sigma_y=show_sigma_y.value,
            show_sigma_z=show_sigma_z.value,
            show_emit_x=show_emit_x.value,
            show_emit_y=show_emit_y.value,
            show_twiss_a_beta=show_twiss_a_beta.value,
            show_twiss_b_beta=show_twiss_b_beta.value,
        )
    )


@app.cell
async def scan_task(
    SCAN_PV,
    SCREEN_CONFIGS,
    asyncio,
    dashboard,
    image_scale_mode,
    mo,
    np,
    run_button,
    run_token,
    scan_max_slider,
    scan_min_slider,
    scan_running,
    scan_steps_slider,
    screen_dropdown,
    set_run_token,
    set_scan_running,
    set_status,
    source,
):
    mo.stop(not run_button.value)

    token = run_token() + 1
    set_run_token(token)
    set_scan_running(True)

    selected_screen = screen_dropdown.value
    scan_screen_config = SCREEN_CONFIGS[selected_screen]
    dashboard.reset(
        scan_screen_config.label,
        SCAN_PV,
        "kG",
        "value",
        image_placeholder=scan_screen_config.image_message,
    )
    set_status(f"Preparing scan at {selected_screen}...")
    source.reset()

    async def _run_scan(active_token: int) -> None:
        quad_values = np.linspace(
            scan_min_slider.value,
            scan_max_slider.value,
            int(scan_steps_slider.value),
        )
        total_steps = len(quad_values)
        for index, quad_value in enumerate(quad_values):
            if active_token != run_token() or not scan_running():
                return

            set_status(
                f"Step {index + 1}/{total_steps} - {SCAN_PV} = {quad_value:.2f} kG at {selected_screen}."
            )
            await asyncio.sleep(0)
            frame = source.snapshot(
                selected_screen,
                control_updates={SCAN_PV: float(quad_value)},
                x_axis_value=float(quad_value),
                frame_index=index,
                image_caption=f"{SCAN_PV} = {quad_value:.2f} kG",
                title_suffix=f"Step {index + 1}/{total_steps}",
            )
            if active_token != run_token() or not scan_running():
                return
            dashboard.update(frame, image_scale_mode.value)
            await asyncio.sleep(0.05)

        if active_token == run_token():
            set_status(f"Scan complete - {total_steps} steps finished at {selected_screen}.")
            set_scan_running(False)

    asyncio.create_task(_run_scan(token))


@app.cell
def stop_scan(
    mo,
    run_token,
    scan_running,
    set_run_token,
    set_scan_running,
    set_status,
    stop_button,
):
    mo.stop(not stop_button.value or not scan_running())
    set_scan_running(False)
    set_run_token(run_token() + 1)
    set_status("Scan stopped by user.")


@app.cell
def status_bar(mo, status_text):
    mo.callout(mo.md(status_text()), kind="info")


if __name__ == "__main__":
    app.run()
