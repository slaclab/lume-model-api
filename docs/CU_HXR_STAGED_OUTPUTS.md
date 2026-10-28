# `cu_hxr_staged` inputs, outputs and screens

A snapshot of `GET /api/v1/models/cu_hxr_staged/config` from the dev deployment, taken 2026-10-28. Model version `cu_hxr_staged`. The live `config` response is the source of truth and can change with a new image, so build UIs from it rather than from this page.

The model has 5 screens, 524 outputs and 54 inputs.

| `kind` | Count |
|---|---|
| `particles` | 5 |
| `array` | 45 |
| `scalar` | 413 |
| `value` | 61 |

## Inputs

The settable knobs, in the order `config` returns them. Send them in `inputs` on `POST evaluate`. An input you leave out uses its `default`. Numbers are rounded to 6 significant figures here, and the `config` response has the full values.

`range_source` is `model` when the model declared the range and `derived` when the service derived it from the default. Neither is a safety limit: a derived range is only a display hint, and the model can still fail on values inside either type of range. None of these inputs is `constant` or an alias today.

| `id` | `unit` | `default` | `min` | `max` | `range_source` | `constant` | `alias_of` |
|---|---|---|---|---|---|---|---|
| `ACCL:IN20:300:L0A_PDES` | `deg` | -9.53597 | -24.9987 | 9.99175 | `model` |  |  |
| `ACCL:IN20:400:L0B_PDES` | `deg` | 9.85566 | -24.9997 | 9.9989 | `model` |  |  |
| `BEND:IN20:661:BCTRL` | `GeV/c` | 0.134999 | 0.0674995 | 0.202499 | `derived` |  |  |
| `BEND:IN20:751:BCTRL` | `GeV/c` | 0.134999 | 0.0674995 | 0.202499 | `derived` |  |  |
| `BEND:LI21:215:BCTRL` | `GeV/c` | 0.219999 | 0.11 | 0.329999 | `derived` |  |  |
| `BEND:LI21:231:BCTRL` | `GeV/c` | 0.219999 | 0.11 | 0.329999 | `derived` |  |  |
| `BEND:LI21:241:BCTRL` | `GeV/c` | 0.219999 | 0.11 | 0.329999 | `derived` |  |  |
| `BEND:LI21:261:BCTRL` | `GeV/c` | 0.219999 | 0.11 | 0.329999 | `derived` |  |  |
| `KLYS:LI21:11:ENLD` | `MeV` | 111.556 | 55.7781 | 167.334 | `derived` |  |  |
| `KLYS:LI21:11:PDES` | `degrees` | 0 | -0.5 | 0.5 | `derived` |  |  |
| `QUAD:IN20:121:BCTRL` | `kG` | -0.00149923 | -0.0209843 | 0.0209992 | `model` |  |  |
| `QUAD:IN20:122:BCTRL` | `kG` | -0.000687299 | -0.0209988 | 0.0209989 | `model` |  |  |
| `QUAD:IN20:361:BCTRL` | `kG` | -2.00059 | -4.31805 | -1.08004 | `model` |  |  |
| `QUAD:IN20:371:BCTRL` | `kG` | 2.00059 | 1.09135 | 4.30968 | `model` |  |  |
| `QUAD:IN20:425:BCTRL` | `kG` | -1.08076 | -7.55976 | -1.08076 | `model` |  |  |
| `QUAD:IN20:441:BCTRL` | `kG` | -0.179388 | -1.07822 | 7.55988 | `model` |  |  |
| `QUAD:IN20:511:BCTRL` | `kG` | 2.85217 | -1.07925 | 7.55829 | `model` |  |  |
| `QUAD:IN20:525:BCTRL` | `kG` | -3.2184 | -7.55793 | -1.08003 | `model` |  |  |
| `QUAD:IN20:631:BCTRL` | `kG` | 7.33536 | 3.66768 | 11.003 | `derived` |  |  |
| `QUAD:IN20:651:BCTRL` | `kG` | -5.82109 | -8.73164 | -2.91055 | `derived` |  |  |
| `QUAD:IN20:731:BCTRL` | `kG` | 10.6621 | 5.33103 | 15.9931 | `derived` |  |  |
| `QUAD:IN20:771:BCTRL` | `kG` | -4.03411 | -6.05116 | -2.01705 | `derived` |  |  |
| `QUAD:IN20:781:BCTRL` | `kG` | 6.48665 | 3.24332 | 9.72997 | `derived` |  |  |
| `QUAD:LI21:131:BCTRL` | `kG` | -2.42969 | -3.64454 | -1.21485 | `derived` |  |  |
| `QUAD:LI21:161:BCTRL` | `kG` | 1.3749 | 0.687448 | 2.06234 | `derived` |  |  |
| `QUAD:LI21:201:BCTRL` | `kG` | -7.36258 | -11.0439 | -3.68129 | `derived` |  |  |
| `QUAD:LI21:211:BCTRL` | `kG` | 6.341 | 3.1705 | 9.51149 | `derived` |  |  |
| `QUAD:LI21:221:BCTRL` | `kG` | -0 | -0.5 | 0.5 | `derived` |  |  |
| `QUAD:LI21:243:BCTRL` | `kG` | -0 | -0.5 | 0.5 | `derived` |  |  |
| `QUAD:LI21:251:BCTRL` | `kG` | -0 | -0.5 | 0.5 | `derived` |  |  |
| `QUAD:LI21:271:BCTRL` | `kG` | -6.59919 | -9.89878 | -3.29959 | `derived` |  |  |
| `QUAD:LI21:278:BCTRL` | `kG` | 7.87607 | 3.93803 | 11.8141 | `derived` |  |  |
| `QUAD:LI21:301:BCTRL` | `kG` | -0.10557 | -0.158355 | -0.0527849 | `derived` |  |  |
| `SOLN:IN20:121:BCTRL` | `kG*m` | 0.477969 | 0.377408 | 0.49838 | `model` |  |  |
| `XCOR:IN20:641:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:IN20:721:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:IN20:761:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:101:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:135:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:165:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:175:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:191:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:275:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `XCOR:LI21:302:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:IN20:642:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:IN20:722:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:IN20:762:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:102:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:136:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:166:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:174:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:192:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:276:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |
| `YCOR:LI21:303:BCTRL` |  | -0 | -0.5 | 0.5 | `derived` |  |  |

## Screens

`?screen=<key>` requests both the screen's particles output and its image output. The image shape comes from the image's entry in `outputs`.

| `key` | `particles` | `image` | Image shape |
|---|---|---|---|
| `OTR11` | `OTR11_beam` | `OTRS:LI21:237:Image:ArrayData` | 1040×1392 |
| `OTR12` | `OTR12_beam` | `OTRS:LI21:291:Image:ArrayData` | 1040×1392 |
| `OTR2` | `OTR2_beam` | `OTRS:IN20:571:Image:ArrayData` | 1040×1392 |
| `OTR3` | `OTR3_beam` | `OTRS:IN20:621:Image:ArrayData` | 1040×1392 |
| `OTR4` | `OTR4_beam` | `OTRS:IN20:711:Image:ArrayData` | 1040×1392 |

## Outputs

`kind` decides how a UI decodes and draws an output, so switch on it. `variable_class` is the model's own class name and is informational only. Each section lists the outputs of one `kind`, in the order `config` returns them. An empty cell means the field is empty or `null`. `alias_of` is set when the output is a read-only alias of a settable input.

### Variable classes and their lume-base parent

Every `variable_class` on this model is a subclass of one of five lume-base classes in `lume.variables`, which all derive from `Variable`. That parent is what `kind` is computed from, which is why the parent and `kind` columns always agree. `IntVariable` is itself a `ScalarVariable`. This is reference information and is not returned by the API. It was resolved from the source of the pinned lume-base, lume-torch, lume-bmad and virtual-accelerator versions in `pyproject.toml`.

| lume-base parent | `kind` | Outputs |
|---|---|---|
| `ScalarVariable` | `scalar` | 403 |
| `IntVariable` | `scalar` | 10 |
| `NDVariable` | `array` | 45 |
| `EnumVariable` | `value` | 61 |
| `ParticleGroupVariable` | `particles` | 5 |

