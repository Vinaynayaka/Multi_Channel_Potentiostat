"""
lsv.py  —  Linear Sweep Voltammetry Experiment
================================================
Runs a single linear voltage sweep from start to end voltage.
Imports all hardware communication from hardware.py.

Live Plot Notes (Raspberry Pi)
──────────────────────────────
Pros
  • Immediate visual of the i–V curve as it builds.
  • Spot issues (noise spikes, wrong range) before the experiment ends.

Cons on RPi
  • matplotlib canvas updates block the thread for 20–80 ms each.
  • At sweep rates > 20 pts/s, plot rendering can cause timing drift.
  • Fix: updates throttled to ≤ 5 Hz (draw_idle + flush_events).
  • For high sweep rates or headless use, set live_plot: false.

Raw Data
────────
All individual ADC readings are saved to *_raw_adc.csv.
The processed *_data.csv contains averaged, converted values plus
the physical A0/A1 averages for re-processing with different factors.
"""

import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read


# ── Module-level constants ────────────────────────────────────────────────────

PLOT_MIN_INTERVAL_S = 0.20   # throttle live plot to ≤ 5 Hz


# ── Plot helper ───────────────────────────────────────────────────────────────

def _update_plot(fig, line, x_data, y_data):
    """
    Fast non-blocking plot refresh.
    draw_idle() + flush_events() is significantly faster than plt.pause()
    and does not impose a mandatory sleep.
    """
    line.set_xdata(x_data)
    line.set_ydata(y_data)
    line.axes.relim()
    line.axes.autoscale_view()
    fig.canvas.draw_idle()
    fig.canvas.flush_events()


# ── Main LSV experiment ───────────────────────────────────────────────────────

