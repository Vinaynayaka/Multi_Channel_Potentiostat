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
    ser    : serial.Serial
    params : dict — LSV parameters from config.yml

    Returns
    -------
    times, voltages, currents : lists
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

    times, voltages, currents = [], [], []

    print(f"Resting at {start_voltage}V for {rest_time}s...")
    send_dac(ser, start_voltage)
    time.sleep(rest_time)
    ser.reset_input_buffer()

    exp_start_time = time.time()
    seg_start      = time.time()

    print("Running LSV sweep...")
    for idx, v_set in enumerate(step_voltages):
        v_a0, v_a2 = send_and_read(ser, v_set)
        if v_a0 is None:
            continue

        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        elapsed = time.time() - exp_start_time
        times.append(elapsed)
        voltages.append(v_meas)
        currents.append(i_meas*1000.0)

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
    return times, voltages, currents


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, voltages, currents, params):
    """
    Saves metadata and CSV data to a timestamped folder inside data/.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR,"data", f"LSV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

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

    csv_path = os.path.join(folder, f"LSV_{timestamp}_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Voltage (V)", "Current (mA)"])
        for t, v, i in zip(times, voltages, currents):
            writer.writerow([round(t, 4), round(v, 6), round(i, 6)])

    print(f"Data saved to {folder}")
    return folder