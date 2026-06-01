"""
main.py  —  Potentiostat Entry Point (Raspberry Pi)
=====================================================
Reads config.yml, initializes RPi hardware, runs the selected
experiment, saves data, and closes hardware cleanly.

Usage:
  python main.py

Differences from Arduino version:
  - No serial port or Arduino needed
  - connect() takes no arguments (SPI + I2C initialized directly)
  - Everything else is identical
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
    exp_type = config["experiment"].upper()
    r_shunt  = config["hardware"]["r_shunt"]

    print(f"\n{'='*50}")
    print(f"  Multi-Channel Potentiostat  —  Raspberry Pi")
    print(f"  Experiment: {exp_type}")
    print(f"{'='*50}\n")

    # Initialize RPi hardware (SPI + I2C)
    board = connect()

    try:
        if exp_type == "CV":
            params = config["cv"]
            params["r_shunt"] = r_shunt
            print("CV Parameters:")
            print(f"  Start Voltage  : {params['start_voltage']} V")
            print(f"  Vertex 1       : {params['vertex_1']} V")
            print(f"  Vertex 2       : {params['vertex_2']} V")
            print(f"  End Voltage    : {params['end_voltage']} V")
            print(f"  Sweep Rate     : {params['sweep_rate']} mV/s")
            print(f"  Cycles         : {params['cycles']}")
            print(f"  Steps per Volt : {params['steps_per_volt']}\n")
            input("Press Enter to start CV...")
            times, voltages, currents = run_cv(board, params)
            save_cv(times, voltages, currents, params)

        elif exp_type == "LSV":
            params = config["lsv"]
            params["r_shunt"] = r_shunt
            print("LSV Parameters:")
            print(f"  Start Voltage  : {params['start_voltage']} V")
            print(f"  End Voltage    : {params['end_voltage']} V")
            print(f"  Sweep Rate     : {params['sweep_rate']} mV/s")
            print(f"  Steps per Volt : {params['steps_per_volt']}\n")
            input("Press Enter to start LSV...")
            times, voltages, currents = run_lsv(board, params)
            save_lsv(times, voltages, currents, params)

        elif exp_type == "CA":
            params = config["ca"]
            params["r_shunt"] = r_shunt
            print("CA Parameters:")
            print(f"  Voltage        : {params['voltage']} V")
            print(f"  Duration       : {params['duration']} s")
            print(f"  Data Points    : {params['data_points']}\n")
            input("Press Enter to start CA...")
            times, voltages, currents = run_ca(board, params)
            save_ca(times, voltages, currents, params)

        else:
            sys.exit(f"Unknown experiment: {exp_type}. Use CV, LSV, or CA in config.yml.")

    except KeyboardInterrupt:
        print("\nExperiment interrupted by user.")
        print("Partial data not saved — add interrupt handling if needed.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nError: {e}")

    finally:
        close(board)
        plt.show()


if __name__ == "__main__":
    main()