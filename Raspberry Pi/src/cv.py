"""
cv.py  —  Cyclic Voltammetry Experiment
========================================
Runs a CV sweep using parameters resolved by main.py from config.yml.

Key design improvements
───────────────────────
1. dt-driven timing
   The master time step dt comes from config. All sweep timing is derived
   from it. steps_per_volt and dE are never user-entered — they are computed:
       dE = sweep_rate_V_s × dt      (voltage step per measurement)

2. Voltage computed from elapsed time — not step index
   E(t) = V_start + (V_end − V_start) × (t_elapsed / t_segment)
   If a step runs late (ADC took longer than expected), the next DAC
   output automatically moves to the correct position on the ramp.
   Step-index-based sweeps accumulate this error silently.

3. Shared sweep engine
   sweep_segment() is imported by lsv.py — LSV is one CV segment.

4. Cycle and segment tracking
   Every row in the processed CSV carries Cycle and Segment columns
   (fwd / rev / ret), making per-cycle analysis trivial in post-processing.

5. Raw ADC CSV contains converted values
   Every individual ADC sample row includes V_echem (V) and I_mA (mA)
   computed from that specific raw reading, not from the averaged value.
   The raw file is fully self-contained for reprocessing.
"""

import time
import matplotlib.pyplot as plt
import csv
import os
from datetime import datetime

from hardware import send_dac, convert_voltage, convert_current, send_and_read


# ── Module constants ──────────────────────────────────────────────────────────

PLOT_MIN_INTERVAL_S = 0.20    # throttle live plot to ≤ 5 Hz


# ── Plot helper ───────────────────────────────────────────────────────────────

def _update_plot(fig, line, x_data, y_data):
    """Fast non-blocking canvas refresh (draw_idle + flush_events)."""
    line.set_xdata(x_data)
    line.set_ydata(y_data)
    line.axes.relim()
    line.axes.autoscale_view()
    fig.canvas.draw_idle()
    fig.canvas.flush_events()


# ── Sweep engine (shared with lsv.py) ────────────────────────────────────────

def sweep_segment(board, v_start, v_end, *,
                  sweep_rate_V_s, dt,
                  r_shunt, n_samples,
                  times, set_voltages, voltages, currents,
                  raw_a0_all, raw_a2_all,
                  cycles, segments,
                  cycle_idx, segment_name,
                  exp_start_time,
                  live_plot, fig, line, plot_state):
    """
    Sweeps from v_start to v_end using absolute-time scheduling.

    Voltage is computed from actual elapsed time rather than step index:

        E(t) = v_start + (v_end − v_start) × (t_actual / t_segment)

    This makes timing self-correcting: a late step causes the subsequent
    DAC output to advance to the correct ramp position automatically,
    rather than silently lagging behind the intended waveform.

    Parameters
    ----------
    board          : RPiBoard
    v_start, v_end : float   segment endpoints (V)
    sweep_rate_V_s : float   sweep rate (V/s)
    dt             : float   master time step (s)  — from config
    r_shunt        : float   shunt resistor (Ω)
    n_samples      : int     ADC readings averaged per point
    times          : list    elapsed time (s)      — appended in-place
    set_voltages   : list    DAC setpoint (V)      — appended in-place
    voltages       : list    measured V_echem (V)  — appended in-place
    currents       : list    measured I (mA)       — appended in-place
    raw_a0_all     : list[list]  individual A0 ADC readings per point
    raw_a2_all     : list[list]  individual A1 ADC readings per point
    cycles         : list    cycle index per point — appended in-place
    segments       : list    segment label per point — appended in-place
    cycle_idx      : int     current cycle number (1-based)
    segment_name   : str     "fwd" | "rev" | "ret" | "sweep"
    exp_start_time : float   time.perf_counter() at experiment start
    live_plot      : bool
    fig, line      : matplotlib objects (None when live_plot=False)
    plot_state     : dict    {'last_update': float}
    """
    voltage_range = abs(v_end - v_start)
    if voltage_range < 1e-6:
        return

    t_segment = voltage_range / sweep_rate_V_s   # expected duration (s)
    seg_start = time.perf_counter()
    step      = 0

    while True:
        # ── Wait until next scheduled slot ───────────────────────────────────
        t_target = seg_start + step * dt
        wait     = t_target - time.perf_counter()
        if wait > 0:
            time.sleep(wait)

        # ── Check segment completion ──────────────────────────────────────────
        t_actual = time.perf_counter() - seg_start
        if t_actual >= t_segment:
            break

        # ── Voltage from elapsed time (self-correcting) ───────────────────────
        frac  = t_actual / t_segment
        v_set = v_start + (v_end - v_start) * frac

        # ── Measure ───────────────────────────────────────────────────────────
        v_a0, v_a2, raw_a0, raw_a2 = send_and_read(board, v_set, n_samples)

        # ── Record ────────────────────────────────────────────────────────────
        t_elapsed = time.perf_counter() - exp_start_time
        times.append(t_elapsed)
        set_voltages.append(v_set)
        voltages.append(convert_voltage(v_a0))
        currents.append(convert_current(v_a2, r_shunt) * 1000.0)
        raw_a0_all.append(raw_a0)
        raw_a2_all.append(raw_a2)
        cycles.append(cycle_idx)
        segments.append(segment_name)

        # ── Live plot (throttled to ≤ 5 Hz) ──────────────────────────────────
        if live_plot:
            now = time.perf_counter()
            if now - plot_state['last_update'] >= PLOT_MIN_INTERVAL_S:
                _update_plot(fig, line, voltages, currents)
                plot_state['last_update'] = time.perf_counter()

        step += 1

    # Force one final plot refresh at segment end
    if live_plot:
        _update_plot(fig, line, voltages, currents)
        plot_state['last_update'] = time.perf_counter()


