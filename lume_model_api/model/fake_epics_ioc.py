from __future__ import annotations

import argparse
import math
import os
import random
import time
from dataclasses import dataclass

from caproto.server import PVGroup, pvproperty, run

from lume_visualizations.config import EPICS_INPUT_PVS


DEFAULT_SERVER_INTERFACES = ("127.0.0.1",)


@dataclass(frozen=True)
class FakePVSpec:
    attr_name: str
    pv_name: str
    default: float
    minimum: float
    maximum: float
    period_s: float
    phase_offset_rad: float = 0.0

SCALE = 0.25
FAKE_INPUT_SPECS = [
    #FakePVSpec("camr_in20_186_r_dist", "CAMR:IN20:186:R_DIST", 423.867825, 210.21247820852545, 499.9996083265339, 23.0, 0.0),
    #FakePVSpec("camr_in20_186_xrms", "CAMR:IN20:186:XRMS", 300, 200, 400, 23.0, 0.0),
    #FakePVSpec("camr_in20_186_yrms", "CAMR:IN20:186:YRMS", 300, 200, 400, 23.0, 0.0),
    FakePVSpec("soln_in20_121_bctrl", "SOLN:IN20:121:BCTRL", 0.4779693455075814, 0.3774080152672698, 0.4983800018349345, 29.0, 1.0),
    FakePVSpec("quad_in20_121_bctrl", "QUAD:IN20:121:BCTRL", -0.001499227120199691, -0.02098429469554406, 0.020999198106589838, 17.0, 1.3),
    FakePVSpec("quad_in20_122_bctrl", "QUAD:IN20:122:BCTRL", -0.0006872989433749197, -0.020998830517503037, 0.020998929132148195, 21.0, 1.7),
    FakePVSpec("accl_in20_300_l0a_pdes", "ACCL:IN20:300:L0A_PDES", -9.53597349, -24.998714513984325, 9.991752397382681, 37.0, 2.0),
    FakePVSpec("accl_in20_400_l0b_pdes", "ACCL:IN20:400:L0B_PDES", 9.85566222, -24.99972566363747, 9.998904767155892, 33.0, 2.3),
    FakePVSpec("quad_in20_361_bctrl", "QUAD:IN20:361:BCTRL", -2.0005920106399526, -4.318053641915576, -1.0800430432494976, 27.0, 2.6),
    FakePVSpec("quad_in20_371_bctrl", "QUAD:IN20:371:BCTRL", 2.0005920106399526, 1.0913525514575348, 4.30967984810423, 25.0, 3.0),
    FakePVSpec("quad_in20_425_bctrl", "QUAD:IN20:425:BCTRL", -1.0807627139393465, -7.559759590824369, -1.080762695815712, 35.0, 3.3),
    FakePVSpec("quad_in20_441_bctrl", "QUAD:IN20:441:BCTRL", -0.17938799998564897, -1.0782202690353522, 7.559878303179915, 22.0, 3.6),
    FakePVSpec("quad_in20_511_bctrl", "QUAD:IN20:511:BCTRL", 2.852171999771826, -1.0792451325247663, 7.5582919025608595, 39.0, 4.0),
    FakePVSpec("quad_in20_525_bctrl", "QUAD:IN20:525:BCTRL", -3.218399988942528, -7.557932980106783, -1.0800286565992732, 15.0, 4.4),
    FakePVSpec("quad_in20_631_bctrl", "QUAD:IN20:631:BCTRL", 7.335358881640616, 7.335358881640616*(1-SCALE), 7.335358881640616*(1+SCALE), 1.0, 4.7),
    FakePVSpec("quad_in20_651_bctrl", "QUAD:IN20:651:BCTRL", -5.821093449409309, -5.821093449409309*(1+SCALE), -5.821093449409309*(1-SCALE), 29.0, 5.0),
    FakePVSpec("xcor_in20_641_bctrl", "XCOR:IN20:641:BCTRL", 0.0, 0.0, 0.0, 19.0, 5.3),
    FakePVSpec("ycor_in20_642_bctrl", "YCOR:IN20:642:BCTRL", 0.0, 0.0, 0.0, 19.0, 5.6),
]


