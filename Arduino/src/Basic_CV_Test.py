"""
cv.py  —  Cyclic Voltammetry with JUAMI Potentiostat (Arduino Mega)
====================================================================
Virtual ground: 2.5 V physical = 0 V electrochemical

What this script does:
  1. Connects to Arduino over Serial
  2. Sweeps voltage: start → vertex1 → vertex2 → start  (one or more cycles)
  3. At each step: sets DAC, reads RE voltage (A0) and TIA voltage (A2)
  4. Converts raw ADC counts to real voltage and current
  5. Plots live and saves data to CSV at the end

Formulas used:
  normalized       = (V_electrochem + 2.5) / 5.0       [0.0 to 1.0]
  dac_value        = int(normalized * 4095)              [0 to 4095]

  V_physical (A0)  = (adc_count / 1023) * 5.0           [0 to 5V]
  V_electrochem    = -(V_physical - 2.5)                 [inverted by op-amp]

  V_tia (A2)       = (adc_count / 1023) * 5.0
  I (amperes)      = (V_tia - 2.5) / R_shunt            [ohm's law]
"""

import serial
import serial.tools.list_ports
import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import sys
import struct

# ── CV Parameters (edit these) ───────────────────────────────────────────────

START_VOLTAGE   =  0.0      # V electrochemical
VERTEX_1        =  1.0      # V electrochemical  (first turnover)
VERTEX_2        = -1.0      # V electrochemical  (second turnover)
SWEEP_RATE      =  50       # mV/s
NUM_CYCLES      =  2
REST_TIME       =  2.0      # seconds before sweep begins

# Hardware constants
R_SHUNT         =  0.2121   # ohms  (TIA feedback resistor on JUAMI board)
STEP_NUMBER     =  100      # voltage steps per segment
BAUD_RATE       =  115200

# Arduino serial commands (must match .ino)
CMD_SET_DAC     =  0x01
CMD_READ_ADC    =  0x02

# ── Helper functions ──────────────────────────────────────────────────────────

def find_arduino():
    """Scan serial ports and return the first Arduino Mega port found."""
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if "Arduino" in p.description or "CH340" in p.description or "ttyUSB" in p.device or "ttyACM" in p.device:
            print(f"Found Arduino on {p.device}")
            return p.device
    sys.exit("No Arduino found. Check USB connection and try again.")


def connect(port):
    """Open serial connection to Arduino."""
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=2)
        time.sleep(2)   # wait for Arduino to reset after serial connect
        ser.reset_input_buffer()
        print("Connected.")
        return ser
    except Exception as e:
        sys.exit(f"Could not open port {port}: {e}")


def voltage_to_dac(v_electrochem):
    """
    Convert electrochemical voltage to 12-bit DAC integer.
    Virtual ground: 2.5V physical = 0V electrochemical.

    Formula: dac = int(((V + 2.5) / 5.0) * 4095)
    """
    normalized = (v_electrochem + 2.5) / 5.0
    normalized = max(0.0, min(1.0, normalized))     # clamp
    return int(normalized * 4095)


def send_dac(ser, v_electrochem):
    """Send CMD_SET_DAC + 2-byte DAC value to Arduino."""
    dac_val = voltage_to_dac(v_electrochem)
    high_byte = (dac_val >> 8) & 0xFF
    low_byte  =  dac_val & 0xFF
    ser.write(bytes([CMD_SET_DAC, high_byte, low_byte]))


def read_adc(ser):
    """
    Send CMD_READ_ADC, receive 4 bytes back (A0 high, A0 low, A2 high, A2 low).
    Returns (a0_count, a2_count) as integers (0-1023).
    """
    ser.write(bytes([CMD_READ_ADC]))
    raw = ser.read(4)
    if len(raw) < 4:
        print("Warning: incomplete ADC response, skipping.")
        return None, None
    a0 = (raw[0] << 8) | raw[1]
    a2 = (raw[2] << 8) | raw[3]
    return a0, a2


def adc_to_voltage(adc_count):
    """
    Convert raw ADC count (0-1023) to electrochemical voltage.
    Formula: V_electrochem = -((adc/1023 * 5.0) - 2.5)
    Negative sign because the op-amp buffer in JUAMI inverts the RE signal.
    """
    v_physical = (adc_count / 1023.0) * 5.0
    return -(v_physical - 2.5)


