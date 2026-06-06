"""
main.py  —  Potentiostat Entry Point (Raspberry Pi)
=====================================================
Reads config.yml, resolves and validates all derived parameters,
initializes RPi hardware, runs the selected experiment, saves data,
and closes hardware cleanly.

Usage
-----
  python main.py

Config architecture  (config.yml)
----------------------------------
Users edit ONLY physical experiment parameters. All derived values
are computed here and never stored back to the YAML file.

  hardware:
    r_shunt    : 1000     # Ω
    adc_samples: 10       # ADC readings averaged per point
    live_plot  : true
    dt         : 0.100    # s — MASTER time step

  cv:
    start_voltage: 0.0    # V
    vertex_1     : 0.5    # V
    vertex_2     : -0.5   # V
    end_voltage  : 0.0    # V
    sweep_rate   : 50     # mV/s
    cycles       : 3
    rest_time    : 5.0    # s

  lsv:
    start_voltage: 0.0
    end_voltage  : 1.0
    sweep_rate   : 50
    rest_time    : 5.0

  ca:
    voltage  : 0.5        # V
    duration : 60.0       # s
    rest_time: 5.0

Derived (computed in resolve_params, never in YAML):
  dE          = sweep_rate_V_s × dt        (V/step)
  data_points = round(duration / dt)       (CA only)
  pts/s       = 1 / dt

Validation checks:
  dt       > t_meas  (hardware timing feasibility)
  dE       ≥ DAC_LSB (voltage step ≥ DAC resolution)
  dt       > 0
  r_shunt  > 0

Partial data on KeyboardInterrupt
----------------------------------
If the experiment is interrupted with Ctrl-C, all data collected
up to that point is automatically saved before hardware shutdown.
"""

import sys
import os
import yaml
import matplotlib.pyplot as plt

from hardware import connect, close, DAC_LSB_V, V_REF, DAC_BITS
from cv  import run_cv,  save_data as save_cv
from lsv import run_lsv, save_data as save_lsv
from ca  import run_ca,  save_data as save_ca


# ── Config loading ────────────────────────────────────────────────────────────

def load_config(path=None):
    if path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "config.yml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ── Parameter resolution and validation ──────────────────────────────────────

