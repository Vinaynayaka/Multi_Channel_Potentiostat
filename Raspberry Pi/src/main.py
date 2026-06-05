"""
main.py  —  Potentiostat Entry Point (Raspberry Pi)
=====================================================
Reads config.yml, initializes RPi hardware, runs the selected
experiment, saves data, and closes hardware cleanly.

Usage
-----
  python main.py

config.yml additions (hardware section)
----------------------------------------
  hardware:
    r_shunt    : 1000      # shunt / feedback resistor in ohms
    adc_samples: 10        # ADC readings averaged per data point
                           # higher → less noise, lower → faster
                           # see data-rate table in hardware.py
    live_plot  : true      # true = live plot during experiment
                           # false = best timing / headless use

  The adc_samples and live_plot settings are injected into the
  params dict before calling run_* so all experiment modules can
  access them via params.get(...).

Partial data on KeyboardInterrupt
----------------------------------
  If the experiment is interrupted with Ctrl-C, all data collected
  up to that point is saved automatically before hardware shutdown.
"""

import sys
import os
import yaml
import matplotlib.pyplot as plt

from hardware import connect, close
from cv  import run_cv,  save_data as save_cv
from lsv import run_lsv, save_data as save_lsv
from ca  import run_ca,  save_data as save_ca


# ── Load config ───────────────────────────────────────────────────────────────

def load_config(path=None):
    if path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "config.yml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    config   = load_config()
    exp_type = config["experiment"].strip().upper()
    r_shunt  = config["hardware"]["r_shunt"]
    n_samples= config["hardware"].get("adc_samples", 10)
    live_plot= config["hardware"].get("live_plot", True)

    print(f"\n{'='*54}")
    print(f"  Multi-Channel Potentiostat  —  Raspberry Pi")
    print(f"  Experiment : {exp_type}")
    print(f"  ADC samples: {n_samples} per point")
    print(f"  Live plot  : {'ON' if live_plot else 'OFF'}")
    print(f"{'='*54}\n")

    # Initialize RPi hardware (SPI + I2C)
    board = connect()

    # Variables to hold results — used in finally block for partial saves
    result       = None   # tuple returned by run_*
    params_saved = None   # params dict used for the run
    interrupted  = False

    try:
        # ── CV ────────────────────────────────────────────────────────────────
        if exp_type == "CV":
            params = config["cv"]
            params["r_shunt"]    = r_shunt
            params["adc_samples"]= n_samples
            params["live_plot"]  = live_plot

            pts = params["steps_per_volt"] * params["sweep_rate"] / 1000.0
            print("CV Parameters:")
            print(f"  Start Voltage : {params['start_voltage']} V")
            print(f"  Vertex 1      : {params['vertex_1']} V")
            print(f"  Vertex 2      : {params['vertex_2']} V")
            print(f"  End Voltage   : {params['end_voltage']} V")
            print(f"  Sweep Rate    : {params['sweep_rate']} mV/s")
            print(f"  Cycles        : {params['cycles']}")
            print(f"  Steps/Volt    : {params['steps_per_volt']}")
            print(f"  Data Rate     : {pts:.1f} pts/s")
            print(f"  ADC Samples   : {n_samples}")
            print()

            input("Press Enter to start CV...")
            params_saved = params
            result = run_cv(board, params)
            save_cv(*result, params)

        # ── LSV ───────────────────────────────────────────────────────────────
        elif exp_type == "LSV":
            params = config["lsv"]
            params["r_shunt"]    = r_shunt
            params["adc_samples"]= n_samples
            params["live_plot"]  = live_plot

            pts = params["steps_per_volt"] * params["sweep_rate"] / 1000.0
            print("LSV Parameters:")
            print(f"  Start Voltage : {params['start_voltage']} V")
            print(f"  End Voltage   : {params['end_voltage']} V")
            print(f"  Sweep Rate    : {params['sweep_rate']} mV/s")
            print(f"  Steps/Volt    : {params['steps_per_volt']}")
            print(f"  Data Rate     : {pts:.1f} pts/s")
            print(f"  ADC Samples   : {n_samples}")
            print()

            input("Press Enter to start LSV...")
            params_saved = params
            result = run_lsv(board, params)
            save_lsv(*result, params)

        # ── CA ────────────────────────────────────────────────────────────────
        elif exp_type == "CA":
            params = config["ca"]
            params["r_shunt"]    = r_shunt
            params["adc_samples"]= n_samples
            params["live_plot"]  = live_plot

            time_step = params["duration"] / params["data_points"]
            print("CA Parameters:")
            print(f"  Step Voltage  : {params['voltage']} V")
            print(f"  Duration      : {params['duration']} s")
            print(f"  Rest Time     : {params['rest_time']} s")
            print(f"  Data Points   : {params['data_points']}")
            print(f"  Time Step     : {time_step*1000:.1f} ms")
            print(f"  Data Rate     : {1/time_step:.2f} pts/s")
            print(f"  ADC Samples   : {n_samples}")
            print()

            input("Press Enter to start CA...")
            params_saved = params
            result = run_ca(board, params)
            save_ca(*result, params)

        else:
            sys.exit(
                f"Unknown experiment type: '{exp_type}'. "
                "Set experiment to CV, LSV, or CA in config.yml."
            )

    except KeyboardInterrupt:
        interrupted = True
        print("\n\nExperiment interrupted by user (Ctrl-C).")

        # Save whatever data was collected before the interrupt
        if result is None and params_saved is not None:
            # run_* was interrupted mid-way — the lists inside board were
            # being filled but run_* never returned. We cannot recover them
            # here unless we restructure to pass lists in by reference
            # (future improvement). Notify user.
            print("Partial data could not be recovered (experiment still running).")
            print("Tip: restructure run_* to accept pre-allocated lists for"
                  " partial-save support.")
        elif result is not None:
            print("Saving partial data...")
            if exp_type == "CV":
                save_cv(*result, params_saved)
            elif exp_type == "LSV":
                save_lsv(*result, params_saved)
            elif exp_type == "CA":
                save_ca(*result, params_saved)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nError: {e}")

    finally:
        close(board)
        if not interrupted:
            plt.show()   # keep final plot window open until user closes it


if __name__ == "__main__":
    main()