# ── Main CV experiment ────────────────────────────────────────────────────────

def run_cv(board, params):
    """
    Runs the full CV experiment.

    Parameters
    ----------
    board  : RPiBoard
    params : dict — fully resolved by main.py (includes dt, r_shunt,
                    adc_samples, live_plot injected from hardware section)

    Returns
    -------
    times        : list[float]
    set_voltages : list[float]
    voltages     : list[float]       measured V_echem (V)
    currents     : list[float]       measured I (mA)
    raw_a0_all   : list[list[float]] all individual A0 readings per point (V physical)
    raw_a2_all   : list[list[float]] all individual A1 readings per point (V physical)
    cycles_out   : list[int]         cycle index per data point
    segments_out : list[str]         segment label per data point
    """
    start_voltage  = params['start_voltage']
    vertex_1       = params['vertex_1']
    vertex_2       = params['vertex_2']
    end_voltage    = params['end_voltage']
    sweep_rate     = params['sweep_rate']       # mV/s
    n_cycles       = params['cycles']
    rest_time      = params['rest_time']
    r_shunt        = params['r_shunt']
    n_samples      = params['adc_samples']
    dt             = params['dt']               # seconds
    live_plot      = params.get('live_plot', True)

    sweep_rate_V_s = sweep_rate / 1000.0
    dE             = sweep_rate_V_s * dt        # V per step

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
        ax.set_title("Cyclic Voltammetry — Live")
        ax.axhline(0, color="gray", linewidth=0.5)
        ax.axvline(0, color="gray", linewidth=0.5)
        line, = ax.plot([], [], "b-", linewidth=1.5)
        plt.tight_layout()
        plt.show(block=False)

    times_out, set_voltages_out  = [], []
    voltages_out, currents_out   = [], []
    raw_a0_all,   raw_a2_all     = [], []
    cycles_out,   segments_out   = [], []

    # ── Rest period ───────────────────────────────────────────────────────────
    print(f"\n  Resting at {start_voltage} V for {rest_time} s...")
    send_dac(board, start_voltage)
    time.sleep(rest_time)
    board.reset_input_buffer()

    exp_start_time = time.perf_counter()

    # Common keyword bundle passed to every sweep_segment call
    seg_kw = dict(
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
        cycles         = cycles_out,
        segments       = segments_out,
        exp_start_time = exp_start_time,
        live_plot      = live_plot,
        fig            = fig,
        line           = line,
        plot_state     = plot_state,
    )

    # ── Cycle loop ────────────────────────────────────────────────────────────
    for c in range(1, n_cycles + 1):
        print(f"  Cycle {c}/{n_cycles}")
        sweep_segment(board, start_voltage, vertex_1,   cycle_idx=c, segment_name="fwd", **seg_kw)
        sweep_segment(board, vertex_1,      vertex_2,   cycle_idx=c, segment_name="rev", **seg_kw)
        sweep_segment(board, vertex_2,      end_voltage, cycle_idx=c, segment_name="ret", **seg_kw)

    print(f"CV complete — {len(times_out)} data points collected.")
    if live_plot:
        plt.ioff()

    return (times_out, set_voltages_out, voltages_out, currents_out,
            raw_a0_all, raw_a2_all, cycles_out, segments_out)


