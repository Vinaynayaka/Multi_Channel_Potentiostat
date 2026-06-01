"""
cv.py  —  Cyclic Voltammetry Experiment
========================================
Runs a CV sweep using parameters from config.yml.
Imports all hardware communication from hardware.py.

Sweep pattern:
  start → vertex_1 → vertex_2 → end_voltage  (repeated for N cycles)

Step count per segment is proportional to voltage range so data point
density (steps per volt) is uniform across all segments.

  Example: steps_per_volt = 100
    0V → 1V  (range 1V) → 100 steps
    1V → -1V (range 2V) → 200 steps
   -1V → 0V  (range 1V) → 100 steps
"""

import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import (
    find_arduino, connect, close,
    send_dac, read_adc,
    convert_voltage, convert_current,
    send_and_read
)


# ── Run one sweep segment ─────────────────────────────────────────────────────

def run_segment(ser, v_start, v_end, sweep_rate_mv_s, steps_per_volt,
                r_shunt, times, voltages, currents, exp_start_time, line):
    """
    Sweeps from v_start to v_end at the given sweep rate.
    Step count is proportional to voltage range (uniform density).
    """
    voltage_range  = abs(v_end - v_start)
    n_steps        = max(2, int(voltage_range * steps_per_volt))
    time_for_range = voltage_range / (sweep_rate_mv_s / 1000.0)
    step_voltages  = np.linspace(v_start, v_end, n_steps)
    step_times     = np.linspace(0, time_for_range, n_steps)

    seg_start = time.time()

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

    return times, voltages, currents


# ── Main CV experiment ────────────────────────────────────────────────────────

def run_cv(ser, params):
    """
    Runs the full CV experiment.

    Parameters
    ----------
    ser    : serial.Serial
    params : dict — CV parameters from config.yml

    Returns
    -------
    times, voltages, currents : lists
    """
    start_voltage  = params["start_voltage"]
    vertex_1       = params["vertex_1"]
    vertex_2       = params["vertex_2"]
    end_voltage    = params["end_voltage"]
    sweep_rate     = params["sweep_rate"]
    cycles         = params["cycles"]
    rest_time      = params["rest_time"]
    steps_per_volt = params["steps_per_volt"]
    r_shunt        = params["r_shunt"]

    plt.ion()
    fig, ax = plt.subplots()
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("Current (mA)")
    ax.set_title("Cyclic Voltammetry — Live")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    line, = ax.plot([], [], "b-", linewidth=1.5)
    plt.tight_layout()

    times, voltages, currents = [], [], []

    print(f"Resting at {start_voltage}V for {rest_time}s...")
    send_dac(ser, start_voltage)
    time.sleep(rest_time)
    ser.reset_input_buffer() 

    exp_start_time = time.time()

    for cycle in range(cycles):
        print(f"  Cycle {cycle + 1}/{cycles}")

        # Segment 1: start → vertex_1
        run_segment(ser, start_voltage, vertex_1, sweep_rate, steps_per_volt,
                    r_shunt, times, voltages, currents, exp_start_time, line)

        # Segment 2: vertex_1 → vertex_2
        run_segment(ser, vertex_1, vertex_2, sweep_rate, steps_per_volt,
                    r_shunt, times, voltages, currents, exp_start_time, line)

        # Segment 3: vertex_2 → end_voltage
        run_segment(ser, vertex_2, end_voltage, sweep_rate, steps_per_volt,
                    r_shunt, times, voltages, currents, exp_start_time, line)

    print("CV complete.")
    plt.ioff()
    return times, voltages, currents


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, voltages, currents, params):
    """
    Creates a timestamped folder inside data/ and saves:
      - CV_TIMESTAMP_metadata.txt
      - CV_TIMESTAMP_data.csv
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    folder    = os.path.join("data", f"CV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    meta_path = os.path.join(folder, f"CV_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment: CV\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Start Voltage : {params['start_voltage']} V\n")
        f.write(f"Vertex 1      : {params['vertex_1']} V\n")
        f.write(f"Vertex 2      : {params['vertex_2']} V\n")
        f.write(f"End Voltage   : {params['end_voltage']} V\n")
        f.write(f"Sweep Rate    : {params['sweep_rate']} mV/s\n")
        f.write(f"Cycles        : {params['cycles']}\n")
        f.write(f"Rest Time     : {params['rest_time']} s\n")
        f.write(f"Steps per Volt: {params['steps_per_volt']}\n")

    csv_path = os.path.join(folder, f"CV_{timestamp}_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Voltage (V)", "Current (mA)"])
        for t, v, i in zip(times, voltages, currents):
            writer.writerow([round(t, 4), round(v, 6), round(i * 1000, 6)])

    print(f"Data saved to {folder}")
    return folder