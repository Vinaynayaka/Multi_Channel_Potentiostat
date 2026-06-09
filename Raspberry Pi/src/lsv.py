"""
lsv.py  —  Linear Sweep Voltammetry Experiment
================================================
Runs a single linear voltage sweep from start to end voltage.
Imports all hardware communication from hardware.py.

steps_per_volt is AUTO-CALCULATED from sample_interval_ms and sweep_rate.
You never set steps_per_volt manually — only change sweep_rate per experiment.

  Formula: steps_per_volt = 1,000,000 / (sweep_rate_mV_s × sample_interval_ms)

  Example:
    100 mV/s + 100ms → 100 steps/volt  (1 point every 100ms)
     50 mV/s + 100ms → 200 steps/volt  (1 point every 100ms)
    200 mV/s + 100ms →  50 steps/volt  (1 point every 100ms)

Saved files:
  LSV_TIMESTAMP_metadata.txt
  LSV_TIMESTAMP_processed.csv  — Time, Set Voltage, Voltage, Current (averaged)
  LSV_TIMESTAMP_raw.csv        — Time, Point Index, Sample Index,
                                 Set Voltage, Voltage_raw, Current_raw
"""

import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read


# ── Main LSV experiment ───────────────────────────────────────────────────────

def run_lsv(ser, params):
    """
    Runs a single linear sweep from start_voltage to end_voltage.

    Parameters
    ----------
    ser    : RPiBoard
    params : dict — from config.yml lsv section + hardware section values

    Required keys in params
    -----------------------
    start_voltage, end_voltage  : float (V)
    sweep_rate                  : float (mV/s)
    rest_time                   : float (s)
    sample_interval_ms          : float — from hardware config, auto-calculates steps
    r_shunt                     : float (ohms)
    adc_samples                 : int   — from hardware config

    Returns
    -------
    times, set_voltages, voltages, currents : lists  (processed, averaged)
    raw_data : list of tuples
        (time, point_idx, sample_idx, set_voltage, voltage_raw, current_raw)
    """
    start_voltage      = params["start_voltage"]
    end_voltage        = params["end_voltage"]
    sweep_rate         = params["sweep_rate"]
    rest_time          = params["rest_time"]
    sample_interval_ms = params["sample_interval_ms"]
    r_shunt            = params["r_shunt"]
    adc_samples        = params["adc_samples"]

    # Auto-calculate steps_per_volt from sweep rate and sample interval
    # steps_per_volt = 1,000,000 / (sweep_rate_mV_s × sample_interval_ms)
    steps_per_volt = 1_000_000 / (sweep_rate * sample_interval_ms)

    voltage_range  = abs(end_voltage - start_voltage)
    n_steps        = max(2, int(voltage_range * steps_per_volt))
    time_for_range = voltage_range / (sweep_rate / 1000.0)
    step_voltages  = np.linspace(start_voltage, end_voltage, n_steps)
    step_times     = np.linspace(0, time_for_range, n_steps)

    print(f"  Sample interval : {sample_interval_ms} ms")
    print(f"  Steps per volt  : {steps_per_volt:.1f}  (auto-calculated)")
    print(f"  Total steps     : {n_steps}")
    print(f"  ADC samples     : {adc_samples} per point")

    # Live plot
    plt.ion()
    fig, ax = plt.subplots()
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("Current (mA)")
    ax.set_title("Linear Sweep Voltammetry — Live")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    line, = ax.plot([], [], "r-", linewidth=1.5)
    plt.tight_layout()

    times, set_voltages, voltages, currents = [], [], [], []
    raw_data = []

    # Rest period — equilibration at start voltage
    print(f"\nResting at {start_voltage}V for {rest_time}s...")
    send_dac(ser, start_voltage)
    time.sleep(rest_time)
    ser.reset_input_buffer()

    exp_start_time = time.time()
    seg_start      = time.time()

    print("Running LSV sweep...")
    for idx, v_set in enumerate(step_voltages):
        v_a0, v_a2, re_samples, tia_samples = send_and_read(
            ser, v_set, adc_samples
        )
        if v_a0 is None:
            continue

        elapsed = time.time() - exp_start_time

        # ── Processed (averaged) ─────────────────────────────────────────
        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        times.append(elapsed)
        set_voltages.append(v_set)
        voltages.append(v_meas)
        currents.append(i_meas * 1000.0)   # A → mA

        # ── Raw — one row per ADC sample ─────────────────────────────────
        for s_idx, (re_s, tia_s) in enumerate(zip(re_samples, tia_samples)):
            v_raw = convert_voltage(re_s)
            i_raw = convert_current(tia_s, r_shunt) * 1000.0
            raw_data.append((elapsed, idx, s_idx, v_set, v_raw, i_raw))

        # Live plot
        line.set_xdata(voltages)
        line.set_ydata(currents)
        line.axes.relim()
        line.axes.autoscale_view()
        plt.pause(0.001)

        # Absolute timing — prevents drift over long experiments
        if idx < n_steps - 1:
            target = seg_start + step_times[idx + 1]
            wait   = target - time.time()
            if wait > 0:
                time.sleep(wait)

    print("LSV complete.")
    plt.ioff()
    return times, set_voltages, voltages, currents, raw_data


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents, raw_data, params):
    """
    Creates a timestamped folder inside data/ and saves 3 files:

    1. LSV_TIMESTAMP_metadata.txt
         Experiment type, date/time, all parameters.

    2. LSV_TIMESTAMP_processed.csv
         Averaged values — one row per data point.
         Columns: Time (s), Set Voltage (V), Voltage (V), Current (mA)

    3. LSV_TIMESTAMP_raw.csv
         Non-averaged — one row per individual ADC sample.
         Columns: Time (s), Point Index, Sample Index,
                  Set Voltage (V), Voltage_raw (V), Current_raw (mA)
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"LSV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    # ── 1. Metadata ───────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"LSV_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment        : LSV (Linear Sweep Voltammetry)\n")
        f.write(f"Date              : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time              : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write("─" * 40 + "\n")
        f.write("Experiment Parameters\n")
        f.write("─" * 40 + "\n")
        f.write(f"Start Voltage     : {params['start_voltage']} V\n")
        f.write(f"End Voltage       : {params['end_voltage']} V\n")
        f.write(f"Sweep Rate        : {params['sweep_rate']} mV/s\n")
        f.write(f"Rest Time         : {params['rest_time']} s\n")
        f.write("─" * 40 + "\n")
        f.write("Hardware Calibration\n")
        f.write("─" * 40 + "\n")
        f.write(f"Sample Interval   : {params['sample_interval_ms']} ms\n")
        f.write(f"R_shunt           : {params['r_shunt']} ohms\n")
        f.write(f"ADC Samples       : {params['adc_samples']} per point\n")
        f.write("─" * 40 + "\n")
        f.write(f"Total Data Points : {len(times)}\n")

    # ── 2. Processed CSV (averaged values) ────────────────────────────────
    proc_path = os.path.join(folder, f"LSV_{timestamp}_processed.csv")
    with open(proc_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Set Voltage (V)", "Voltage (V)", "Current (mA)"])
        for t, sv, v, i in zip(times, set_voltages, voltages, currents):
            writer.writerow([round(t, 4), round(sv, 6), round(v, 6), round(i, 6)])

    # ── 3. Raw CSV (non-averaged, per ADC sample) ─────────────────────────
    raw_path = os.path.join(folder, f"LSV_{timestamp}_raw.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Time (s)", "Point Index", "Sample Index",
            "Set Voltage (V)", "Voltage_raw (V)", "Current_raw (mA)"
        ])
        for (t, pt, sm, sv, vr, ir) in raw_data:
            writer.writerow([round(t, 4), pt, sm,
                             round(sv, 6), round(vr, 6), round(ir, 6)])

    print(f"Data saved to {folder}")
    return folder