"""
ca.py  —  Chronoamperometry Experiment
========================================
Steps to a fixed voltage and holds it for a set duration.
Records current vs time.
Imports all hardware communication from hardware.py.

Live Plot Notes (Raspberry Pi)
──────────────────────────────
Pros
  • Watch transient current decay in real time — immediately see if
    the step occurred and if the decay shape looks correct.
  • Useful for long experiments where you want progress confirmation.

Cons on RPi
  • matplotlib canvas updates block the thread 20–80 ms each.
  • CA typically has generous time_step (100–1000 ms/pt), so plot
    overhead is much less of a problem than in fast CV/LSV.
  • Still throttled to ≤ 5 Hz as a safety margin.
  • For high time-resolution CA (many pts/s), set live_plot: false.

Raw Data
────────
All individual ADC readings are saved to *_raw_adc.csv.
The processed *_data.csv contains averaged, converted values plus
the physical A0/A1 averages for full traceability and re-processing.
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


# ── Main CA experiment ────────────────────────────────────────────────────────

def run_ca(board, params):
    """
    Steps to target voltage and holds it, recording current over time.

    Timing model
    ────────────
    Measurements are scheduled at absolute times:
      T_i = exp_start + i × time_step
    This prevents cumulative drift regardless of individual measurement
    duration. If a measurement overruns its slot, the next one is taken
    immediately (no additional delay) to catch up.

    Parameters
    ----------
    board  : RPiBoard
    params : dict — CA parameters from config.yml (r_shunt, adc_samples,
                    and live_plot are injected by main.py from the hardware
                    section of config.yml)

    Returns
    -------
    times        : list[float]       elapsed time per point (s)
    set_voltages : list[float]       DAC setpoint per point (V) — constant
    voltages     : list[float]       measured electrochemical voltage (V)
    currents     : list[float]       measured current (mA)
    raw_a0_all   : list[list[float]] all individual A0 ADC readings per point
    raw_a2_all   : list[list[float]] all individual A1 ADC readings per point
    """
    voltage     = params["voltage"]
    duration    = params["duration"]
    rest_time   = params["rest_time"]
    data_points = params["data_points"]
    r_shunt     = params["r_shunt"]
    n_samples   = params.get("adc_samples", 10)
    live_plot   = params.get("live_plot", True)

    time_step   = duration / data_points   # seconds between measurements

    pts_per_sec = 1.0 / time_step if time_step > 0 else float("inf")
    print(f"  Data rate  : {pts_per_sec:.2f} pts/s  ({time_step*1000:.0f} ms/pt)")
    print(f"  ADC samples: {n_samples} per point")
    print(f"  Live plot  : {'ON  (≤5 Hz updates)' if live_plot else 'OFF (best timing)'}")

    # ── Live plot setup ───────────────────────────────────────────────────────
    fig = line = None
    last_plot_time = 0.0
    if live_plot:
        plt.ion()
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Current (mA)")
        ax.set_title(f"Chronoamperometry — Live  (step to {voltage} V)")
        ax.axhline(0, color="gray", linewidth=0.5)
        line, = ax.plot([], [], "g-", linewidth=1.5)
        plt.tight_layout()
        plt.show(block=False)

    times, set_voltages, voltages, currents = [], [], [], []
    raw_a0_all, raw_a2_all = [], []

    # ── Rest period (at 0 V) ──────────────────────────────────────────────────
    print(f"\n  Resting at 0 V for {rest_time} s...")
    send_dac(board, 0.0)
    time.sleep(rest_time)
    board.reset_input_buffer()

    # ── Step to target voltage ─────────────────────────────────────────────────
    print(f"  Stepping to {voltage} V, holding for {duration} s...")
    exp_start_time = time.time()

    # ── Measurement loop ──────────────────────────────────────────────────────
    for i in range(data_points):
        # Absolute target time for this measurement
        target_time = exp_start_time + i * time_step

        # ── Measure ──────────────────────────────────────────────────────────
        v_a0, v_a2, raw_a0, raw_a2 = send_and_read(board, voltage, n_samples)

        elapsed = time.time() - exp_start_time
        times.append(elapsed)
        set_voltages.append(voltage)
        voltages.append(convert_voltage(v_a0))
        currents.append(convert_current(v_a2, r_shunt) * 1000.0)
        raw_a0_all.append(raw_a0)
        raw_a2_all.append(raw_a2)

        # ── Live plot (throttled to ≤ 5 Hz) ──────────────────────────────────
        if live_plot:
            now = time.time()
            if now - last_plot_time >= PLOT_MIN_INTERVAL_S:
                _update_plot(fig, line, times, currents)
                last_plot_time = time.time()

        # ── Hold timing: sleep until next scheduled measurement ───────────────
        next_target = exp_start_time + (i + 1) * time_step
        remaining   = next_target - time.time()
        if remaining > 0:
            time.sleep(remaining)

    # Final plot refresh to show complete transient
    if live_plot:
        _update_plot(fig, line, times, currents)

    print("CA complete.")
    if live_plot:
        plt.ioff()

    return times, set_voltages, voltages, currents, raw_a0_all, raw_a2_all


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents,
              raw_a0_all, raw_a2_all, params):
    """
    Creates a timestamped folder inside data/ and saves three files:

      CA_TIMESTAMP_metadata.txt   — experiment parameters
      CA_TIMESTAMP_data.csv       — averaged & converted values (for analysis)
      CA_TIMESTAMP_raw_adc.csv    — every individual ADC reading (for archiving)

    Processed CSV columns
    ─────────────────────
      Time (s) | Set Voltage (V) | Voltage (V) | Current (mA) |
      A0 avg (V) | A1 avg (V)

    Raw ADC CSV columns
    ───────────────────
      Point Index | Sample Index | A0 raw (V) | A1 raw (V)

    Note: For CA, Set Voltage (V) is constant (the step voltage).
    Voltage (V) is the measured RE voltage — it should equal Set Voltage
    if the potentiostat is controlling correctly.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"CA_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    time_step = params["duration"] / params["data_points"]

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"CA_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment    : CA\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Step Voltage  : {params['voltage']} V\n")
        f.write(f"Duration      : {params['duration']} s\n")
        f.write(f"Rest Time     : {params['rest_time']} s\n")
        f.write(f"Data Points   : {params['data_points']}\n")
        f.write(f"ADC Samples   : {params.get('adc_samples', 10)}\n")
        f.write(f"Live Plot     : {params.get('live_plot', True)}\n")
        f.write(f"Time Step     : {time_step*1000:.1f} ms\n")
        f.write(f"Data Rate     : {1.0/time_step:.2f} pts/s\n")
        f.write(f"Total Points  : {len(times)}\n")

    # ── Processed data CSV ────────────────────────────────────────────────────
    csv_path = os.path.join(folder, f"CA_{timestamp}_data.csv")
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
    raw_path = os.path.join(folder, f"CA_{timestamp}_raw_adc.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Point Index", "Sample Index", "A0 raw (V)", "A1 raw (V)"])
        for pt_idx, (ra0, ra2) in enumerate(zip(raw_a0_all, raw_a2_all)):
            for s_idx, (a0, a2) in enumerate(zip(ra0, ra2)):
                writer.writerow([pt_idx, s_idx, round(a0, 6), round(a2, 6)])

    print(f"Data saved → {folder}")
    return folder