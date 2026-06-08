"""
ca.py  —  Chronoamperometry Experiment
========================================
Steps to a fixed voltage and holds it for a set duration.
Records current vs time.
Imports all hardware communication from hardware.py.
"""

import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read


# ── Main CA experiment ────────────────────────────────────────────────────────

def run_ca(ser, params):
    """
    Steps to target voltage and holds it, recording current over time.

    Parameters
    ----------
    ser    : serial.Serial / RPiBoard
    params : dict — CA parameters from config.yml

    Returns
    -------
    times, set_voltages, voltages, currents : lists  (processed)
    raw_data : list of tuples
        (time, point_idx, sample_idx, set_voltage, voltage_raw, current_raw)
    """
    voltage     = params["voltage"]
    duration    = params["duration"]
    rest_time   = params["rest_time"]
    data_points = params["data_points"]
    r_shunt     = params["r_shunt"]

    time_step = duration / data_points   # seconds between each reading

    # Live plot setup
    plt.ion()
    fig, ax = plt.subplots()
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Current (mA)")
    ax.set_title(f"Chronoamperometry — Live  ({voltage}V)")
    ax.axhline(0, color="gray", linewidth=0.5)
    line, = ax.plot([], [], "g-", linewidth=1.5)
    plt.tight_layout()

    times, set_voltages, voltages, currents = [], [], [], []
    raw_data = []   # (time, point_idx, sample_idx, set_voltage, v_raw, i_raw)

    # Rest at 0V before stepping
    print(f"Resting at 0V for {rest_time}s...")
    send_dac(ser, 0.0)
    time.sleep(rest_time)
    ser.reset_input_buffer()

    # Step to target voltage and start timing
    print(f"Stepping to {voltage}V, holding for {duration}s...")
    exp_start_time = time.time()

    for i in range(data_points):
        target_time = exp_start_time + (i + 1) * time_step

        # Send target voltage and read response (averaged + raw samples)
        v_a0, v_a2, re_samples, tia_samples = send_and_read(ser, voltage)
        if v_a0 is None:
            continue

        elapsed = time.time() - exp_start_time

        # ── Processed data ────────────────────────────────────────────────
        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        times.append(elapsed)
        set_voltages.append(voltage)
        voltages.append(v_meas)
        currents.append(i_meas * 1000.0)

        # ── Raw data — one row per ADC sample ────────────────────────────
        for s_idx, (re_s, tia_s) in enumerate(zip(re_samples, tia_samples)):
            v_raw = convert_voltage(re_s)
            i_raw = convert_current(tia_s, r_shunt) * 1000.0
            raw_data.append((elapsed, i, s_idx, voltage, v_raw, i_raw))

        # Update live plot
        line.set_xdata(times)
        line.set_ydata(currents)
        line.axes.relim()
        line.axes.autoscale_view()
        plt.pause(0.001)

        # Wait until next scheduled reading
        wait = target_time - time.time()
        if wait > 0:
            time.sleep(wait)

    print("CA complete.")
    plt.ioff()
    return times, set_voltages, voltages, currents, raw_data


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents, raw_data, params):
    """
    Saves metadata, a processed CSV, and a raw CSV to a timestamped folder
    inside data/.

    Processed CSV columns : Time (s), Set Voltage (V), Voltage (V), Current (mA)
    Raw CSV columns       : Time (s), Point Index, Sample Index,
                            Set Voltage (V), Voltage_raw (V), Current_raw (mA)
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"CA_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    # ── Metadata ──────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"CA_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment: CA\n")
        f.write(f"Date        : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time        : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Voltage     : {params['voltage']} V\n")
        f.write(f"Duration    : {params['duration']} s\n")
        f.write(f"Rest Time   : {params['rest_time']} s\n")
        f.write(f"Data Points : {params['data_points']}\n")

    # ── Processed CSV ─────────────────────────────────────────────────────
    proc_path = os.path.join(folder, f"CA_{timestamp}_processed.csv")
    with open(proc_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Set Voltage (V)", "Voltage (V)", "Current (mA)"])
        for t, sv, v, i in zip(times, set_voltages, voltages, currents):
            writer.writerow([round(t, 4), round(sv, 6), round(v, 6), round(i, 6)])

    # ── Raw CSV ───────────────────────────────────────────────────────────
    raw_path = os.path.join(folder, f"CA_{timestamp}_raw.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Time (s)", "Point Index", "Sample Index",
            "Set Voltage (V)", "Voltage_raw (V)", "Current_raw (mA)"
        ])
        for (t, pt, sm, sv, vr, ir) in raw_data:
            writer.writerow([round(t, 4), pt, sm, round(sv, 6),
                             round(vr, 6), round(ir, 6)])

    print(f"Data saved to {folder}")
    return folder