def resolve_params(config):
    """
    Compute all derived parameters from config and validate hardware limits.

    This is the single config resolution layer — no experiment file
    computes derived values independently.

    Raises
    ------
    ValueError if any parameter violates hardware limits.

    Returns
    -------
    params : dict   fully resolved parameter dict for the selected experiment
    exp    : str    experiment type ("CV", "LSV", or "CA")
    """
    hw        = config['hardware']
    dt        = float(hw['dt'])
    n_samples = int(hw.get('adc_samples', 10))
    r_shunt   = float(hw['r_shunt'])
    live_plot = bool(hw.get('live_plot', True))
    exp       = config['experiment'].strip().upper()

    # ── Basic sanity checks ───────────────────────────────────────────────────
    if dt <= 0:
        raise ValueError(f"dt must be positive, got {dt}")
    if r_shunt <= 0:
        raise ValueError(f"r_shunt must be positive, got {r_shunt}")
    if n_samples < 1 or n_samples > 20:
        raise ValueError(f"adc_samples must be 1–20, got {n_samples}")

    # ── Hardware timing feasibility ───────────────────────────────────────────
    # t_meas = DAC settle (5 ms) + 2 channels × n_samples × 1.7 ms/read
    t_meas_ms = 5.0 + n_samples * 3.4      # ms, 100 kHz I2C
    dt_ms     = dt * 1000.0
    slack_ms  = dt_ms - t_meas_ms

    if slack_ms <= 0:
        raise ValueError(
            f"dt={dt_ms:.1f} ms ≤ t_meas={t_meas_ms:.1f} ms — "
            f"hardware cannot keep up.\n"
            f"  Fix: increase dt to at least {t_meas_ms+5:.0f} ms, "
            f"or reduce adc_samples below "
            f"{int((dt_ms - 5) / 3.4)}."
        )

    print(f"\n  Timing check:")
    print(f"    dt          = {dt_ms:.1f} ms")
    print(f"    t_meas      = {t_meas_ms:.1f} ms  (5 + {n_samples}×3.4)")
    print(f"    slack       = {slack_ms:.1f} ms  ", end="")
    print("✓ safe" if slack_ms > dt_ms * 0.5 else
          "⚠ ok"  if slack_ms > dt_ms * 0.1 else "⚠ tight")

    # ── Experiment-specific resolution ────────────────────────────────────────
    if exp == "CV":
        params = dict(config['cv'])
        sweep_rate_V_s = params['sweep_rate'] / 1000.0
        dE = sweep_rate_V_s * dt

        if dE < DAC_LSB_V:
            raise ValueError(
                f"dE = {dE*1000:.3f} mV < DAC LSB ({DAC_LSB_V*1000:.3f} mV).\n"
                f"  Fix: increase dt above {DAC_LSB_V/sweep_rate_V_s*1000:.0f} ms, "
                f"or increase sweep_rate."
            )

        print(f"    dE          = {dE*1000:.3f} mV/step  (sweep_rate × dt)")
        print(f"    pts/s       = {1.0/dt:.1f}")

        params['dE'] = dE

    elif exp == "LSV":
        params = dict(config['lsv'])
        sweep_rate_V_s = params['sweep_rate'] / 1000.0
        dE = sweep_rate_V_s * dt

        if dE < DAC_LSB_V:
            raise ValueError(
                f"dE = {dE*1000:.3f} mV < DAC LSB ({DAC_LSB_V*1000:.3f} mV).\n"
                f"  Fix: increase dt or sweep_rate."
            )

        print(f"    dE          = {dE*1000:.3f} mV/step")
        print(f"    pts/s       = {1.0/dt:.1f}")

        params['dE'] = dE

    elif exp == "CA":
        params = dict(config['ca'])
        data_points = round(params['duration'] / dt)
        params['data_points'] = data_points
        print(f"    data_points = {data_points}  (duration / dt)")
        print(f"    pts/s       = {1.0/dt:.1f}")

    else:
        raise ValueError(
            f"Unknown experiment: '{exp}'. "
            "Set experiment to CV, LSV, or CA in config.yml."
        )

    # ── Inject shared hardware params ─────────────────────────────────────────
    params['dt']         = dt
    params['adc_samples']= n_samples
    params['r_shunt']    = r_shunt
    params['live_plot']  = live_plot

    return params, exp


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    config        = load_config()
    params, exp   = resolve_params(config)

    print(f"\n{'='*54}")
    print(f"  Multi-Channel Potentiostat  —  Raspberry Pi")
    print(f"  Experiment : {exp}")
    print(f"  R_shunt    : {params['r_shunt']} Ω")
    print(f"  ADC samples: {params['adc_samples']}")
    print(f"  Live plot  : {'ON' if params['live_plot'] else 'OFF'}")
    print(f"{'='*54}\n")

    # ── Print experiment parameters ───────────────────────────────────────────
    if exp == "CV":
        print("CV Parameters:")
        print(f"  Start / V1 / V2 / End : "
              f"{params['start_voltage']} / {params['vertex_1']} / "
              f"{params['vertex_2']} / {params['end_voltage']}  V")
        print(f"  Sweep Rate            : {params['sweep_rate']} mV/s")
        print(f"  Cycles                : {params['cycles']}")
        print(f"  dE                    : {params['dE']*1000:.3f} mV/step")
        print(f"  dt                    : {params['dt']*1000:.1f} ms")
    elif exp == "LSV":
        print("LSV Parameters:")
        print(f"  Start / End : {params['start_voltage']} / {params['end_voltage']}  V")
        print(f"  Sweep Rate  : {params['sweep_rate']} mV/s")
        print(f"  dE          : {params['dE']*1000:.3f} mV/step")
        print(f"  dt          : {params['dt']*1000:.1f} ms")
    elif exp == "CA":
        print("CA Parameters:")
        print(f"  Step Voltage  : {params['voltage']} V")
        print(f"  Duration      : {params['duration']} s")
        print(f"  dt            : {params['dt']*1000:.1f} ms")
        print(f"  Data points   : {params['data_points']}")

    input("\nPress Enter to start...")

    # ── Initialize hardware ───────────────────────────────────────────────────
    board = connect()

    result       = None
    params_saved = None
    interrupted  = False

    try:
        if exp == "CV":
            params_saved = params
            result = run_cv(board, params)
            save_cv(*result, params)

        elif exp == "LSV":
            params_saved = params
            result = run_lsv(board, params)
            save_lsv(*result, params)

        elif exp == "CA":
            params_saved = params
            result = run_ca(board, params)
            save_ca(*result, params)

    except KeyboardInterrupt:
        interrupted = True
        print("\n\nExperiment interrupted by user (Ctrl-C).")
        if result is not None and params_saved is not None:
            print("Saving partial data...")
            try:
                if exp == "CV":
                    save_cv(*result, params_saved)
                elif exp == "LSV":
                    save_lsv(*result, params_saved)
                elif exp == "CA":
                    save_ca(*result, params_saved)
            except Exception as save_err:
                print(f"Partial save failed: {save_err}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nError: {e}")

    finally:
        close(board)
        if not interrupted:
            plt.show()


if __name__ == "__main__":
    main()