def _validate_fake_input_specs() -> None:
    fake_pvs = {spec.pv_name for spec in FAKE_INPUT_SPECS}
    expected_pvs = set(EPICS_INPUT_PVS)
    missing = sorted(expected_pvs - fake_pvs)
    extra = sorted(fake_pvs - expected_pvs)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected: {', '.join(extra)}")
        raise RuntimeError(
            "FAKE_INPUT_SPECS does not match EPICS_INPUT_PVS ("
            + "; ".join(details)
            + ")."
        )


_validate_fake_input_specs()


class FakeLumeInputIOC(PVGroup):
    heartbeat = pvproperty(name="LUME:FAKEIOC:HEARTBEAT", value=0.0)

    # camr_in20_186_r_dist = pvproperty(name="CAMR:IN20:186:R_DIST", value=423.867825)
    # camr_in20_186_xrms = pvproperty(name="CAMR:IN20:186:XRMS", value=300)
    # camr_in20_186_yrms = pvproperty(name="CAMR:IN20:186:YRMS", value=300)
    soln_in20_121_bctrl = pvproperty(name="SOLN:IN20:121:BCTRL", value=0.4779693455075814)
    quad_in20_121_bctrl = pvproperty(name="QUAD:IN20:121:BCTRL", value=-0.001499227120199691)
    quad_in20_122_bctrl = pvproperty(name="QUAD:IN20:122:BCTRL", value=-0.0006872989433749197)
    accl_in20_300_l0a_pdes = pvproperty(name="ACCL:IN20:300:L0A_PDES", value=-9.53597349)
    accl_in20_400_l0b_pdes = pvproperty(name="ACCL:IN20:400:L0B_PDES", value=9.85566222)
    quad_in20_361_bctrl = pvproperty(name="QUAD:IN20:361:BCTRL", value=-2.0005920106399526)
    quad_in20_371_bctrl = pvproperty(name="QUAD:IN20:371:BCTRL", value=2.0005920106399526)
    quad_in20_425_bctrl = pvproperty(name="QUAD:IN20:425:BCTRL", value=-1.0807627139393465)
    quad_in20_441_bctrl = pvproperty(name="QUAD:IN20:441:BCTRL", value=-0.17938799998564897)
    quad_in20_511_bctrl = pvproperty(name="QUAD:IN20:511:BCTRL", value=2.852171999771826)
    quad_in20_525_bctrl = pvproperty(name="QUAD:IN20:525:BCTRL", value=-3.218399988942528)
    quad_in20_631_bctrl = pvproperty(name="QUAD:IN20:631:BCTRL", value=-0.5)    
    quad_in20_651_bctrl = pvproperty(name="QUAD:IN20:651:BCTRL", value=0.5)
    xcor_in20_641_bctrl = pvproperty(name="XCOR:IN20:641:BCTRL", value=0.0)
    ycor_in20_642_bctrl = pvproperty(name="YCOR:IN20:642:BCTRL", value=0.0)
    def __init__(self, *args, update_period: float = 0.5, noise_scale: float = 0.03, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_period = update_period
        self.noise_scale = noise_scale
        self._random = random.Random(2719)

    def _signal_bounds(self, spec: FakePVSpec) -> tuple[float, float, float]:
        if math.isclose(spec.minimum, spec.maximum):
            return (spec.default, 0.0, 0.0)

        span = spec.maximum - spec.minimum
        center = spec.default
        half_span = min(center - spec.minimum, spec.maximum - center)
        if half_span < 0.15 * (0.5 * span):
            center = spec.minimum + 0.5 * span
            half_span = 0.5 * span

        amplitude = 0.45 * half_span
        noise_amplitude = amplitude * self.noise_scale
        return (center, amplitude, noise_amplitude)

    def _value_for_spec(self, spec: FakePVSpec, elapsed_s: float) -> float:
        center, amplitude, noise_amplitude = self._signal_bounds(spec)
        if amplitude == 0.0:
            return center

        phase = 2.0 * math.pi * elapsed_s / spec.period_s + spec.phase_offset_rad
        noise = noise_amplitude * (self._random.random() - 0.5)
        value = center + amplitude * math.sin(phase) + noise
        return min(max(value, spec.minimum), spec.maximum)

    @heartbeat.startup
    async def heartbeat(self, instance, async_lib):
        started_at = time.monotonic()
        tick = 0
        while True:
            elapsed_s = time.monotonic() - started_at
            for spec in FAKE_INPUT_SPECS:
                await getattr(self, spec.attr_name).write(self._value_for_spec(spec, elapsed_s))
            await instance.write(float(tick))
            tick += 1
            await async_lib.library.sleep(self.update_period)


def pv_names() -> list[str]:
    return [spec.pv_name for spec in FAKE_INPUT_SPECS]


def _split_address_list(value: str) -> list[str]:
    return [item for item in value.split() if item.strip()]


def _resolve_interfaces(cli_interfaces: list[str] | None) -> list[str]:
    if cli_interfaces:
        return cli_interfaces

    env_interfaces = _split_address_list(os.environ.get("EPICS_CAS_INTF_ADDR_LIST", ""))
    if env_interfaces:
        return env_interfaces

    return list(DEFAULT_SERVER_INTERFACES)


def _configure_caproto_network(
    cli_interfaces: list[str] | None,
    beacon_addresses: list[str] | None,
    broadcast_auto_beacons: bool,
) -> tuple[list[str], bool]:
    interfaces = _resolve_interfaces(cli_interfaces)

    if broadcast_auto_beacons:
        return interfaces, False

    if beacon_addresses:
        os.environ["EPICS_CAS_AUTO_BEACON_ADDR_LIST"] = "NO"
        os.environ["EPICS_CAS_BEACON_ADDR_LIST"] = " ".join(beacon_addresses)
        return interfaces, False

    if "EPICS_CAS_BEACON_ADDR_LIST" in os.environ and "EPICS_CAS_AUTO_BEACON_ADDR_LIST" not in os.environ:
        os.environ["EPICS_CAS_AUTO_BEACON_ADDR_LIST"] = "NO"
        return interfaces, False

    if "EPICS_CAS_AUTO_BEACON_ADDR_LIST" not in os.environ and "EPICS_CAS_BEACON_ADDR_LIST" not in os.environ:
        os.environ["EPICS_CAS_AUTO_BEACON_ADDR_LIST"] = "NO"
        os.environ["EPICS_CAS_BEACON_ADDR_LIST"] = ""
        return interfaces, True

    return interfaces, False


def _disable_caproto_beacons() -> None:
    import caproto as ca

    def _no_beacon_addresses(*, protocol=ca.Protocol.ChannelAccess):
        return []

    ca.get_beacon_address_list = _no_beacon_addresses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fake EPICS IOC for the live streaming monitor.")
    parser.add_argument("--update-period", type=float, default=0.5, help="Seconds between IOC updates.")
    parser.add_argument("--noise-scale", type=float, default=0.03, help="Relative noise scale applied to each waveform.")
    parser.add_argument("--list-pvs", action="store_true", help="Print the PV names served by this IOC and exit.")
    parser.add_argument(
        "--interfaces",
        nargs="+",
        default=None,
        help="Server interfaces to bind. Defaults to EPICS_CAS_INTF_ADDR_LIST when set, otherwise 127.0.0.1.",
    )
    beacon_group = parser.add_mutually_exclusive_group()
    beacon_group.add_argument(
        "--beacon-addresses",
        nargs="+",
        default=None,
        help="Explicit EPICS CA beacon destinations. Defaults to the chosen interfaces with automatic broadcast disabled.",
    )
    beacon_group.add_argument(
        "--broadcast-auto-beacons",
        action="store_true",
        help="Use caproto's automatic broadcast beacon address list instead of the local-safe default.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_pvs:
        for name in pv_names():
            print(name)
        return 0

    interfaces, beacons_disabled = _configure_caproto_network(
        cli_interfaces=args.interfaces,
        beacon_addresses=args.beacon_addresses,
        broadcast_auto_beacons=args.broadcast_auto_beacons,
    )
    if beacons_disabled:
        _disable_caproto_beacons()
    ioc = FakeLumeInputIOC(prefix="", update_period=args.update_period, noise_scale=args.noise_scale)
    print("Starting fake EPICS IOC for LUME live-monitor testing")
    print(f"Serving interfaces: {', '.join(interfaces)}")
    if beacons_disabled:
        print("Beacon targets: disabled")
    elif os.environ.get("EPICS_CAS_AUTO_BEACON_ADDR_LIST", "YES").lower() == "yes":
        print("Beacon targets: automatic broadcast discovery")
    else:
        beacon_targets = os.environ.get("EPICS_CAS_BEACON_ADDR_LIST", "") or "disabled"
        print(f"Beacon targets: {beacon_targets}")
    print("Serving PVs:")
    for name in pv_names():
        print(f"  {name}")
    run(ioc.pvdb, interfaces=interfaces)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
