"""
main.py  —  Potentiostat Entry Point
======================================
Reads config.yml, connects to Arduino, runs the selected experiment,
saves data, and closes the connection cleanly.

Usage:
  python main.py

To change experiment type or parameters, edit config.yml.
"""
import traceback
import sys
import yaml
import matplotlib.pyplot as plt


from hardware import find_arduino, connect, close

from cv  import run_cv,  save_data as save_cv
from lsv import run_lsv, save_data as save_lsv
from ca  import run_ca,  save_data as save_ca


# ── Load config ───────────────────────────────────────────────────────────────

import os
def load_config(path=None):
    if path is None:
        # Go one level up from src/ to find config.yml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "config.yml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    config      = load_config()
    exp_type    = config["experiment"].upper()
    hw          = config["hardware"]
    r_shunt     = hw["r_shunt"]

    print(f"\n{'='*50}")
    print(f"  Multi-Channel Potentiostat")
    print(f"  Experiment: {exp_type}")
    print(f"{'='*50}\n")

    # Connect to Arduino
    port = find_arduino()
    ser  = connect(port)

    try:
        if exp_type == "CV":
            params = config["cv"]
            params["r_shunt"] = r_shunt
            print("\nCV Parameters:")
            print(f"  Start Voltage  : {params['start_voltage']} V")
            print(f"  Vertex 1       : {params['vertex_1']} V")
            print(f"  Vertex 2       : {params['vertex_2']} V")
            print(f"  End Voltage    : {params['end_voltage']} V")
            print(f"  Sweep Rate     : {params['sweep_rate']} mV/s")
            print(f"  Cycles         : {params['cycles']}")
            print(f"  Steps per Volt : {params['steps_per_volt']}\n")

            input("Press Enter to start CV...")
            times, voltages, currents = run_cv(ser, params)
            save_cv(times, voltages, currents, params)

        elif exp_type == "LSV":
            params = config["lsv"]
            params["r_shunt"] = r_shunt
            print("\nLSV Parameters:")
            print(f"  Start Voltage  : {params['start_voltage']} V")
            print(f"  End Voltage    : {params['end_voltage']} V")
            print(f"  Sweep Rate     : {params['sweep_rate']} mV/s")
            print(f"  Steps per Volt : {params['steps_per_volt']}\n")

            input("Press Enter to start LSV...")
            times, voltages, currents = run_lsv(ser, params)
            save_lsv(times, voltages, currents, params)

        elif exp_type == "CA":
            params = config["ca"]
            params["r_shunt"] = r_shunt
            print("\nCA Parameters:")
            print(f"  Voltage     : {params['voltage']} V")
            print(f"  Duration    : {params['duration']} s")
            print(f"  Data Points : {params['data_points']}\n")

            input("Press Enter to start CA...")
            times, voltages, currents = run_ca(ser, params)
            save_ca(times, voltages, currents, params)

        else:
            sys.exit(f"Unknown experiment type: {exp_type}. Use CV, LSV, or CA in config.yml.")

    except Exception as e:
        traceback.print_exc()
        print(f"\nError: {e}")

    finally:
        close(ser)
        plt.show()


if __name__ == "__main__":
    main()