# ── Save data ─────────────────────────────────────────────────────────────────

def save_data(times, set_voltages, voltages, currents,
              raw_a0_all, raw_a2_all, cycles, segments, params):
    """
    Creates a timestamped folder inside data/ and saves three files.

    CV_TIMESTAMP_metadata.txt
    ─────────────────────────
    All experiment and hardware parameters, including derived values
    (dt, dE) so the run is fully reproducible from the folder alone.

    CV_TIMESTAMP_data.csv  — processed, averaged values
    ──────────────────────────────────────────────────────
    Columns:
      Time (s) | Cycle | Segment | Set Voltage (V) | Voltage (V) |
      Current (mA) | A0 avg (V) | A1 avg (V)

    CV_TIMESTAMP_raw_adc.csv  — every individual ADC sample
    ─────────────────────────────────────────────────────────
    Columns:
      Point Index | Sample Index | Cycle | Segment |
      A0 raw (V) | A1 raw (V) | V_echem (V) | I_mA (mA)

    V_echem and I_mA are computed from each individual raw reading
    using convert_voltage() and convert_current(), NOT from the
    per-point average. This lets you re-apply any averaging scheme
    in post-processing without losing information.
    """
    timestamp = datetime.now().strftime("%d_%m_%Y__%H_%M_%S")
    BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder    = os.path.join(BASE_DIR, "data", f"CV_{timestamp}")
    os.makedirs(folder, exist_ok=True)

    r_shunt = params['r_shunt']
    dt      = params['dt']
    dE      = params['sweep_rate'] / 1000.0 * dt

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta_path = os.path.join(folder, f"CV_{timestamp}_metadata.txt")
    with open(meta_path, "w") as f:
        f.write("Experiment    : CV\n")
        f.write(f"Date          : {datetime.now().strftime('%d-%m-%Y')}\n")
        f.write(f"Time          : {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"Start Voltage : {params['start_voltage']} V\n")
        f.write(f"Vertex 1      : {params['vertex_1']} V\n")
        f.write(f"Vertex 2      : {params['vertex_2']} V\n")
        f.write(f"End Voltage   : {params['end_voltage']} V\n")
        f.write(f"Sweep Rate    : {params['sweep_rate']} mV/s\n")
        f.write(f"Cycles        : {params['cycles']}\n")
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
    csv_path = os.path.join(folder, f"CV_{timestamp}_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Time (s)", "Cycle", "Segment",
            "Set Voltage (V)", "Voltage (V)", "Current (mA)",
            "A0 avg (V)", "A1 avg (V)"
        ])
        for t, sv, v, i, ra0, ra2, cyc, seg in zip(
                times, set_voltages, voltages, currents,
                raw_a0_all, raw_a2_all, cycles, segments):
            a0_avg = sum(ra0) / len(ra0)
            a1_avg = sum(ra2) / len(ra2)
            writer.writerow([
                round(t,      4),
                cyc,
                seg,
                round(sv,     6),
                round(v,      6),
                round(i,      6),
                round(a0_avg, 6),
                round(a1_avg, 6),
            ])

    # ── Raw ADC CSV — every sample with conversions ───────────────────────────
    raw_path = os.path.join(folder, f"CV_{timestamp}_raw_adc.csv")
    with open(raw_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Point Index", "Sample Index", "Cycle", "Segment",
            "A0 raw (V)", "A1 raw (V)",
            "V_echem (V)", "I_mA (mA)"
        ])
        for pt_idx, (ra0, ra2, cyc, seg) in enumerate(
                zip(raw_a0_all, raw_a2_all, cycles, segments)):
            for s_idx, (a0, a2) in enumerate(zip(ra0, ra2)):
                # Convert each individual raw reading independently
                v_echem = convert_voltage(a0)
                i_ma    = convert_current(a2, r_shunt) * 1000.0
                writer.writerow([
                    pt_idx,
                    s_idx,
                    cyc,
                    seg,
                    round(a0,      6),
                    round(a2,      6),
                    round(v_echem, 6),
                    round(i_ma,    6),
                ])

    print(f"Data saved → {folder}")
    return folder