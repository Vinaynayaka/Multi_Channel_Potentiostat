"""
lsv.py  —  Linear Sweep Voltammetry Experiment
================================================
LSV is a single CV segment. This module re-uses sweep_segment()
from cv.py — no sweep logic is duplicated here.

The same dt-based, time-derived voltage approach applies:
    E(t) = V_start + (V_end − V_start) × (t_elapsed / t_segment)

Raw ADC CSV includes V_echem and I_mA for every individual sample.
"""

import time
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read
from cv       import sweep_segment, PLOT_MIN_INTERVAL_S, _update_plot


# ── Main LSV experiment ───────────────────────────────────────────────────────

def run_lsv(board, params):
    """
    Single linear sweep from start_voltage to end_voltage.

    Parameters
    ----------
    board  : RPiBoard
    params : dict — fully resolved by main.py

    Returns
    -------
    times        : list[float]
    set_voltages : list[float]
    voltages     : list[float]       measured V_echem (V)
    currents     : list[float]       measured I (mA)
    raw_a0_all   : list[list[float]] individual A0 readings per point (V physical)
    raw_a2_all   : list[list[float]] individual A1 readings per point (V physical)
    """
    start_voltage = params['start_voltage']
    end_voltage   = params['end_voltage']
    sweep_rate    = params['sweep_rate']     # mV/s
    rest_time     = params['rest_time']
    r_shunt       = params['r_shunt']
    n_samples     = params['adc_samples']
    dt            = params['dt']
    live_plot     = params.get('live_plot', True)

    sweep_rate_V_s = sweep_rate / 1000.0
    dE             = sweep_rate_V_s * dt

    print(f"  dt        : {dt*1000:.1f} ms")
    print(f"  dE        : {dE*1000:.3f} mV/step")
    print(f"  pts/s     : {1.0/dt:.1f}")
    print(f"  live plot : {'ON  (≤5 Hz updates)' if live_plot else 'OFF (best timing)'}")

    # ── Live plot setup ───────────────────────────────────────────────────────
    fig = line = None
    plot_state = {'last_update': 0.0}
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

    times_out, set_voltages_out  = [], []
    voltages_out, currents_out   = [], []
    raw_a0_all,   raw_a2_all     = [], []
    cycles_buf,   segments_buf   = [], []   # internal; not returned for LSV

    # ── Rest period ───────────────────────────────────────────────────────────
    print(f"\n  Resting at {start_voltage} V for {rest_time} s...")
    send_dac(board, start_voltage)
    time.sleep(rest_time)
    board.reset_input_buffer()

    exp_start_time = time.perf_counter()
    print("  Running LSV sweep...")

    # ── Single sweep — reuses CV sweep engine ─────────────────────────────────
    sweep_segment(
        board, start_voltage, end_voltage,
        sweep_rate_V_s = sweep_rate_V_s,
        dt             = dt,
        r_shunt        = r_shunt,
        n_samples      = n_samples,
        times          = times_out,
        set_voltages   = set_voltages_out,
        voltages       = voltages_out,
        currents       = currents_out,
        raw_a0_all     = raw_a0_all,
        raw_a2_all     = raw_a2_all,
        cycles         = cycles_buf,
        segments       = segments_buf,
        cycle_idx      = 1,
        segment_name   = "sweep",
        exp_start_time = exp_start_time,
        live_plot      = live_plot,
        fig            = fig,
        line           = line,
        plot_state     = plot_state,
    )

    print(f"LSV complete — {len(times_out)} data points collected.")
    if live_plot:
        plt.ioff()

    return (times_out, set_voltages_out, voltages_out, currents_out,
            raw_a0_all, raw_a2_all)


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents,
              raw_a0_all, raw_a2_all, params):
    """
    Creates a timestamped folder inside data/ and saves three files.

    LSV_TIMESTAMP_metadata.txt

    LSV_TIMESTAMP_data.csv  — processed, averaged values
    ──────────────────────────────────────────────────────
    Columns:
      Time (s) | Set Voltage (V) | Voltage (V) | Current (mA) |
      A0 avg (V) | A1 avg (V)

    LSV_TIMESTAMP_raw_adc.csv  — every individual ADC sample
    ──────────────────────────────────────────────────────────
    Columns:
      Point Index | Sample Index |
      A0 raw (V) | A1 raw (V) | V_echem (V) | I_mA (mA)

    V_echem and I_mA are derived from each individual raw reading,
    not from the per-point average. Re-apply any averaging in post.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"LSV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    r_shunt = params['r_shunt']
    dt      = params['dt']
    dE      = params['sweep_rate'] / 1000.0 * dt

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
        f.write(f"dt            : {dt*1000:.1f} ms  (master time step)\n")
        f.write(f"dE            : {dE*1000:.3f} mV/step  (derived)\n")
        f.write(f"pts/s         : {1.0/dt:.1f}\n")
        f.write(f"ADC Samples   : {params['adc_samples']}\n")
        f.write(f"R_shunt       : {r_shunt} Ω\n")
        f.write(f"Live Plot     : {params.get('live_plot', True)}\n")
        f.write(f"Total Points  : {len(times)}\n")
        f.write(f"Raw ADC rows  : {len(times) * params['adc_samples'] * 2}\n")

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

    # ── Raw ADC CSV — every sample with conversions ───────────────────────────
    raw_path = os.path.join(folder, f"LSV_{timestamp}_raw_adc.csv")
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