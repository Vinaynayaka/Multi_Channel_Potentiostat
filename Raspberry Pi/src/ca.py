"""
ca.py  —  Chronoamperometry Experiment
========================================
Steps to a fixed voltage and holds it for a set duration.
Records current vs time.

Key design improvements
───────────────────────
1. While-loop with absolute-time exit
   The experiment runs until  time.perf_counter() − t_start ≥ duration.
   This eliminates the duration-drift bug (e.g. 60 s experiment ending
   at 45 s or 75 s) that occurs when loop overhead accumulates across
   a counter-based for-loop.

2. dt from config (master time step)
   data_points is derived: data_points = round(duration / dt)
   Never entered manually — prevents duration/data-point mismatches.

3. Raw ADC CSV contains converted values
   Every individual ADC sample row includes V_echem (V) and I_mA (mA)
   computed from that specific raw reading, not from the point average.
"""

import time
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read
from cv       import PLOT_MIN_INTERVAL_S, _update_plot


# ── Main CA experiment ────────────────────────────────────────────────────────

def run_ca(board, params):
    """
    Steps to target voltage and holds it for duration seconds,
    recording current at every dt interval.

    Parameters
    ----------
    board  : RPiBoard
    params : dict — fully resolved by main.py (includes dt, r_shunt,
                    adc_samples, live_plot, data_points)

    Returns
    -------
    times        : list[float]
    set_voltages : list[float]       always params['voltage']
    voltages     : list[float]       measured V_echem (V)
    currents     : list[float]       measured I (mA)
    raw_a0_all   : list[list[float]] individual A0 readings per point (V physical)
    raw_a2_all   : list[list[float]] individual A1 readings per point (V physical)
    """
    voltage   = params['voltage']
    duration  = params['duration']
    rest_time = params['rest_time']
    r_shunt   = params['r_shunt']
    n_samples = params['adc_samples']
    dt        = params['dt']
    live_plot = params.get('live_plot', True)

    # data_points is derived in main.py; fall back to round(duration/dt)
    expected_pts = params.get('data_points', round(duration / dt))

    print(f"  dt            : {dt*1000:.1f} ms")
    print(f"  pts/s         : {1.0/dt:.1f}")
    print(f"  Expected pts  : {expected_pts}")
    print(f"  live plot     : {'ON  (≤5 Hz updates)' if live_plot else 'OFF (best timing)'}")

    # ── Live plot setup ───────────────────────────────────────────────────────
    fig = line = None
    plot_state = {'last_update': 0.0}
    if live_plot:
        plt.ion()
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Current (mA)")
        ax.set_title(f"Chronoamperometry — Live  (step → {voltage} V)")
        ax.axhline(0, color="gray", linewidth=0.5)
        line, = ax.plot([], [], "g-", linewidth=1.5)
        plt.tight_layout()
        plt.show(block=False)

    times_out, set_voltages_out  = [], []
    voltages_out, currents_out   = [], []
    raw_a0_all,   raw_a2_all     = [], []

    # ── Rest period at 0 V ────────────────────────────────────────────────────
    print(f"\n  Resting at 0 V for {rest_time} s...")
    send_dac(board, 0.0)
    time.sleep(rest_time)
    board.reset_input_buffer()

    # ── Step to target voltage ────────────────────────────────────────────────
    print(f"  Stepping to {voltage} V, holding for {duration} s...")
    exp_start = time.perf_counter()
    step      = 0

    # ── While-loop — exits when wall time exceeds duration ────────────────────
    while True:
        # Schedule next slot
        t_target = exp_start + step * dt
        wait     = t_target - time.perf_counter()
        if wait > 0:
            time.sleep(wait)

        # Hard exit when duration is reached
        t_actual = time.perf_counter() - exp_start
        if t_actual >= duration:
            break

        # ── Measure ───────────────────────────────────────────────────────────
        v_a0, v_a2, raw_a0, raw_a2 = send_and_read(board, voltage, n_samples)

        # ── Record ────────────────────────────────────────────────────────────
        t_elapsed = time.perf_counter() - exp_start
        times_out.append(t_elapsed)
        set_voltages_out.append(voltage)
        voltages_out.append(convert_voltage(v_a0))
        currents_out.append(convert_current(v_a2, r_shunt) * 1000.0)
        raw_a0_all.append(raw_a0)
        raw_a2_all.append(raw_a2)

        # ── Live plot (throttled to ≤ 5 Hz) ──────────────────────────────────
        if live_plot:
            now = time.perf_counter()
            if now - plot_state['last_update'] >= PLOT_MIN_INTERVAL_S:
                _update_plot(fig, line, times_out, currents_out)
                plot_state['last_update'] = time.perf_counter()

        step += 1

    # Final plot refresh
    if live_plot:
        _update_plot(fig, line, times_out, currents_out)

    actual_duration = times_out[-1] if times_out else 0
    print(f"CA complete — {len(times_out)} points, "
          f"actual duration {actual_duration:.2f} s.")
    if live_plot:
        plt.ioff()

    return (times_out, set_voltages_out, voltages_out, currents_out,
            raw_a0_all, raw_a2_all)


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents,
              raw_a0_all, raw_a2_all, params):
    """
    Creates a timestamped folder inside data/ and saves three files.

    CA_TIMESTAMP_metadata.txt

    CA_TIMESTAMP_data.csv  — processed, averaged values
    ─────────────────────────────────────────────────────
    Columns:
      Time (s) | Set Voltage (V) | Voltage (V) | Current (mA) |
      A0 avg (V) | A1 avg (V)

    Set Voltage (V) is the intended step voltage (constant).
    Voltage (V) is the measured RE voltage — confirms potentiostat
    is controlling correctly.

    CA_TIMESTAMP_raw_adc.csv  — every individual ADC sample
    ─────────────────────────────────────────────────────────
    Columns:
      Point Index | Sample Index |
      A0 raw (V) | A1 raw (V) | V_echem (V) | I_mA (mA)

    V_echem and I_mA computed from each individual raw reading.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"CA_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    r_shunt = params['r_shunt']
    dt      = params['dt']

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"CA_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment    : CA\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Step Voltage  : {params['voltage']} V\n")
        f.write(f"Duration      : {params['duration']} s\n")
        f.write(f"Rest Time     : {params['rest_time']} s\n")
        f.write(f"dt            : {dt*1000:.1f} ms  (master time step)\n")
        f.write(f"pts/s         : {1.0/dt:.1f}\n")
        f.write(f"ADC Samples   : {params['adc_samples']}\n")
        f.write(f"R_shunt       : {r_shunt} Ω\n")
        f.write(f"Live Plot     : {params.get('live_plot', True)}\n")
        f.write(f"Total Points  : {len(times)}\n")
        actual = times[-1] if times else 0
        f.write(f"Actual Duration: {actual:.3f} s\n")
        f.write(f"Raw ADC rows  : {len(times) * params['adc_samples'] * 2}\n")

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

    # ── Raw ADC CSV — every sample with conversions ───────────────────────────
    raw_path = os.path.join(folder, f"CA_{timestamp}_raw_adc.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Point Index", "Sample Index",
            "A0 raw (V)", "A1 raw (V)",
            "V_echem (V)", "I_mA (mA)"
        ])
        for pt_idx, (ra0, ra2) in enumerate(zip(raw_a0_all, raw_a2_all)):
            for s_idx, (a0, a2) in enumerate(zip(ra0, ra2)):
                v_echem = convert_voltage(a0)
                i_ma    = convert_current(a2, r_shunt) * 1000.0
                writer.writerow([
                    pt_idx,
                    s_idx,
                    round(a0,      6),
                    round(a2,      6),
                    round(v_echem, 6),
                    round(i_ma,    6),
                ])

    print(f"Data saved → {folder}")
    return folder