| `variable_class` | Defined in | lume-base parent | `kind` | Outputs | Example `id` |
|---|---|---|---|---|---|
| `ScalarVariable` | lume-base | `ScalarVariable` | `scalar` | 54 | `QUAD:IN20:121:BACT` |
| `ScreenResolutionVariable` | lume-bmad | `ScalarVariable` | `scalar` | 5 | `OTRS:IN20:571:RESOLUTION` |
| `TorchScalarVariable` | lume-torch | `ScalarVariable` | `scalar` | 10 | `ACCL:IN20:300:L0A_ADES` |
| `BPMTMITDummyVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 12 | `BPMS:IN20:581:TMIT` |
| `BPMXVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 17 | `BPMS:IN20:581:X` |
| `BPMYVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 17 | `BPMS:IN20:581:Y` |
| `BmaxVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 82 | `BEND:IN20:661:BCTRL.DRVH` |
| `BminVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 82 | `BEND:IN20:661:BCTRL.DRVL` |
| `KickerBACTVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 20 | `XCOR:IN20:641:BACT` |
| `KickerBCTRLVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 20 | `XCOR:IN20:641:BDES` |
| `KlystronPACTVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 1 | `KLYS:LI21:11:PACT` |
| `QuadrupoleBACTVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 15 | `QUAD:IN20:631:BACT` |
| `QuadrupoleBCTRLVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 15 | `QUAD:IN20:631:BDES` |
| `SBendBACTVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 6 | `BEND:IN20:661:BACT` |
| `SBendBCTRLVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 6 | `BEND:IN20:661:BDES` |
| `StatusVariable` | virtual-accelerator | `ScalarVariable` | `scalar` | 41 | `BEND:IN20:661:STATCTRLSUB.T` |
| `ScreenImageShapeVariable` | lume-bmad | `IntVariable` | `scalar` | 10 | `OTRS:IN20:571:Image:ArraySize0_RBV` |
| `CombStatVariable` | lume-bmad | `NDVariable` | `array` | 19 | `n_particle_live` |
| `ScreenImageVariable` | lume-bmad | `NDVariable` | `array` | 5 | `OTRS:IN20:571:Image:ArrayData` |
| `StatVariable` | lume-bmad | `NDVariable` | `array` | 21 | `a.alpha` |
| `EnumVariable` | lume-base | `EnumVariable` | `value` | 18 | `QUAD:IN20:121:CTRL` |
| `ControlStateVariable` | virtual-accelerator | `EnumVariable` | `value` | 41 | `BEND:IN20:661:CTRL` |
| `KlystronStatVariable` | virtual-accelerator | `EnumVariable` | `value` | 2 | `KLYS:LI21:11:BEAMCODE1_STAT` |
| `BeamAtElementVariable` | lume-bmad | `ParticleGroupVariable` | `particles` | 5 | `OTR11_beam` |

`TorchScalarVariable` subclasses lume-base `ScalarVariable` through lume-torch, and lume-torch's own `ScalarVariable` is a deprecated alias of it. The `ScalarVariable` outputs on this model are the plain lume-base class, created by the injector surrogate stage. The `*BACT` readback classes subclass their `*BCTRL` setpoint class with a read-only mixin, so a readback inherits the setpoint's unit and element.

### Particles (`particles`, 5)

| `id` | `kind` | `unit` | `shape` | `element_name` | `variable_class` | `alias_of` |
|---|---|---|---|---|---|---|
| `OTR11_beam` | `particles` |  |  | `OTR11` | `BeamAtElementVariable` |  |
| `OTR12_beam` | `particles` |  |  | `OTR12` | `BeamAtElementVariable` |  |
| `OTR2_beam` | `particles` |  |  | `OTR2` | `BeamAtElementVariable` |  |
| `OTR3_beam` | `particles` |  |  | `OTR3` | `BeamAtElementVariable` |  |
| `OTR4_beam` | `particles` |  |  | `OTR4` | `BeamAtElementVariable` |  |

### Arrays (`array`, 45)

| `id` | `kind` | `unit` | `shape` | `element_name` | `variable_class` | `alias_of` |
|---|---|---|---|---|---|---|
| `OTRS:IN20:571:Image:ArrayData` | `array` |  | 1040×1392 | `OTR2` | `ScreenImageVariable` |  |
| `OTRS:IN20:621:Image:ArrayData` | `array` |  | 1040×1392 | `OTR3` | `ScreenImageVariable` |  |
| `OTRS:IN20:711:Image:ArrayData` | `array` |  | 1040×1392 | `OTR4` | `ScreenImageVariable` |  |
| `OTRS:LI21:237:Image:ArrayData` | `array` |  | 1040×1392 | `OTR11` | `ScreenImageVariable` |  |
| `OTRS:LI21:291:Image:ArrayData` | `array` |  | 1040×1392 | `OTR12` | `ScreenImageVariable` |  |
| `a.alpha` | `array` |  | 180 |  | `StatVariable` |  |
| `a.beta` | `array` | `m` | 180 |  | `StatVariable` |  |
| `a.eta` | `array` | `m` | 180 |  | `StatVariable` |  |
| `a.etap` | `array` |  | 180 |  | `StatVariable` |  |
| `a.gamma` | `array` | `1/m` | 180 |  | `StatVariable` |  |
| `a.phi` | `array` |  | 180 |  | `StatVariable` |  |
| `b.alpha` | `array` |  | 180 |  | `StatVariable` |  |
| `b.beta` | `array` | `m` | 180 |  | `StatVariable` |  |
| `b.eta` | `array` | `m` | 180 |  | `StatVariable` |  |
| `b.etap` | `array` |  | 180 |  | `StatVariable` |  |
| `b.gamma` | `array` | `1/m` | 180 |  | `StatVariable` |  |
| `b.phi` | `array` |  | 180 |  | `StatVariable` |  |
| `e_tot` | `array` | `eV` | 180 |  | `StatVariable` |  |
| `ix_branch` | `array` |  | 180 |  | `StatVariable` |  |
| `ix_ele` | `array` |  | 180 |  | `StatVariable` |  |
| `l` | `array` | `m` | 180 |  | `StatVariable` |  |
| `mat6` | `array` |  | 180×6×6 |  | `StatVariable` |  |
| `n_particle_live` | `array` |  | 241 |  | `CombStatVariable` |  |
| `name` | `array` |  | 180 |  | `StatVariable` |  |
| `p0c` | `array` | `eV/c` | 180 |  | `StatVariable` |  |
| `px` | `array` | `rad` | 241 |  | `CombStatVariable` |  |
| `py` | `array` | `rad` | 241 |  | `CombStatVariable` |  |
| `s` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `s_ele` | `array` | `m` | 180 |  | `StatVariable` |  |
| `t` | `array` | `s` | 241 |  | `CombStatVariable` |  |
| `vec0` | `array` | `m` | 180×6 |  | `StatVariable` |  |
| `x` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `x.alpha` | `array` |  | 241 |  | `CombStatVariable` |  |
| `x.beta` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `x.emit` | `array` | `m*rad` | 241 |  | `CombStatVariable` |  |
| `x.eta` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `x.etap` | `array` |  | 241 |  | `CombStatVariable` |  |
| `x.norm_emit` | `array` | `m*rad` | 241 |  | `CombStatVariable` |  |
| `y` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `y.alpha` | `array` |  | 241 |  | `CombStatVariable` |  |
| `y.beta` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `y.emit` | `array` | `m*rad` | 241 |  | `CombStatVariable` |  |
| `y.eta` | `array` | `m` | 241 |  | `CombStatVariable` |  |
| `y.etap` | `array` |  | 241 |  | `CombStatVariable` |  |
| `y.norm_emit` | `array` | `m*rad` | 241 |  | `CombStatVariable` |  |

### Scalars (`scalar`, 413)

