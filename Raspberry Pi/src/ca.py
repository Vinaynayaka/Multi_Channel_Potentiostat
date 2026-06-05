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
    ser    : serial.Serial
    params : dict — CA parameters from config.yml

    Returns
    -------
    times, voltages, currents : lists
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

    times, voltages, currents = [], [], []

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

        # Send target voltage and read response
        v_a0, v_a2 = send_and_read(ser, voltage)
        if v_a0 is None:
            continue

        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        elapsed = time.time() - exp_start_time
        times.append(elapsed)
        voltages.append(v_meas)
        currents.append(i_meas*1000.0)

        # CA plots current vs time
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
    return times, voltages, currents


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, voltages, currents, params):
    """
    Saves metadata and CSV data to a timestamped folder inside data/.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR,"data", f"CA_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    meta_path = os.path.join(folder, f"CA_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment: CA\n")
        f.write(f"Date        : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time        : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Voltage     : {params['voltage']} V\n")
        f.write(f"Duration    : {params['duration']} s\n")
        f.write(f"Rest Time   : {params['rest_time']} s\n")
        f.write(f"Data Points : {params['data_points']}\n")

    csv_path = os.path.join(folder, f"CA_{timestamp}_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Voltage (V)", "Current (mA)"])
        for t, v, i in zip(times, voltages, currents):
            writer.writerow([round(t, 4), round(v, 6), round(i, 6)])

    print(f"Data saved to {folder}")
    return folder