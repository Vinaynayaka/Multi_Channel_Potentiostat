"""
ca.py  —  Chronoamperometry Experiment
========================================
Steps to a fixed voltage and holds it for a set duration.
Records current vs time.
Imports all hardware communication from hardware.py.

data_points is AUTO-CALCULATED from duration and sample_interval_ms.
You never set data_points manually.

  Formula: data_points = (duration_s × 1000) / sample_interval_ms

  Example:
    30s duration + 100ms interval → 300 data points
    60s duration + 100ms interval → 600 data points

Saved files:
  CA_TIMESTAMP_metadata.txt
  CA_TIMESTAMP_processed.csv  — Time, Set Voltage, Voltage, Current (averaged)
  CA_TIMESTAMP_raw.csv        — Time, Point Index, Sample Index,
                                Set Voltage, Voltage_raw, Current_raw
"""

import time
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read


# ── Plot throttle constant ────────────────────────────────────────────────────
# All plot operations (set_xdata, relim, autoscale, pause) run at most every
# PLOT_INTERVAL_S seconds — not every data point.
# This prevents Matplotlib layout recalculations from blocking the timing loop.
# Time-based (not count-based) so it stays correct at any sample_interval_ms.

PLOT_INTERVAL_S = 0.5   # refresh live plot twice per second


# ── Main CA experiment ────────────────────────────────────────────────────────