def run_lsv(board, params):
    """
    Runs a single linear sweep from start_voltage to end_voltage.

    Parameters
    ----------
    board  : RPiBoard
    params : dict — LSV parameters from config.yml (r_shunt, adc_samples,
                    and live_plot are injected by main.py from the hardware
                    section of config.yml)

    Returns
    -------
    times        : list[float]       elapsed time per point (s)
    set_voltages : list[float]       DAC setpoint per point (V)
    voltages     : list[float]       measured electrochemical voltage (V)
    currents     : list[float]       measured current (mA)
    raw_a0_all   : list[list[float]] all individual A0 ADC readings per point
    raw_a2_all   : list[list[float]] all individual A1 ADC readings per point
    """
    start_voltage  = params["start_voltage"]
    end_voltage    = params["end_voltage"]
    sweep_rate     = params["sweep_rate"]       # mV/s
    rest_time      = params["rest_time"]
    steps_per_volt = params["steps_per_volt"]
    r_shunt        = params["r_shunt"]
    n_samples      = params.get("adc_samples", 10)
    live_plot      = params.get("live_plot", True)

    voltage_range  = abs(end_voltage - start_voltage)
    n_steps        = max(2, int(voltage_range * steps_per_volt))
    time_for_range = voltage_range / (sweep_rate / 1000.0)
    step_voltages  = np.linspace(start_voltage, end_voltage, n_steps)
    step_times     = np.linspace(0.0, time_for_range, n_steps)

    # Data rate summary
    pts_per_sec = steps_per_volt * sweep_rate / 1000.0
    ms_per_pt   = 1000.0 / pts_per_sec if pts_per_sec > 0 else float("inf")
    print(f"  Data rate  : {pts_per_sec:.1f} pts/s  ({ms_per_pt:.0f} ms/pt)")
    print(f"  ADC samples: {n_samples} per point")
    print(f"  Steps total: {n_steps}  ({voltage_range:.2f} V range)")
    print(f"  Live plot  : {'ON  (≤5 Hz updates)' if live_plot else 'OFF (best timing)'}")

    # ── Live plot setup ───────────────────────────────────────────────────────
    fig = line = None
    last_plot_time = 0.0
    if live_plot:
        plt.ion()
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.set_xlabel("Voltage (V)")
        ax.set_ylabel("Current (mA)")
        ax.set_title("Linear Sweep Voltammetry — Live")
        ax.axhline(0, color="gray", linewidth=0.5)
        ax.axvline(0, color="gray", linewidth=0.5)
        line, = ax.plot([], [], "r-", linewidth=1.5)
        plt.tight_layout()
        plt.show(block=False)

    times, set_voltages, voltages, currents = [], [], [], []
    raw_a0_all, raw_a2_all = [], []

    # ── Rest period ───────────────────────────────────────────────────────────
    print(f"\n  Resting at {start_voltage} V for {rest_time} s...")
    send_dac(board, start_voltage)
    time.sleep(rest_time)
    board.reset_input_buffer()

    exp_start_time = time.time()
    seg_start      = time.time()
    print("  Running LSV sweep...")

    # ── Sweep loop ────────────────────────────────────────────────────────────
    for idx, v_set in enumerate(step_voltages):
        # ── Measure ──────────────────────────────────────────────────────────
        v_a0, v_a2, raw_a0, raw_a2 = send_and_read(board, v_set, n_samples)

        times.append(time.time() - exp_start_time)
        set_voltages.append(float(v_set))
        voltages.append(convert_voltage(v_a0))
        currents.append(convert_current(v_a2, r_shunt) * 1000.0)
        raw_a0_all.append(raw_a0)
        raw_a2_all.append(raw_a2)

        # ── Live plot (throttled to ≤ 5 Hz) ──────────────────────────────────
        if live_plot:
            now = time.time()
            if now - last_plot_time >= PLOT_MIN_INTERVAL_S:
                _update_plot(fig, line, voltages, currents)
                last_plot_time = time.time()

        # ── Hold timing: sleep until next scheduled step ──────────────────────
        if idx < n_steps - 1:
            remaining = (seg_start + step_times[idx + 1]) - time.time()
            if remaining > 0:
                time.sleep(remaining)

    # Final plot refresh to show complete curve
    if live_plot:
        _update_plot(fig, line, voltages, currents)

    print("LSV complete.")
    if live_plot:
        plt.ioff()

    return times, set_voltages, voltages, currents, raw_a0_all, raw_a2_all


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents,
              raw_a0_all, raw_a2_all, params):
    """
    Creates a timestamped folder inside data/ and saves three files:

      LSV_TIMESTAMP_metadata.txt   — experiment parameters
      LSV_TIMESTAMP_data.csv       — averaged & converted values (for analysis)
      LSV_TIMESTAMP_raw_adc.csv    — every individual ADC reading (for archiving)

    Processed CSV columns
    ─────────────────────
      Time (s) | Set Voltage (V) | Voltage (V) | Current (mA) |
      A0 avg (V) | A1 avg (V)

    Raw ADC CSV columns
    ───────────────────
      Point Index | Sample Index | A0 raw (V) | A1 raw (V)
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"LSV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"LSV_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment    : LSV\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Start Voltage : {params['start_voltage']} V\n")
        f.write(f"End Voltage   : {params['end_voltage']} V\n")
        f.write(f"Sweep Rate    : {params['sweep_rate']} mV/s\n")
        f.write(f"Rest Time     : {params['rest_time']} s\n")
        f.write(f"Steps/Volt    : {params['steps_per_volt']}\n")
        f.write(f"ADC Samples   : {params.get('adc_samples', 10)}\n")
        f.write(f"Live Plot     : {params.get('live_plot', True)}\n")
        pts = params['steps_per_volt'] * params['sweep_rate'] / 1000.0
        f.write(f"Data Rate     : {pts:.1f} pts/s\n")
        f.write(f"Total Points  : {len(times)}\n")

    # ── Processed data CSV ────────────────────────────────────────────────────
    csv_path = os.path.join(folder, f"LSV_{timestamp}_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Time (s)", "Set Voltage (V)", "Voltage (V)", "Current (mA)",
            "A0 avg (V)", "A1 avg (V)"
        ])
        for t, sv, v, i, ra0, ra2 in zip(
                times, set_voltages, voltages, currents, raw_a0_all, raw_a2_all):
            a0_avg = sum(ra0) / len(ra0)
            a1_avg = sum(ra2) / len(ra2)
            writer.writerow([
                round(t,      4),
                round(sv,     6),
                round(v,      6),
                round(i,      6),
                round(a0_avg, 6),
                round(a1_avg, 6),
            ])

    # ── Raw ADC CSV ───────────────────────────────────────────────────────────
    raw_path = os.path.join(folder, f"LSV_{timestamp}_raw_adc.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Point Index", "Sample Index", "A0 raw (V)", "A1 raw (V)"])
        for pt_idx, (ra0, ra2) in enumerate(zip(raw_a0_all, raw_a2_all)):
            for s_idx, (a0, a2) in enumerate(zip(ra0, ra2)):
                writer.writerow([pt_idx, s_idx, round(a0, 6), round(a2, 6)])

    print(f"Data saved → {folder}")
    return folder