| `id` | `kind` | `unit` | `shape` | `element_name` | `variable_class` | `alias_of` |
|---|---|---|---|---|---|---|
| `ACCL:IN20:300:L0A_ADES` | `scalar` | `MV` |  |  | `TorchScalarVariable` |  |
| `ACCL:IN20:400:L0B_ADES` | `scalar` | `MV` |  |  | `TorchScalarVariable` |  |
| `BEND:IN20:661:BACT` | `scalar` | `GeV/c` |  | `BX01` | `SBendBACTVariable` |  |
| `BEND:IN20:661:BCTRL.DRVH` | `scalar` |  |  | `BX01` | `BmaxVariable` |  |
| `BEND:IN20:661:BCTRL.DRVL` | `scalar` |  |  | `BX01` | `BminVariable` |  |
| `BEND:IN20:661:BDES` | `scalar` | `GeV/c` |  | `BX01` | `SBendBCTRLVariable` | `BEND:IN20:661:BCTRL` |
| `BEND:IN20:661:BMAX` | `scalar` |  |  | `BX01` | `BmaxVariable` |  |
| `BEND:IN20:661:BMIN` | `scalar` |  |  | `BX01` | `BminVariable` |  |
| `BEND:IN20:661:STATCTRLSUB.T` | `scalar` |  |  | `BX01` | `StatusVariable` |  |
| `BEND:IN20:751:BACT` | `scalar` | `GeV/c` |  | `BX02` | `SBendBACTVariable` |  |
| `BEND:IN20:751:BCTRL.DRVH` | `scalar` |  |  | `BX02` | `BmaxVariable` |  |
| `BEND:IN20:751:BCTRL.DRVL` | `scalar` |  |  | `BX02` | `BminVariable` |  |
| `BEND:IN20:751:BDES` | `scalar` | `GeV/c` |  | `BX02` | `SBendBCTRLVariable` | `BEND:IN20:751:BCTRL` |
| `BEND:IN20:751:BMAX` | `scalar` |  |  | `BX02` | `BmaxVariable` |  |
| `BEND:IN20:751:BMIN` | `scalar` |  |  | `BX02` | `BminVariable` |  |
| `BEND:IN20:751:STATCTRLSUB.T` | `scalar` |  |  | `BX02` | `StatusVariable` |  |
| `BEND:LI21:215:BACT` | `scalar` | `GeV/c` |  | `BX11` | `SBendBACTVariable` |  |
| `BEND:LI21:215:BCTRL.DRVH` | `scalar` |  |  | `BX11` | `BmaxVariable` |  |
| `BEND:LI21:215:BCTRL.DRVL` | `scalar` |  |  | `BX11` | `BminVariable` |  |
| `BEND:LI21:215:BDES` | `scalar` | `GeV/c` |  | `BX11` | `SBendBCTRLVariable` | `BEND:LI21:215:BCTRL` |
| `BEND:LI21:215:BMAX` | `scalar` |  |  | `BX11` | `BmaxVariable` |  |
| `BEND:LI21:215:BMIN` | `scalar` |  |  | `BX11` | `BminVariable` |  |
| `BEND:LI21:215:STATCTRLSUB.T` | `scalar` |  |  | `BX11` | `StatusVariable` |  |
| `BEND:LI21:231:BACT` | `scalar` | `GeV/c` |  | `BX12` | `SBendBACTVariable` |  |
| `BEND:LI21:231:BCTRL.DRVH` | `scalar` |  |  | `BX12` | `BmaxVariable` |  |
| `BEND:LI21:231:BCTRL.DRVL` | `scalar` |  |  | `BX12` | `BminVariable` |  |
| `BEND:LI21:231:BDES` | `scalar` | `GeV/c` |  | `BX12` | `SBendBCTRLVariable` | `BEND:LI21:231:BCTRL` |
| `BEND:LI21:231:BMAX` | `scalar` |  |  | `BX12` | `BmaxVariable` |  |
| `BEND:LI21:231:BMIN` | `scalar` |  |  | `BX12` | `BminVariable` |  |
| `BEND:LI21:231:STATCTRLSUB.T` | `scalar` |  |  | `BX12` | `StatusVariable` |  |
| `BEND:LI21:241:BACT` | `scalar` | `GeV/c` |  | `BX13` | `SBendBACTVariable` |  |
| `BEND:LI21:241:BCTRL.DRVH` | `scalar` |  |  | `BX13` | `BmaxVariable` |  |
| `BEND:LI21:241:BCTRL.DRVL` | `scalar` |  |  | `BX13` | `BminVariable` |  |
| `BEND:LI21:241:BDES` | `scalar` | `GeV/c` |  | `BX13` | `SBendBCTRLVariable` | `BEND:LI21:241:BCTRL` |
| `BEND:LI21:241:BMAX` | `scalar` |  |  | `BX13` | `BmaxVariable` |  |
| `BEND:LI21:241:BMIN` | `scalar` |  |  | `BX13` | `BminVariable` |  |
| `BEND:LI21:241:STATCTRLSUB.T` | `scalar` |  |  | `BX13` | `StatusVariable` |  |
| `BEND:LI21:261:BACT` | `scalar` | `GeV/c` |  | `BX14` | `SBendBACTVariable` |  |
| `BEND:LI21:261:BCTRL.DRVH` | `scalar` |  |  | `BX14` | `BmaxVariable` |  |
| `BEND:LI21:261:BCTRL.DRVL` | `scalar` |  |  | `BX14` | `BminVariable` |  |
| `BEND:LI21:261:BDES` | `scalar` | `GeV/c` |  | `BX14` | `SBendBCTRLVariable` | `BEND:LI21:261:BCTRL` |
| `BEND:LI21:261:BMAX` | `scalar` |  |  | `BX14` | `BmaxVariable` |  |
| `BEND:LI21:261:BMIN` | `scalar` |  |  | `BX14` | `BminVariable` |  |
| `BEND:LI21:261:STATCTRLSUB.T` | `scalar` |  |  | `BX14` | `StatusVariable` |  |
| `BPMS:IN20:581:TMIT` | `scalar` | `arbitrary units` |  | `BPM10` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:581:X` | `scalar` | `mm` |  | `BPM10` | `BPMXVariable` |  |
| `BPMS:IN20:581:Y` | `scalar` | `mm` |  | `BPM10` | `BPMYVariable` |  |
| `BPMS:IN20:631:TMIT` | `scalar` | `arbitrary units` |  | `BPM11` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:631:X` | `scalar` | `mm` |  | `BPM11` | `BPMXVariable` |  |
| `BPMS:IN20:631:Y` | `scalar` | `mm` |  | `BPM11` | `BPMYVariable` |  |
| `BPMS:IN20:651:TMIT` | `scalar` | `arbitrary units` |  | `BPM12` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:651:X` | `scalar` | `mm` |  | `BPM12` | `BPMXVariable` |  |
| `BPMS:IN20:651:Y` | `scalar` | `mm` |  | `BPM12` | `BPMYVariable` |  |
| `BPMS:IN20:731:TMIT` | `scalar` | `arbitrary units` |  | `BPM13` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:731:X` | `scalar` | `mm` |  | `BPM13` | `BPMXVariable` |  |
| `BPMS:IN20:731:Y` | `scalar` | `mm` |  | `BPM13` | `BPMYVariable` |  |
| `BPMS:IN20:771:TMIT` | `scalar` | `arbitrary units` |  | `BPM14` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:771:X` | `scalar` | `mm` |  | `BPM14` | `BPMXVariable` |  |
| `BPMS:IN20:771:Y` | `scalar` | `mm` |  | `BPM14` | `BPMYVariable` |  |
| `BPMS:IN20:781:TMIT` | `scalar` | `arbitrary units` |  | `BPM15` | `BPMTMITDummyVariable` |  |
| `BPMS:IN20:781:X` | `scalar` | `mm` |  | `BPM15` | `BPMXVariable` |  |
| `BPMS:IN20:781:Y` | `scalar` | `mm` |  | `BPM15` | `BPMYVariable` |  |
| `BPMS:LI21:131:TMIT` | `scalar` | `arbitrary units` |  | `BPMA11` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:131:X` | `scalar` | `mm` |  | `BPMA11` | `BPMXVariable` |  |
| `BPMS:LI21:131:Y` | `scalar` | `mm` |  | `BPMA11` | `BPMYVariable` |  |
| `BPMS:LI21:161:TMIT` | `scalar` | `arbitrary units` |  | `BPMA12` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:161:X` | `scalar` | `mm` |  | `BPMA12` | `BPMXVariable` |  |
| `BPMS:LI21:161:Y` | `scalar` | `mm` |  | `BPMA12` | `BPMYVariable` |  |
| `BPMS:LI21:201:TMIT` | `scalar` | `arbitrary units` |  | `BPM21201` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:201:X` | `scalar` | `mm` |  | `BPM21201` | `BPMXVariable` |  |
| `BPMS:LI21:201:Y` | `scalar` | `mm` |  | `BPM21201` | `BPMYVariable` |  |
| `BPMS:LI21:233:TMIT` | `scalar` | `arbitrary units` |  | `BPMS11` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:233:X` | `scalar` | `mm` |  | `BPMS11` | `BPMXVariable` |  |
| `BPMS:LI21:233:Y` | `scalar` | `mm` |  | `BPMS11` | `BPMYVariable` |  |
| `BPMS:LI21:278:TMIT` | `scalar` | `arbitrary units` |  | `BPMM12` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:278:X` | `scalar` | `mm` |  | `BPMM12` | `BPMXVariable` |  |
| `BPMS:LI21:278:Y` | `scalar` | `mm` |  | `BPMM12` | `BPMYVariable` |  |
| `BPMS:LI21:301:TMIT` | `scalar` | `arbitrary units` |  | `BPM21301` | `BPMTMITDummyVariable` |  |
| `BPMS:LI21:301:X` | `scalar` | `mm` |  | `BPM21301` | `BPMXVariable` |  |
| `BPMS:LI21:301:Y` | `scalar` | `mm` |  | `BPM21301` | `BPMYVariable` |  |
| `CAMR:IN20:186:R_DIST` | `scalar` | `um` |  |  | `TorchScalarVariable` |  |
| `FBCK:BCI0:1:CHRG_S` | `scalar` | `pC` |  |  | `TorchScalarVariable` |  |
| `KLYS:LI21:11:PACT` | `scalar` | `degrees` |  | `K21_1` | `KlystronPACTVariable` |  |
| `OTRS:IN20:571:Image:ArraySize0_RBV` | `scalar` |  | 1040×1392 | `OTR2` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:571:Image:ArraySize1_RBV` | `scalar` |  | 1040×1392 | `OTR2` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:571:RESOLUTION` | `scalar` |  | 1040×1392 | `OTR2` | `ScreenResolutionVariable` |  |
| `OTRS:IN20:571:X` | `scalar` | `mm` |  | `OTR2` | `BPMXVariable` |  |
| `OTRS:IN20:571:XRMS` | `scalar` | `um` |  |  | `TorchScalarVariable` |  |
| `OTRS:IN20:571:Y` | `scalar` | `mm` |  | `OTR2` | `BPMYVariable` |  |
| `OTRS:IN20:571:YRMS` | `scalar` | `um` |  |  | `TorchScalarVariable` |  |
| `OTRS:IN20:621:Image:ArraySize0_RBV` | `scalar` |  | 1040×1392 | `OTR3` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:621:Image:ArraySize1_RBV` | `scalar` |  | 1040×1392 | `OTR3` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:621:RESOLUTION` | `scalar` |  | 1040×1392 | `OTR3` | `ScreenResolutionVariable` |  |
| `OTRS:IN20:621:X` | `scalar` | `mm` |  | `OTR3` | `BPMXVariable` |  |
| `OTRS:IN20:621:Y` | `scalar` | `mm` |  | `OTR3` | `BPMYVariable` |  |
| `OTRS:IN20:711:Image:ArraySize0_RBV` | `scalar` |  | 1040×1392 | `OTR4` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:711:Image:ArraySize1_RBV` | `scalar` |  | 1040×1392 | `OTR4` | `ScreenImageShapeVariable` |  |
| `OTRS:IN20:711:RESOLUTION` | `scalar` |  | 1040×1392 | `OTR4` | `ScreenResolutionVariable` |  |
| `OTRS:IN20:711:X` | `scalar` | `mm` |  | `OTR4` | `BPMXVariable` |  |
| `OTRS:IN20:711:Y` | `scalar` | `mm` |  | `OTR4` | `BPMYVariable` |  |
| `OTRS:LI21:237:Image:ArraySize0_RBV` | `scalar` |  | 1040×1392 | `OTR11` | `ScreenImageShapeVariable` |  |
| `OTRS:LI21:237:Image:ArraySize1_RBV` | `scalar` |  | 1040×1392 | `OTR11` | `ScreenImageShapeVariable` |  |
| `OTRS:LI21:237:RESOLUTION` | `scalar` |  | 1040×1392 | `OTR11` | `ScreenResolutionVariable` |  |
| `OTRS:LI21:237:X` | `scalar` | `mm` |  | `OTR11` | `BPMXVariable` |  |
| `OTRS:LI21:237:Y` | `scalar` | `mm` |  | `OTR11` | `BPMYVariable` |  |
| `OTRS:LI21:291:Image:ArraySize0_RBV` | `scalar` |  | 1040×1392 | `OTR12` | `ScreenImageShapeVariable` |  |
| `OTRS:LI21:291:Image:ArraySize1_RBV` | `scalar` |  | 1040×1392 | `OTR12` | `ScreenImageShapeVariable` |  |
| `OTRS:LI21:291:RESOLUTION` | `scalar` |  | 1040×1392 | `OTR12` | `ScreenResolutionVariable` |  |
| `OTRS:LI21:291:X` | `scalar` | `mm` |  | `OTR12` | `BPMXVariable` |  |
| `OTRS:LI21:291:Y` | `scalar` | `mm` |  | `OTR12` | `BPMYVariable` |  |
| `Pulse_length` | `scalar` | `ps` |  |  | `TorchScalarVariable` |  |
| `QUAD:IN20:121:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:121:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:121:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:121:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:121:BCTRL` |
| `QUAD:IN20:121:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:121:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:122:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:122:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:122:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:122:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:122:BCTRL` |
| `QUAD:IN20:122:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:122:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:361:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:361:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:361:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:361:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:361:BCTRL` |
| `QUAD:IN20:361:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:361:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:371:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:371:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:371:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:371:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:371:BCTRL` |
| `QUAD:IN20:371:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:371:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:425:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:425:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:425:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:425:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:425:BCTRL` |
| `QUAD:IN20:425:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:425:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:441:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:441:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:441:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:441:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:441:BCTRL` |
| `QUAD:IN20:441:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:441:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:511:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:511:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:511:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:511:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:511:BCTRL` |
| `QUAD:IN20:511:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:511:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:525:BACT` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:525:BCTRL.DRVH` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:525:BCTRL.DRVL` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:525:BDES` | `scalar` | `kG` |  |  | `ScalarVariable` | `QUAD:IN20:525:BCTRL` |
| `QUAD:IN20:525:BMAX` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:525:BMIN` | `scalar` | `kG` |  |  | `ScalarVariable` |  |
| `QUAD:IN20:631:BACT` | `scalar` | `kG` |  | `QM01` | `QuadrupoleBACTVariable` |  |
| `QUAD:IN20:631:BCTRL.DRVH` | `scalar` |  |  | `QM01` | `BmaxVariable` |  |
| `QUAD:IN20:631:BCTRL.DRVL` | `scalar` |  |  | `QM01` | `BminVariable` |  |
| `QUAD:IN20:631:BDES` | `scalar` | `kG` |  | `QM01` | `QuadrupoleBCTRLVariable` | `QUAD:IN20:631:BCTRL` |
| `QUAD:IN20:631:BMAX` | `scalar` |  |  | `QM01` | `BmaxVariable` |  |
| `QUAD:IN20:631:BMIN` | `scalar` |  |  | `QM01` | `BminVariable` |  |
| `QUAD:IN20:631:STATCTRLSUB.T` | `scalar` |  |  | `QM01` | `StatusVariable` |  |
| `QUAD:IN20:651:BACT` | `scalar` | `kG` |  | `QM02` | `QuadrupoleBACTVariable` |  |
| `QUAD:IN20:651:BCTRL.DRVH` | `scalar` |  |  | `QM02` | `BmaxVariable` |  |
| `QUAD:IN20:651:BCTRL.DRVL` | `scalar` |  |  | `QM02` | `BminVariable` |  |
| `QUAD:IN20:651:BDES` | `scalar` | `kG` |  | `QM02` | `QuadrupoleBCTRLVariable` | `QUAD:IN20:651:BCTRL` |
| `QUAD:IN20:651:BMAX` | `scalar` |  |  | `QM02` | `BmaxVariable` |  |
| `QUAD:IN20:651:BMIN` | `scalar` |  |  | `QM02` | `BminVariable` |  |
| `QUAD:IN20:651:STATCTRLSUB.T` | `scalar` |  |  | `QM02` | `StatusVariable` |  |
| `QUAD:IN20:731:BACT` | `scalar` | `kG` |  | `QB` | `QuadrupoleBACTVariable` |  |
| `QUAD:IN20:731:BCTRL.DRVH` | `scalar` |  |  | `QB` | `BmaxVariable` |  |
| `QUAD:IN20:731:BCTRL.DRVL` | `scalar` |  |  | `QB` | `BminVariable` |  |
| `QUAD:IN20:731:BDES` | `scalar` | `kG` |  | `QB` | `QuadrupoleBCTRLVariable` | `QUAD:IN20:731:BCTRL` |
| `QUAD:IN20:731:BMAX` | `scalar` |  |  | `QB` | `BmaxVariable` |  |
| `QUAD:IN20:731:BMIN` | `scalar` |  |  | `QB` | `BminVariable` |  |
| `QUAD:IN20:731:STATCTRLSUB.T` | `scalar` |  |  | `QB` | `StatusVariable` |  |
| `QUAD:IN20:771:BACT` | `scalar` | `kG` |  | `QM03` | `QuadrupoleBACTVariable` |  |
| `QUAD:IN20:771:BCTRL.DRVH` | `scalar` |  |  | `QM03` | `BmaxVariable` |  |
| `QUAD:IN20:771:BCTRL.DRVL` | `scalar` |  |  | `QM03` | `BminVariable` |  |
| `QUAD:IN20:771:BDES` | `scalar` | `kG` |  | `QM03` | `QuadrupoleBCTRLVariable` | `QUAD:IN20:771:BCTRL` |
| `QUAD:IN20:771:BMAX` | `scalar` |  |  | `QM03` | `BmaxVariable` |  |
| `QUAD:IN20:771:BMIN` | `scalar` |  |  | `QM03` | `BminVariable` |  |
| `QUAD:IN20:771:STATCTRLSUB.T` | `scalar` |  |  | `QM03` | `StatusVariable` |  |
| `QUAD:IN20:781:BACT` | `scalar` | `kG` |  | `QM04` | `QuadrupoleBACTVariable` |  |
| `QUAD:IN20:781:BCTRL.DRVH` | `scalar` |  |  | `QM04` | `BmaxVariable` |  |
| `QUAD:IN20:781:BCTRL.DRVL` | `scalar` |  |  | `QM04` | `BminVariable` |  |
| `QUAD:IN20:781:BDES` | `scalar` | `kG` |  | `QM04` | `QuadrupoleBCTRLVariable` | `QUAD:IN20:781:BCTRL` |
| `QUAD:IN20:781:BMAX` | `scalar` |  |  | `QM04` | `BmaxVariable` |  |
| `QUAD:IN20:781:BMIN` | `scalar` |  |  | `QM04` | `BminVariable` |  |
| `QUAD:IN20:781:STATCTRLSUB.T` | `scalar` |  |  | `QM04` | `StatusVariable` |  |
| `QUAD:LI21:131:BACT` | `scalar` | `kG` |  | `QA11` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:131:BCTRL.DRVH` | `scalar` |  |  | `QA11` | `BmaxVariable` |  |
| `QUAD:LI21:131:BCTRL.DRVL` | `scalar` |  |  | `QA11` | `BminVariable` |  |
| `QUAD:LI21:131:BDES` | `scalar` | `kG` |  | `QA11` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:131:BCTRL` |
| `QUAD:LI21:131:BMAX` | `scalar` |  |  | `QA11` | `BmaxVariable` |  |
| `QUAD:LI21:131:BMIN` | `scalar` |  |  | `QA11` | `BminVariable` |  |
| `QUAD:LI21:131:STATCTRLSUB.T` | `scalar` |  |  | `QA11` | `StatusVariable` |  |
| `QUAD:LI21:161:BACT` | `scalar` | `kG` |  | `QA12` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:161:BCTRL.DRVH` | `scalar` |  |  | `QA12` | `BmaxVariable` |  |
| `QUAD:LI21:161:BCTRL.DRVL` | `scalar` |  |  | `QA12` | `BminVariable` |  |
| `QUAD:LI21:161:BDES` | `scalar` | `kG` |  | `QA12` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:161:BCTRL` |
| `QUAD:LI21:161:BMAX` | `scalar` |  |  | `QA12` | `BmaxVariable` |  |
| `QUAD:LI21:161:BMIN` | `scalar` |  |  | `QA12` | `BminVariable` |  |
| `QUAD:LI21:161:STATCTRLSUB.T` | `scalar` |  |  | `QA12` | `StatusVariable` |  |
| `QUAD:LI21:201:BACT` | `scalar` | `kG` |  | `Q21201` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:201:BCTRL.DRVH` | `scalar` |  |  | `Q21201` | `BmaxVariable` |  |
| `QUAD:LI21:201:BCTRL.DRVL` | `scalar` |  |  | `Q21201` | `BminVariable` |  |
| `QUAD:LI21:201:BDES` | `scalar` | `kG` |  | `Q21201` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:201:BCTRL` |
| `QUAD:LI21:201:BMAX` | `scalar` |  |  | `Q21201` | `BmaxVariable` |  |
| `QUAD:LI21:201:BMIN` | `scalar` |  |  | `Q21201` | `BminVariable` |  |
| `QUAD:LI21:201:STATCTRLSUB.T` | `scalar` |  |  | `Q21201` | `StatusVariable` |  |
| `QUAD:LI21:211:BACT` | `scalar` | `kG` |  | `QM11` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:211:BCTRL.DRVH` | `scalar` |  |  | `QM11` | `BmaxVariable` |  |
| `QUAD:LI21:211:BCTRL.DRVL` | `scalar` |  |  | `QM11` | `BminVariable` |  |
| `QUAD:LI21:211:BDES` | `scalar` | `kG` |  | `QM11` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:211:BCTRL` |
| `QUAD:LI21:211:BMAX` | `scalar` |  |  | `QM11` | `BmaxVariable` |  |
| `QUAD:LI21:211:BMIN` | `scalar` |  |  | `QM11` | `BminVariable` |  |
| `QUAD:LI21:211:STATCTRLSUB.T` | `scalar` |  |  | `QM11` | `StatusVariable` |  |
| `QUAD:LI21:221:BACT` | `scalar` | `kG` |  | `CQ11` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:221:BCTRL.DRVH` | `scalar` |  |  | `CQ11` | `BmaxVariable` |  |
| `QUAD:LI21:221:BCTRL.DRVL` | `scalar` |  |  | `CQ11` | `BminVariable` |  |
| `QUAD:LI21:221:BDES` | `scalar` | `kG` |  | `CQ11` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:221:BCTRL` |
| `QUAD:LI21:221:BMAX` | `scalar` |  |  | `CQ11` | `BmaxVariable` |  |
| `QUAD:LI21:221:BMIN` | `scalar` |  |  | `CQ11` | `BminVariable` |  |
| `QUAD:LI21:221:STATCTRLSUB.T` | `scalar` |  |  | `CQ11` | `StatusVariable` |  |
| `QUAD:LI21:243:BACT` | `scalar` | `kG` |  | `SQ13` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:243:BCTRL.DRVH` | `scalar` |  |  | `SQ13` | `BmaxVariable` |  |
| `QUAD:LI21:243:BCTRL.DRVL` | `scalar` |  |  | `SQ13` | `BminVariable` |  |
| `QUAD:LI21:243:BDES` | `scalar` | `kG` |  | `SQ13` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:243:BCTRL` |
| `QUAD:LI21:243:BMAX` | `scalar` |  |  | `SQ13` | `BmaxVariable` |  |
| `QUAD:LI21:243:BMIN` | `scalar` |  |  | `SQ13` | `BminVariable` |  |
| `QUAD:LI21:243:STATCTRLSUB.T` | `scalar` |  |  | `SQ13` | `StatusVariable` |  |
| `QUAD:LI21:251:BACT` | `scalar` | `kG` |  | `CQ12` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:251:BCTRL.DRVH` | `scalar` |  |  | `CQ12` | `BmaxVariable` |  |
| `QUAD:LI21:251:BCTRL.DRVL` | `scalar` |  |  | `CQ12` | `BminVariable` |  |
| `QUAD:LI21:251:BDES` | `scalar` | `kG` |  | `CQ12` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:251:BCTRL` |
| `QUAD:LI21:251:BMAX` | `scalar` |  |  | `CQ12` | `BmaxVariable` |  |
| `QUAD:LI21:251:BMIN` | `scalar` |  |  | `CQ12` | `BminVariable` |  |
| `QUAD:LI21:251:STATCTRLSUB.T` | `scalar` |  |  | `CQ12` | `StatusVariable` |  |
| `QUAD:LI21:271:BACT` | `scalar` | `kG` |  | `QM12` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:271:BCTRL.DRVH` | `scalar` |  |  | `QM12` | `BmaxVariable` |  |
| `QUAD:LI21:271:BCTRL.DRVL` | `scalar` |  |  | `QM12` | `BminVariable` |  |
| `QUAD:LI21:271:BDES` | `scalar` | `kG` |  | `QM12` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:271:BCTRL` |
| `QUAD:LI21:271:BMAX` | `scalar` |  |  | `QM12` | `BmaxVariable` |  |
| `QUAD:LI21:271:BMIN` | `scalar` |  |  | `QM12` | `BminVariable` |  |
| `QUAD:LI21:271:STATCTRLSUB.T` | `scalar` |  |  | `QM12` | `StatusVariable` |  |
| `QUAD:LI21:278:BACT` | `scalar` | `kG` |  | `QM13` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:278:BCTRL.DRVH` | `scalar` |  |  | `QM13` | `BmaxVariable` |  |
| `QUAD:LI21:278:BCTRL.DRVL` | `scalar` |  |  | `QM13` | `BminVariable` |  |
| `QUAD:LI21:278:BDES` | `scalar` | `kG` |  | `QM13` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:278:BCTRL` |
| `QUAD:LI21:278:BMAX` | `scalar` |  |  | `QM13` | `BmaxVariable` |  |
| `QUAD:LI21:278:BMIN` | `scalar` |  |  | `QM13` | `BminVariable` |  |
| `QUAD:LI21:278:STATCTRLSUB.T` | `scalar` |  |  | `QM13` | `StatusVariable` |  |
| `QUAD:LI21:301:BACT` | `scalar` | `kG` |  | `Q21301` | `QuadrupoleBACTVariable` |  |
| `QUAD:LI21:301:BCTRL.DRVH` | `scalar` |  |  | `Q21301` | `BmaxVariable` |  |
| `QUAD:LI21:301:BCTRL.DRVL` | `scalar` |  |  | `Q21301` | `BminVariable` |  |
| `QUAD:LI21:301:BDES` | `scalar` | `kG` |  | `Q21301` | `QuadrupoleBCTRLVariable` | `QUAD:LI21:301:BCTRL` |
| `QUAD:LI21:301:BMAX` | `scalar` |  |  | `Q21301` | `BmaxVariable` |  |
| `QUAD:LI21:301:BMIN` | `scalar` |  |  | `Q21301` | `BminVariable` |  |
| `QUAD:LI21:301:STATCTRLSUB.T` | `scalar` |  |  | `Q21301` | `StatusVariable` |  |
| `SOLN:IN20:121:BACT` | `scalar` | `kG*m` |  |  | `ScalarVariable` |  |
| `SOLN:IN20:121:BCTRL.DRVH` | `scalar` | `kG*m` |  |  | `ScalarVariable` |  |
| `SOLN:IN20:121:BCTRL.DRVL` | `scalar` | `kG*m` |  |  | `ScalarVariable` |  |
| `SOLN:IN20:121:BDES` | `scalar` | `kG*m` |  |  | `ScalarVariable` | `SOLN:IN20:121:BCTRL` |
| `SOLN:IN20:121:BMAX` | `scalar` | `kG*m` |  |  | `ScalarVariable` |  |
| `SOLN:IN20:121:BMIN` | `scalar` | `kG*m` |  |  | `ScalarVariable` |  |
| `XCOR:IN20:641:BACT` | `scalar` |  |  | `XC08` | `KickerBACTVariable` |  |
| `XCOR:IN20:641:BCTRL.DRVH` | `scalar` |  |  | `XC08` | `BmaxVariable` |  |
| `XCOR:IN20:641:BCTRL.DRVL` | `scalar` |  |  | `XC08` | `BminVariable` |  |
| `XCOR:IN20:641:BDES` | `scalar` |  |  | `XC08` | `KickerBCTRLVariable` | `XCOR:IN20:641:BCTRL` |
| `XCOR:IN20:641:BMAX` | `scalar` |  |  | `XC08` | `BmaxVariable` |  |
| `XCOR:IN20:641:BMIN` | `scalar` |  |  | `XC08` | `BminVariable` |  |
| `XCOR:IN20:641:STATCTRLSUB.T` | `scalar` |  |  | `XC08` | `StatusVariable` |  |
| `XCOR:IN20:721:BACT` | `scalar` |  |  | `XC09` | `KickerBACTVariable` |  |
| `XCOR:IN20:721:BCTRL.DRVH` | `scalar` |  |  | `XC09` | `BmaxVariable` |  |
| `XCOR:IN20:721:BCTRL.DRVL` | `scalar` |  |  | `XC09` | `BminVariable` |  |
| `XCOR:IN20:721:BDES` | `scalar` |  |  | `XC09` | `KickerBCTRLVariable` | `XCOR:IN20:721:BCTRL` |
| `XCOR:IN20:721:BMAX` | `scalar` |  |  | `XC09` | `BmaxVariable` |  |
| `XCOR:IN20:721:BMIN` | `scalar` |  |  | `XC09` | `BminVariable` |  |
| `XCOR:IN20:721:STATCTRLSUB.T` | `scalar` |  |  | `XC09` | `StatusVariable` |  |
| `XCOR:IN20:761:BACT` | `scalar` |  |  | `XC10` | `KickerBACTVariable` |  |
| `XCOR:IN20:761:BCTRL.DRVH` | `scalar` |  |  | `XC10` | `BmaxVariable` |  |
| `XCOR:IN20:761:BCTRL.DRVL` | `scalar` |  |  | `XC10` | `BminVariable` |  |
| `XCOR:IN20:761:BDES` | `scalar` |  |  | `XC10` | `KickerBCTRLVariable` | `XCOR:IN20:761:BCTRL` |
| `XCOR:IN20:761:BMAX` | `scalar` |  |  | `XC10` | `BmaxVariable` |  |
| `XCOR:IN20:761:BMIN` | `scalar` |  |  | `XC10` | `BminVariable` |  |
| `XCOR:IN20:761:STATCTRLSUB.T` | `scalar` |  |  | `XC10` | `StatusVariable` |  |
| `XCOR:LI21:101:BACT` | `scalar` |  |  | `XC21101` | `KickerBACTVariable` |  |
| `XCOR:LI21:101:BCTRL.DRVH` | `scalar` |  |  | `XC21101` | `BmaxVariable` |  |
| `XCOR:LI21:101:BCTRL.DRVL` | `scalar` |  |  | `XC21101` | `BminVariable` |  |
| `XCOR:LI21:101:BDES` | `scalar` |  |  | `XC21101` | `KickerBCTRLVariable` | `XCOR:LI21:101:BCTRL` |
| `XCOR:LI21:101:BMAX` | `scalar` |  |  | `XC21101` | `BmaxVariable` |  |
| `XCOR:LI21:101:BMIN` | `scalar` |  |  | `XC21101` | `BminVariable` |  |
| `XCOR:LI21:101:STATCTRLSUB.T` | `scalar` |  |  | `XC21101` | `StatusVariable` |  |
| `XCOR:LI21:135:BACT` | `scalar` |  |  | `XC21135` | `KickerBACTVariable` |  |
| `XCOR:LI21:135:BCTRL.DRVH` | `scalar` |  |  | `XC21135` | `BmaxVariable` |  |
| `XCOR:LI21:135:BCTRL.DRVL` | `scalar` |  |  | `XC21135` | `BminVariable` |  |
| `XCOR:LI21:135:BDES` | `scalar` |  |  | `XC21135` | `KickerBCTRLVariable` | `XCOR:LI21:135:BCTRL` |
| `XCOR:LI21:135:BMAX` | `scalar` |  |  | `XC21135` | `BmaxVariable` |  |
| `XCOR:LI21:135:BMIN` | `scalar` |  |  | `XC21135` | `BminVariable` |  |
| `XCOR:LI21:135:STATCTRLSUB.T` | `scalar` |  |  | `XC21135` | `StatusVariable` |  |
| `XCOR:LI21:165:BACT` | `scalar` |  |  | `XC21165` | `KickerBACTVariable` |  |
| `XCOR:LI21:165:BCTRL.DRVH` | `scalar` |  |  | `XC21165` | `BmaxVariable` |  |
| `XCOR:LI21:165:BCTRL.DRVL` | `scalar` |  |  | `XC21165` | `BminVariable` |  |
| `XCOR:LI21:165:BDES` | `scalar` |  |  | `XC21165` | `KickerBCTRLVariable` | `XCOR:LI21:165:BCTRL` |
| `XCOR:LI21:165:BMAX` | `scalar` |  |  | `XC21165` | `BmaxVariable` |  |
| `XCOR:LI21:165:BMIN` | `scalar` |  |  | `XC21165` | `BminVariable` |  |
| `XCOR:LI21:165:STATCTRLSUB.T` | `scalar` |  |  | `XC21165` | `StatusVariable` |  |
| `XCOR:LI21:175:BACT` | `scalar` |  |  | `XC21175` | `KickerBACTVariable` |  |
| `XCOR:LI21:175:BCTRL.DRVH` | `scalar` |  |  | `XC21175` | `BmaxVariable` |  |
| `XCOR:LI21:175:BCTRL.DRVL` | `scalar` |  |  | `XC21175` | `BminVariable` |  |
| `XCOR:LI21:175:BDES` | `scalar` |  |  | `XC21175` | `KickerBCTRLVariable` | `XCOR:LI21:175:BCTRL` |
| `XCOR:LI21:175:BMAX` | `scalar` |  |  | `XC21175` | `BmaxVariable` |  |
| `XCOR:LI21:175:BMIN` | `scalar` |  |  | `XC21175` | `BminVariable` |  |
| `XCOR:LI21:175:STATCTRLSUB.T` | `scalar` |  |  | `XC21175` | `StatusVariable` |  |
| `XCOR:LI21:191:BACT` | `scalar` |  |  | `XC21191` | `KickerBACTVariable` |  |
| `XCOR:LI21:191:BCTRL.DRVH` | `scalar` |  |  | `XC21191` | `BmaxVariable` |  |
| `XCOR:LI21:191:BCTRL.DRVL` | `scalar` |  |  | `XC21191` | `BminVariable` |  |
| `XCOR:LI21:191:BDES` | `scalar` |  |  | `XC21191` | `KickerBCTRLVariable` | `XCOR:LI21:191:BCTRL` |
| `XCOR:LI21:191:BMAX` | `scalar` |  |  | `XC21191` | `BmaxVariable` |  |
| `XCOR:LI21:191:BMIN` | `scalar` |  |  | `XC21191` | `BminVariable` |  |
| `XCOR:LI21:191:STATCTRLSUB.T` | `scalar` |  |  | `XC21191` | `StatusVariable` |  |
| `XCOR:LI21:275:BACT` | `scalar` |  |  | `XC21275` | `KickerBACTVariable` |  |
| `XCOR:LI21:275:BCTRL.DRVH` | `scalar` |  |  | `XC21275` | `BmaxVariable` |  |
| `XCOR:LI21:275:BCTRL.DRVL` | `scalar` |  |  | `XC21275` | `BminVariable` |  |
| `XCOR:LI21:275:BDES` | `scalar` |  |  | `XC21275` | `KickerBCTRLVariable` | `XCOR:LI21:275:BCTRL` |
| `XCOR:LI21:275:BMAX` | `scalar` |  |  | `XC21275` | `BmaxVariable` |  |
| `XCOR:LI21:275:BMIN` | `scalar` |  |  | `XC21275` | `BminVariable` |  |
| `XCOR:LI21:275:STATCTRLSUB.T` | `scalar` |  |  | `XC21275` | `StatusVariable` |  |
| `XCOR:LI21:302:BACT` | `scalar` |  |  | `XC21302` | `KickerBACTVariable` |  |
| `XCOR:LI21:302:BCTRL.DRVH` | `scalar` |  |  | `XC21302` | `BmaxVariable` |  |
| `XCOR:LI21:302:BCTRL.DRVL` | `scalar` |  |  | `XC21302` | `BminVariable` |  |
| `XCOR:LI21:302:BDES` | `scalar` |  |  | `XC21302` | `KickerBCTRLVariable` | `XCOR:LI21:302:BCTRL` |
| `XCOR:LI21:302:BMAX` | `scalar` |  |  | `XC21302` | `BmaxVariable` |  |
| `XCOR:LI21:302:BMIN` | `scalar` |  |  | `XC21302` | `BminVariable` |  |
| `XCOR:LI21:302:STATCTRLSUB.T` | `scalar` |  |  | `XC21302` | `StatusVariable` |  |
| `YCOR:IN20:642:BACT` | `scalar` |  |  | `YC08` | `KickerBACTVariable` |  |
| `YCOR:IN20:642:BCTRL.DRVH` | `scalar` |  |  | `YC08` | `BmaxVariable` |  |
| `YCOR:IN20:642:BCTRL.DRVL` | `scalar` |  |  | `YC08` | `BminVariable` |  |
| `YCOR:IN20:642:BDES` | `scalar` |  |  | `YC08` | `KickerBCTRLVariable` | `YCOR:IN20:642:BCTRL` |
| `YCOR:IN20:642:BMAX` | `scalar` |  |  | `YC08` | `BmaxVariable` |  |
| `YCOR:IN20:642:BMIN` | `scalar` |  |  | `YC08` | `BminVariable` |  |
| `YCOR:IN20:642:STATCTRLSUB.T` | `scalar` |  |  | `YC08` | `StatusVariable` |  |
| `YCOR:IN20:722:BACT` | `scalar` |  |  | `YC09` | `KickerBACTVariable` |  |
| `YCOR:IN20:722:BCTRL.DRVH` | `scalar` |  |  | `YC09` | `BmaxVariable` |  |
| `YCOR:IN20:722:BCTRL.DRVL` | `scalar` |  |  | `YC09` | `BminVariable` |  |
| `YCOR:IN20:722:BDES` | `scalar` |  |  | `YC09` | `KickerBCTRLVariable` | `YCOR:IN20:722:BCTRL` |
| `YCOR:IN20:722:BMAX` | `scalar` |  |  | `YC09` | `BmaxVariable` |  |
| `YCOR:IN20:722:BMIN` | `scalar` |  |  | `YC09` | `BminVariable` |  |
| `YCOR:IN20:722:STATCTRLSUB.T` | `scalar` |  |  | `YC09` | `StatusVariable` |  |
| `YCOR:IN20:762:BACT` | `scalar` |  |  | `YC10` | `KickerBACTVariable` |  |
| `YCOR:IN20:762:BCTRL.DRVH` | `scalar` |  |  | `YC10` | `BmaxVariable` |  |
| `YCOR:IN20:762:BCTRL.DRVL` | `scalar` |  |  | `YC10` | `BminVariable` |  |
| `YCOR:IN20:762:BDES` | `scalar` |  |  | `YC10` | `KickerBCTRLVariable` | `YCOR:IN20:762:BCTRL` |
| `YCOR:IN20:762:BMAX` | `scalar` |  |  | `YC10` | `BmaxVariable` |  |
| `YCOR:IN20:762:BMIN` | `scalar` |  |  | `YC10` | `BminVariable` |  |
| `YCOR:IN20:762:STATCTRLSUB.T` | `scalar` |  |  | `YC10` | `StatusVariable` |  |
| `YCOR:LI21:102:BACT` | `scalar` |  |  | `YC21102` | `KickerBACTVariable` |  |
| `YCOR:LI21:102:BCTRL.DRVH` | `scalar` |  |  | `YC21102` | `BmaxVariable` |  |
| `YCOR:LI21:102:BCTRL.DRVL` | `scalar` |  |  | `YC21102` | `BminVariable` |  |
| `YCOR:LI21:102:BDES` | `scalar` |  |  | `YC21102` | `KickerBCTRLVariable` | `YCOR:LI21:102:BCTRL` |
| `YCOR:LI21:102:BMAX` | `scalar` |  |  | `YC21102` | `BmaxVariable` |  |
| `YCOR:LI21:102:BMIN` | `scalar` |  |  | `YC21102` | `BminVariable` |  |
| `YCOR:LI21:102:STATCTRLSUB.T` | `scalar` |  |  | `YC21102` | `StatusVariable` |  |
| `YCOR:LI21:136:BACT` | `scalar` |  |  | `YC21136` | `KickerBACTVariable` |  |
| `YCOR:LI21:136:BCTRL.DRVH` | `scalar` |  |  | `YC21136` | `BmaxVariable` |  |
| `YCOR:LI21:136:BCTRL.DRVL` | `scalar` |  |  | `YC21136` | `BminVariable` |  |
| `YCOR:LI21:136:BDES` | `scalar` |  |  | `YC21136` | `KickerBCTRLVariable` | `YCOR:LI21:136:BCTRL` |
| `YCOR:LI21:136:BMAX` | `scalar` |  |  | `YC21136` | `BmaxVariable` |  |
| `YCOR:LI21:136:BMIN` | `scalar` |  |  | `YC21136` | `BminVariable` |  |
| `YCOR:LI21:136:STATCTRLSUB.T` | `scalar` |  |  | `YC21136` | `StatusVariable` |  |
| `YCOR:LI21:166:BACT` | `scalar` |  |  | `YC21166` | `KickerBACTVariable` |  |
| `YCOR:LI21:166:BCTRL.DRVH` | `scalar` |  |  | `YC21166` | `BmaxVariable` |  |
| `YCOR:LI21:166:BCTRL.DRVL` | `scalar` |  |  | `YC21166` | `BminVariable` |  |
| `YCOR:LI21:166:BDES` | `scalar` |  |  | `YC21166` | `KickerBCTRLVariable` | `YCOR:LI21:166:BCTRL` |
| `YCOR:LI21:166:BMAX` | `scalar` |  |  | `YC21166` | `BmaxVariable` |  |
| `YCOR:LI21:166:BMIN` | `scalar` |  |  | `YC21166` | `BminVariable` |  |
| `YCOR:LI21:166:STATCTRLSUB.T` | `scalar` |  |  | `YC21166` | `StatusVariable` |  |
| `YCOR:LI21:174:BACT` | `scalar` |  |  | `YC21174` | `KickerBACTVariable` |  |
| `YCOR:LI21:174:BCTRL.DRVH` | `scalar` |  |  | `YC21174` | `BmaxVariable` |  |
| `YCOR:LI21:174:BCTRL.DRVL` | `scalar` |  |  | `YC21174` | `BminVariable` |  |
| `YCOR:LI21:174:BDES` | `scalar` |  |  | `YC21174` | `KickerBCTRLVariable` | `YCOR:LI21:174:BCTRL` |
| `YCOR:LI21:174:BMAX` | `scalar` |  |  | `YC21174` | `BmaxVariable` |  |
| `YCOR:LI21:174:BMIN` | `scalar` |  |  | `YC21174` | `BminVariable` |  |
| `YCOR:LI21:174:STATCTRLSUB.T` | `scalar` |  |  | `YC21174` | `StatusVariable` |  |
| `YCOR:LI21:192:BACT` | `scalar` |  |  | `YC21192` | `KickerBACTVariable` |  |
| `YCOR:LI21:192:BCTRL.DRVH` | `scalar` |  |  | `YC21192` | `BmaxVariable` |  |
| `YCOR:LI21:192:BCTRL.DRVL` | `scalar` |  |  | `YC21192` | `BminVariable` |  |
| `YCOR:LI21:192:BDES` | `scalar` |  |  | `YC21192` | `KickerBCTRLVariable` | `YCOR:LI21:192:BCTRL` |
| `YCOR:LI21:192:BMAX` | `scalar` |  |  | `YC21192` | `BmaxVariable` |  |
| `YCOR:LI21:192:BMIN` | `scalar` |  |  | `YC21192` | `BminVariable` |  |
| `YCOR:LI21:192:STATCTRLSUB.T` | `scalar` |  |  | `YC21192` | `StatusVariable` |  |
| `YCOR:LI21:276:BACT` | `scalar` |  |  | `YC21276` | `KickerBACTVariable` |  |
| `YCOR:LI21:276:BCTRL.DRVH` | `scalar` |  |  | `YC21276` | `BmaxVariable` |  |
| `YCOR:LI21:276:BCTRL.DRVL` | `scalar` |  |  | `YC21276` | `BminVariable` |  |
| `YCOR:LI21:276:BDES` | `scalar` |  |  | `YC21276` | `KickerBCTRLVariable` | `YCOR:LI21:276:BCTRL` |
| `YCOR:LI21:276:BMAX` | `scalar` |  |  | `YC21276` | `BmaxVariable` |  |
| `YCOR:LI21:276:BMIN` | `scalar` |  |  | `YC21276` | `BminVariable` |  |
| `YCOR:LI21:276:STATCTRLSUB.T` | `scalar` |  |  | `YC21276` | `StatusVariable` |  |
| `YCOR:LI21:303:BACT` | `scalar` |  |  | `YC21303` | `KickerBACTVariable` |  |
| `YCOR:LI21:303:BCTRL.DRVH` | `scalar` |  |  | `YC21303` | `BmaxVariable` |  |
| `YCOR:LI21:303:BCTRL.DRVL` | `scalar` |  |  | `YC21303` | `BminVariable` |  |
| `YCOR:LI21:303:BDES` | `scalar` |  |  | `YC21303` | `KickerBCTRLVariable` | `YCOR:LI21:303:BCTRL` |
| `YCOR:LI21:303:BMAX` | `scalar` |  |  | `YC21303` | `BmaxVariable` |  |
| `YCOR:LI21:303:BMIN` | `scalar` |  |  | `YC21303` | `BminVariable` |  |
| `YCOR:LI21:303:STATCTRLSUB.T` | `scalar` |  |  | `YC21303` | `StatusVariable` |  |
| `norm_emit_x` | `scalar` | `m.rad` |  |  | `TorchScalarVariable` |  |
| `norm_emit_y` | `scalar` | `m.rad` |  |  | `TorchScalarVariable` |  |
| `sigma_z` | `scalar` | `m` |  |  | `TorchScalarVariable` |  |

### Values (`value`, 61)

| `id` | `kind` | `unit` | `shape` | `element_name` | `variable_class` | `alias_of` |
|---|---|---|---|---|---|---|
| `BEND:IN20:661:CTRL` | `value` |  |  | `BX01` | `ControlStateVariable` |  |
| `BEND:IN20:751:CTRL` | `value` |  |  | `BX02` | `ControlStateVariable` |  |
| `BEND:LI21:215:CTRL` | `value` |  |  | `BX11` | `ControlStateVariable` |  |
| `BEND:LI21:231:CTRL` | `value` |  |  | `BX12` | `ControlStateVariable` |  |
| `BEND:LI21:241:CTRL` | `value` |  |  | `BX13` | `ControlStateVariable` |  |
| `BEND:LI21:261:CTRL` | `value` |  |  | `BX14` | `ControlStateVariable` |  |
| `KLYS:LI21:11:BEAMCODE1_STAT` | `value` |  |  | `K21_1` | `KlystronStatVariable` |  |
| `KLYS:LI21:11:BEAMCODE2_STAT` | `value` |  |  | `K21_1` | `KlystronStatVariable` |  |
| `QUAD:IN20:121:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:121:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:122:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:122:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:361:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:361:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:371:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:371:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:425:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:425:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:441:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:441:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:511:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:511:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:525:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:525:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `QUAD:IN20:631:CTRL` | `value` |  |  | `QM01` | `ControlStateVariable` |  |
| `QUAD:IN20:651:CTRL` | `value` |  |  | `QM02` | `ControlStateVariable` |  |
| `QUAD:IN20:731:CTRL` | `value` |  |  | `QB` | `ControlStateVariable` |  |
| `QUAD:IN20:771:CTRL` | `value` |  |  | `QM03` | `ControlStateVariable` |  |
| `QUAD:IN20:781:CTRL` | `value` |  |  | `QM04` | `ControlStateVariable` |  |
| `QUAD:LI21:131:CTRL` | `value` |  |  | `QA11` | `ControlStateVariable` |  |
| `QUAD:LI21:161:CTRL` | `value` |  |  | `QA12` | `ControlStateVariable` |  |
| `QUAD:LI21:201:CTRL` | `value` |  |  | `Q21201` | `ControlStateVariable` |  |
| `QUAD:LI21:211:CTRL` | `value` |  |  | `QM11` | `ControlStateVariable` |  |
| `QUAD:LI21:221:CTRL` | `value` |  |  | `CQ11` | `ControlStateVariable` |  |
| `QUAD:LI21:243:CTRL` | `value` |  |  | `SQ13` | `ControlStateVariable` |  |
| `QUAD:LI21:251:CTRL` | `value` |  |  | `CQ12` | `ControlStateVariable` |  |
| `QUAD:LI21:271:CTRL` | `value` |  |  | `QM12` | `ControlStateVariable` |  |
| `QUAD:LI21:278:CTRL` | `value` |  |  | `QM13` | `ControlStateVariable` |  |
| `QUAD:LI21:301:CTRL` | `value` |  |  | `Q21301` | `ControlStateVariable` |  |
| `SOLN:IN20:121:CTRL` | `value` |  |  |  | `EnumVariable` |  |
| `SOLN:IN20:121:STATCTRLSUB.T` | `value` |  |  |  | `EnumVariable` |  |
| `XCOR:IN20:641:CTRL` | `value` |  |  | `XC08` | `ControlStateVariable` |  |
| `XCOR:IN20:721:CTRL` | `value` |  |  | `XC09` | `ControlStateVariable` |  |
| `XCOR:IN20:761:CTRL` | `value` |  |  | `XC10` | `ControlStateVariable` |  |
| `XCOR:LI21:101:CTRL` | `value` |  |  | `XC21101` | `ControlStateVariable` |  |
| `XCOR:LI21:135:CTRL` | `value` |  |  | `XC21135` | `ControlStateVariable` |  |
| `XCOR:LI21:165:CTRL` | `value` |  |  | `XC21165` | `ControlStateVariable` |  |
| `XCOR:LI21:175:CTRL` | `value` |  |  | `XC21175` | `ControlStateVariable` |  |
| `XCOR:LI21:191:CTRL` | `value` |  |  | `XC21191` | `ControlStateVariable` |  |
| `XCOR:LI21:275:CTRL` | `value` |  |  | `XC21275` | `ControlStateVariable` |  |
| `XCOR:LI21:302:CTRL` | `value` |  |  | `XC21302` | `ControlStateVariable` |  |
| `YCOR:IN20:642:CTRL` | `value` |  |  | `YC08` | `ControlStateVariable` |  |
| `YCOR:IN20:722:CTRL` | `value` |  |  | `YC09` | `ControlStateVariable` |  |
| `YCOR:IN20:762:CTRL` | `value` |  |  | `YC10` | `ControlStateVariable` |  |
| `YCOR:LI21:102:CTRL` | `value` |  |  | `YC21102` | `ControlStateVariable` |  |
| `YCOR:LI21:136:CTRL` | `value` |  |  | `YC21136` | `ControlStateVariable` |  |
| `YCOR:LI21:166:CTRL` | `value` |  |  | `YC21166` | `ControlStateVariable` |  |
| `YCOR:LI21:174:CTRL` | `value` |  |  | `YC21174` | `ControlStateVariable` |  |
| `YCOR:LI21:192:CTRL` | `value` |  |  | `YC21192` | `ControlStateVariable` |  |
| `YCOR:LI21:276:CTRL` | `value` |  |  | `YC21276` | `ControlStateVariable` |  |
| `YCOR:LI21:303:CTRL` | `value` |  |  | `YC21303` | `ControlStateVariable` |  |
