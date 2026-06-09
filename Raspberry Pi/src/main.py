"""
main.py  —  Potentiostat Entry Point (Raspberry Pi)
=====================================================
Reads config.yml, initializes RPi hardware, runs the selected
experiment, saves data, and closes hardware cleanly.

Usage:
  python main.py

Hardware calibration parameters (sample_interval_ms, r_shunt, adc_samples)
are read once from config.yml and injected into experiment params automatically.
You never need to touch these per experiment — only change experiment-specific
parameters (voltages, sweep rate, cycles, duration).

Config parameters per experiment:
  CV  : start_voltage, vertex_1, vertex_2, end_voltage, sweep_rate, cycles, rest_time
  LSV : start_voltage, end_voltage, sweep_rate, rest_time
  CA  : voltage, duration, rest_time
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

    # Hardware calibration — read once, injected into all experiment params
    hw                 = config["hardware"]
    sample_interval_ms = hw["sample_interval_ms"]
    r_shunt            = hw["r_shunt"]
    adc_samples        = hw["adc_samples"]

    print(f"\n{'='*50}")
    print(f"  Multi-Channel Potentiostat  —  Raspberry Pi")
    print(f"  Experiment : {exp_type}")
    print(f"{'='*50}")
    print(f"  Hardware Calibration:")
    print(f"    Sample Interval : {sample_interval_ms} ms")
    print(f"    R_shunt         : {r_shunt} ohms")
    print(f"    ADC Samples     : {adc_samples} per point")
    print(f"{'='*50}\n")

    # Initialize RPi hardware (SPI + I2C)
    board = connect()

    try:
        if exp_type == "CV":
            params = config["cv"]
            # Inject hardware calibration into params
            params["sample_interval_ms"] = sample_interval_ms
            params["r_shunt"]            = r_shunt
            params["adc_samples"]        = adc_samples

            print("CV Parameters:")
            print(f"  Start Voltage : {params['start_voltage']} V")
            print(f"  Vertex 1      : {params['vertex_1']} V")
            print(f"  Vertex 2      : {params['vertex_2']} V")
            print(f"  End Voltage   : {params['end_voltage']} V")
            print(f"  Sweep Rate    : {params['sweep_rate']} mV/s")
            print(f"  Cycles        : {params['cycles']}")
            print(f"  Rest Time     : {params['rest_time']} s\n")
            input("Press Enter to start CV...")
            times, set_voltages, voltages, currents, raw_data = run_cv(board, params)
            save_cv(times, set_voltages, voltages, currents, raw_data, params)

        elif exp_type == "LSV":
            params = config["lsv"]
            # Inject hardware calibration into params
            params["sample_interval_ms"] = sample_interval_ms
            params["r_shunt"]            = r_shunt
            params["adc_samples"]        = adc_samples

            print("LSV Parameters:")
            print(f"  Start Voltage : {params['start_voltage']} V")
            print(f"  End Voltage   : {params['end_voltage']} V")
            print(f"  Sweep Rate    : {params['sweep_rate']} mV/s")
            print(f"  Rest Time     : {params['rest_time']} s\n")
            input("Press Enter to start LSV...")
            times, set_voltages, voltages, currents, raw_data = run_lsv(board, params)
            save_lsv(times, set_voltages, voltages, currents, raw_data, params)

        elif exp_type == "CA":
            params = config["ca"]
            # Inject hardware calibration into params
            params["sample_interval_ms"] = sample_interval_ms
            params["r_shunt"]            = r_shunt
            params["adc_samples"]        = adc_samples

            print("CA Parameters:")
            print(f"  Voltage       : {params['voltage']} V")
            print(f"  Duration      : {params['duration']} s")
            print(f"  Rest Time     : {params['rest_time']} s\n")
            input("Press Enter to start CA...")
            times, set_voltages, voltages, currents, raw_data = run_ca(board, params)
            save_ca(times, set_voltages, voltages, currents, raw_data, params)

        else:
            sys.exit(
                f"Unknown experiment: {exp_type}. "
                "Set experiment to CV, LSV, or CA in config.yml."
            )

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