def run_ca(ser, params):
    """
    Steps to target voltage and holds it, recording current over time.

    Parameters
    ----------
    ser    : RPiBoard
    params : dict — from config.yml ca section + hardware section values

    Required keys in params
    -----------------------
    voltage             : float (V)  — step voltage to hold
    duration            : float (s)  — how long to hold
    rest_time           : float (s)  — equilibration at 0V before step
    sample_interval_ms  : float      — from hardware config
    r_shunt             : float (ohms)
    adc_samples         : int        — from hardware config

    Returns
    -------
    times, set_voltages, voltages, currents : lists  (processed, averaged)
    raw_data : list of tuples
        (time, point_idx, sample_idx, set_voltage, voltage_raw, current_raw)
    """
    voltage            = params["voltage"]
    duration           = params["duration"]
    rest_time          = params["rest_time"]
    sample_interval_ms = params["sample_interval_ms"]
    r_shunt            = params["r_shunt"]
    adc_samples        = params["adc_samples"]

    # Auto-calculate data_points from duration and sample interval
    data_points = int((duration * 1000) / sample_interval_ms)
    time_step   = duration / data_points   # seconds between readings

    print(f"  Sample interval : {sample_interval_ms} ms")
    print(f"  Data points     : {data_points}  (auto-calculated)")
    print(f"  ADC samples     : {adc_samples} per point")

    # Live plot setup
    plt.ion()
    fig, ax = plt.subplots()
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Current (mA)")
    ax.set_title(f"Chronoamperometry — Live  ({voltage} V)")
    ax.axhline(0, color="gray", linewidth=0.5)
    line, = ax.plot([], [], "g-", linewidth=1.5)
    plt.tight_layout()

    times, set_voltages, voltages, currents = [], [], [], []
    raw_data       = []
    timing_misses  = 0   # count of points where timing budget was exceeded

    # Rest at 0V — equilibration before step
    print(f"\nResting at 0V for {rest_time}s...")
    send_dac(ser, 0.0)
    time.sleep(rest_time)
    ser.reset_input_buffer()

    print(f"Stepping to {voltage}V, holding for {duration}s...")
    exp_start_time = time.time()
    last_plot_time = exp_start_time

    for i in range(data_points):
        # Absolute target time — self-corrects drift over long experiments
        target_time = exp_start_time + (i + 1) * time_step

        # Hardware read — catch transient errors without crashing
        try:
            v_a0, v_a2, re_samples, tia_samples = send_and_read(
                ser, voltage, adc_samples
            )
        except Exception as hw_err:
            print(f"  ⚠ Hardware error at point {i}: {hw_err} — skipping")
            timing_misses += 1
            continue

        # Cache time.time() once — reused for elapsed and plot check
        now     = time.time()
        elapsed = now - exp_start_time

        # ── Processed (averaged) ─────────────────────────────────────────
        v_meas = convert_voltage(v_a0)
        i_meas = convert_current(v_a2, r_shunt)

        times.append(elapsed)
        set_voltages.append(voltage)
        voltages.append(v_meas)
        currents.append(i_meas * 1000.0)   # A → mA

        # ── Raw — one row per ADC sample ─────────────────────────────────
        for s_idx, (re_s, tia_s) in enumerate(zip(re_samples, tia_samples)):
            v_raw = convert_voltage(re_s)
            i_raw = convert_current(tia_s, r_shunt) * 1000.0
            raw_data.append((elapsed, i, s_idx, voltage, v_raw, i_raw))

        # ── Live plot — time-throttled ────────────────────────────────────
        # ALL plot operations are inside this block — relim() and autoscale_view()
        # recalculate full axis geometry; on RPi this is heavy enough to break
        # the timing loop if called every point.
        # Time-based throttle keeps overhead ~0ms for most points.
        is_last = (i == data_points - 1)
        if (now - last_plot_time >= PLOT_INTERVAL_S) or is_last:
            line.set_xdata(times)
            line.set_ydata(currents)
            ax.relim()
            ax.autoscale_view()
            plt.pause(0.001)
            last_plot_time = now

        # ── Absolute timing wait ──────────────────────────────────────────
        # Recalculate AFTER potential plot overhead so it is always accurate
        wait = target_time - time.time()
        if wait > 0:
            time.sleep(wait)
        else:
            timing_misses += 1

    # Report timing performance
    if timing_misses > 0:
        print(f"  ⚠ {timing_misses} timing miss(es) detected.")
        print(f"    Consider increasing sample_interval_ms in config.yml.")
    print("CA complete.")
    plt.ioff()
    return times, set_voltages, voltages, currents, raw_data


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents, raw_data, params):
    """
    Creates a timestamped folder inside data/ and saves 3 files:

    1. CA_TIMESTAMP_metadata.txt
         Experiment type, date/time, all parameters, computed values.

    2. CA_TIMESTAMP_processed.csv
         Averaged values — one row per data point.
         Columns: Time (s), Set Voltage (V), Voltage (V), Current (mA)

    3. CA_TIMESTAMP_raw.csv
         Non-averaged — one row per individual ADC sample.
         Columns: Time (s), Point Index, Sample Index,
                  Set Voltage (V), Voltage_raw (V), Current_raw (mA)
    """
    # Capture datetime once — folder name and metadata stay consistent
    now            = datetime.now()
    timestamp      = now.strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder         = os.path.join(BASE_DIR, "data", f"CA_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    # Computed values for metadata
    sample_interval_ms  = params["sample_interval_ms"]
    data_points_calc    = int((params["duration"] * 1000) / sample_interval_ms)

    # ── 1. Metadata ───────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"CA_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment           : CA (Chronoamperometry)\n")
        f.write(f"Date                 : {now.strftime('%d-%m-%Y')}\n")
        f.write(f"Time                 : {now.strftime('%H:%M:%S')}\n")
        f.write("─" * 45 + "\n")
        f.write("Experiment Parameters\n")
        f.write("─" * 45 + "\n")
        f.write(f"Voltage              : {params['voltage']} V\n")
        f.write(f"Duration             : {params['duration']} s\n")
        f.write(f"Rest Time            : {params['rest_time']} s\n")
        f.write("─" * 45 + "\n")
        f.write("Hardware Calibration\n")
        f.write("─" * 45 + "\n")
        f.write(f"Sample Interval      : {sample_interval_ms} ms\n")
        f.write(f"R_shunt              : {params['r_shunt']} ohms\n")
        f.write(f"ADC Samples          : {params['adc_samples']} per point\n")
        f.write("─" * 45 + "\n")
        f.write("Data Summary\n")
        f.write("─" * 45 + "\n")
        f.write(f"Data Points (calc)   : {data_points_calc}\n")
        f.write(f"Data Points (actual) : {len(times)}\n")

    # ── 2. Processed CSV (averaged values) ────────────────────────────────
    proc_path = os.path.join(folder, f"CA_{timestamp}_processed.csv")
    with open(proc_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Set Voltage (V)", "Voltage (V)", "Current (mA)"])
        for t, sv, v, i in zip(times, set_voltages, voltages, currents):
            writer.writerow([round(t, 4), round(sv, 6), round(v, 6), round(i, 6)])

    # ── 3. Raw CSV (non-averaged, per ADC sample) ─────────────────────────
    raw_path = os.path.join(folder, f"CA_{timestamp}_raw.csv")
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