def adc_to_current(adc_count):
    """
    Convert raw ADC count (0-1023) to current in amperes.
    TIA converts current to voltage. We read that voltage and divide by R_shunt.
    Formula: I = (V_tia - 2.5) / R_shunt
    """
    v_tia = (adc_count / 1023.0) * 5.0
    return (v_tia - 2.5) / R_SHUNT


# ── Live plot setup ───────────────────────────────────────────────────────────

def setup_plot():
    plt.ion()
    fig, ax = plt.subplots()
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("Current (A)")
    ax.set_title("Cyclic Voltammetry — Live")
    ax.axhline(0, color='gray', linewidth=0.5)
    ax.axvline(0, color='gray', linewidth=0.5)
    line, = ax.plot([], [], 'b-', linewidth=1.5)
    plt.tight_layout()
    return fig, ax, line


def update_plot(line, voltages, currents):
    line.set_xdata(voltages)
    line.set_ydata(currents)
    line.axes.relim()
    line.axes.autoscale_view()
    plt.pause(0.001)


# ── CV sweep ──────────────────────────────────────────────────────────────────

def run_segment(ser, line, v_start, v_end, sweep_rate_mv_s,
                times, voltages, currents, exp_start_time):
    """
    Sweep from v_start to v_end at the given sweep rate.
    Records time, voltage, current at each step.
    """
    voltage_range   = abs(v_end - v_start)
    time_for_range  = voltage_range / (sweep_rate_mv_s / 1000.0)   # seconds
    step_voltages   = np.linspace(v_start, v_end, STEP_NUMBER + 1)
    step_times      = np.linspace(0, time_for_range, STEP_NUMBER + 1)

    seg_start = time.time()

    for idx, v_set in enumerate(step_voltages):
        # Set DAC
        send_dac(ser, v_set)

        # Read ADC
        a0, a2 = read_adc(ser)
        if a0 is None:
            continue

        # Convert
        v_meas = adc_to_voltage(a0)
        i_meas = adc_to_current(a2)

        # Store
        elapsed = time.time() - exp_start_time
        times.append(elapsed)
        voltages.append(v_meas)
        currents.append(i_meas)

        # Update live plot
        update_plot(line, voltages, currents)

        # Wait until scheduled time for next step
        if idx < STEP_NUMBER:
            target_time = seg_start + step_times[idx + 1]
            now = time.time()
            if target_time > now:
                time.sleep(target_time - now)

    return times, voltages, currents


def run_cv(ser):
    """Main CV experiment loop."""
    fig, ax, line = setup_plot()

    times, voltages, currents = [], [], []

    print(f"\nResting for {REST_TIME}s at start voltage ({START_VOLTAGE}V)...")
    send_dac(ser, START_VOLTAGE)
    time.sleep(REST_TIME)

    exp_start_time = time.time()

    for cycle in range(NUM_CYCLES):
        print(f"Cycle {cycle + 1}/{NUM_CYCLES}")

        # Segment 1: start -> vertex 1
        run_segment(ser, line, START_VOLTAGE, VERTEX_1, SWEEP_RATE,
                    times, voltages, currents, exp_start_time)

        # Segment 2: vertex 1 -> vertex 2
        run_segment(ser, line, VERTEX_1, VERTEX_2, SWEEP_RATE,
                    times, voltages, currents, exp_start_time)

        # Segment 3: vertex 2 -> start
        run_segment(ser, line, VERTEX_2, START_VOLTAGE, SWEEP_RATE,
                    times, voltages, currents, exp_start_time)

    print("CV complete.")
    return times, voltages, currents


# ── Save data ─────────────────────────────────────────────────────────────────

def save_csv(times, voltages, currents, filename="cv_data.csv"):
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (s)", "Voltage (V)", "Current (A)"])
        for t, v, i in zip(times, voltages, currents):
            writer.writerow([round(t, 4), round(v, 6), round(i, 9)])
    print(f"Data saved to {filename}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = find_arduino()
    ser  = connect(port)

    try:
        times, voltages, currents = run_cv(ser)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        times, voltages, currents = [], [], []   # partial data lost; handle later

    finally:
        # Return to virtual ground before closing
        send_dac(ser, 0.0)
        time.sleep(1)
        ser.close()
        print("Serial port closed.")

    if times:
        save_csv(times, voltages, currents)
        plt.ioff()
        plt.show()