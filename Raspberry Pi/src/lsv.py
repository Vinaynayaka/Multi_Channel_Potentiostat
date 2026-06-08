"""
lsv.py  —  Linear Sweep Voltammetry Experiment
================================================
Runs a single linear voltage sweep from start to end voltage.
Imports all hardware communication from hardware.py.
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
    ser    : serial.Serial / RPiBoard
    params : dict — LSV parameters from config.yml

    Returns
    -------
    times, set_voltages, voltages, currents : lists  (processed)
    raw_data : list of tuples
        (time, point_idx, sample_idx, set_voltage, voltage_raw, current_raw)
    """
    start_voltage  = params["start_voltage"]
    end_voltage    = params["end_voltage"]
    sweep_rate     = params["sweep_rate"]
    rest_time      = params["rest_time"]
    steps_per_volt = params["steps_per_volt"]
    r_shunt        = params["r_shunt"]

    voltage_range  = abs(end_voltage - start_voltage)
    n_steps        = max(2, int(voltage_range * steps_per_volt))
    time_for_range = voltage_range / (sweep_rate / 1000.0)
    step_voltages  = np.linspace(start_voltage, end_voltage, n_steps)
    step_times     = np.linspace(0, time_for_range, n_steps)

    # Live plot setup
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

    print(f"Resting at {start_voltage}V for {rest_time}s...")
    send_dac(ser, start_voltage)
    time.sleep(rest_time)
    ser.reset_input_buffer()

    exp_start_time = time.time()
    seg_start      = time.time()

    print("Running LSV sweep...")
    for idx, v_set in enumerate(step_voltages):
        v_a0, v_a2, re_samples, tia_samples = send_and_read(ser, v_set)
        if v_a0 is None:
            continue

        elapsed = time.time() - exp_start_time

        # ── Processed ────────────────────────────────────────────────────
        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        times.append(elapsed)
        set_voltages.append(v_set)
        voltages.append(v_meas)
        currents.append(i_meas * 1000.0)

        # ── Raw — one row per ADC sample ─────────────────────────────────
        for s_idx, (re_s, tia_s) in enumerate(zip(re_samples, tia_samples)):
            v_raw = convert_voltage(re_s)
            i_raw = convert_current(tia_s, r_shunt) * 1000.0
            raw_data.append((elapsed, idx, s_idx, v_set, v_raw, i_raw))

        # Update live plot
        line.set_xdata(voltages)
        line.set_ydata(currents)
        line.axes.relim()
        line.axes.autoscale_view()
        plt.pause(0.001)

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
    Creates a timestamped folder inside data/ and saves:
      - LSV_TIMESTAMP_metadata.txt
      - LSV_TIMESTAMP_processed.csv  : Time, Set Voltage, Voltage, Current
      - LSV_TIMESTAMP_raw.csv        : Time, Point Index, Sample Index,
                                       Set Voltage, Voltage_raw, Current_raw
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"LSV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    # ── Metadata ──────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"LSV_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment: LSV\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Start Voltage : {params['start_voltage']} V\n")
        f.write(f"End Voltage   : {params['end_voltage']} V\n")
        f.write(f"Sweep Rate    : {params['sweep_rate']} mV/s\n")
        f.write(f"Rest Time     : {params['rest_time']} s\n")
        f.write(f"Steps per Volt: {params['steps_per_volt']}\n")

    # ── Processed CSV ─────────────────────────────────────────────────────
    proc_path = os.path.join(folder, f"LSV_{timestamp}_processed.csv")
    with open(proc_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Set Voltage (V)", "Voltage (V)", "Current (mA)"])
        for t, sv, v, i in zip(times, set_voltages, voltages, currents):
            writer.writerow([round(t, 4), round(sv, 6), round(v, 6), round(i, 6)])

    # ── Raw CSV ───────────────────────────────────────────────────────────
    raw_path = os.path.join(folder, f"LSV_{timestamp}_raw.csv")
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