"""
hardware.py  —  Arduino Communication Layer
============================================
Handles all low-level communication with the Arduino Mega:
  - Finding and connecting to the Arduino
  - Sending voltage setpoints to the MCP4921 DAC
  - Reading ADC values from A0 (RE voltage) and A2 (TIA current voltage)
  - Closing the connection safely

All other experiment scripts (cv.py, lsv.py, ca.py) import from this file.
Nothing hardware-specific should be written anywhere else.

Virtual ground: 2.5V physical = 0V electrochemical
DAC: MCP4921 12-bit (0-4095)
ADC: Arduino Mega 10-bit (0-1023), returned as float 0.0-1.0 over serial
"""

import serial
import serial.tools.list_ports
import time
import sys

# ── Constants ─────────────────────────────────────────────────────────────────

BAUD_RATE    = 115200
VIRTUAL_GND  = 2.5      # volts — physical midpoint of the circuit
DAC_BITS     = 4095     # 12-bit DAC max value
ADC_BITS     = 1023     # 10-bit ADC max value
V_REF        = 5.0      # Arduino ADC reference voltage


# ── Connection ────────────────────────────────────────────────────────────────

def find_arduino():
    """
    Scans all serial ports and returns the port of the first Arduino found.
    Exits with an error message if none is found.

    Returns
    -------
    port : str
        e.g. 'COM3' on Windows or '/dev/ttyUSB0' on Linux
    """
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc   = p.description or ""
        device = p.device or ""
        if any(x in desc for x in ["Arduino", "CH340", "CH341"]) or \
           any(x in device for x in ["ttyUSB", "ttyACM"]):
            print(f"Found Arduino on {p.device}  ({desc})")
            return p.device
    sys.exit("No Arduino found. Check USB connection and try again.")


def connect(port):
    """
    Opens a serial connection to the Arduino and waits for it to reset.

    Parameters
    ----------
    port : str
        COM port returned by find_arduino().

    Returns
    -------
    ser : serial.Serial
        Open serial connection object.
    """
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=2)
        time.sleep(3)                  # wait for Arduino bootloader reset
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        # Wait for READY handshake from Arduino
        print("Waiting for Arduino...")
        deadline = time.time() + 10
        while time.time() < deadline:
            if ser.in_waiting:
                line = ser.readline().decode(errors="ignore").strip()
                if line == "READY":
                    print("Arduino ready.")
                    return ser
            time.sleep(0.05)
        print("Warning: No READY signal received. Proceeding anyway.")
        return ser
    except Exception as e:
        sys.exit(f"Could not open port {port}: {e}")


def close(ser):
    """
    Returns DAC to virtual ground (0V electrochemical) then closes serial port.

    Parameters
    ----------
    ser : serial.Serial
    """
    print("Returning to virtual ground and closing connection...")
    ser.reset_output_buffer()
    ser.reset_input_buffer()
    send_and_read(ser, 0.0)   # send 0V and read response to confirm
    time.sleep(2)
    ser.close()
    print("Connection closed.")


# ── DAC ───────────────────────────────────────────────────────────────────────

def voltage_to_dac(v_electrochem):
    """
    Converts an electrochemical voltage to a 12-bit DAC integer.

    Virtual ground means:
      0V electrochemical = 2.5V physical = DAC midpoint (2047)

    Formula:
      normalized = (V_electrochem + 2.5) / 5.0
      dac_value  = int(normalized * 4095)

    Parameters
    ----------
    v_electrochem : float
        Desired voltage in electrochemical units (e.g. -1.0 to +1.0 V)

    Returns
    -------
    dac_value : int
        Integer in range 0-4095
    """
    normalized = (v_electrochem + VIRTUAL_GND) / V_REF
    normalized = max(0.0, min(1.0, normalized))   # clamp to valid range
    return int(normalized * DAC_BITS)


def send_dac(ser, v_electrochem):
    """
    Sends a voltage setpoint to the Arduino as a float string.
    Arduino parses it, converts to DAC value, and writes to MCP4921.

    Protocol: Python sends "V\n", Arduino writes to DAC.

    Parameters
    ----------
    ser : serial.Serial
    v_electrochem : float
        Electrochemical voltage to set (-2.5V to +2.5V)
    """
    # Convert to physical voltage (what the Arduino/DAC actually sees)
    v_physical = v_electrochem + VIRTUAL_GND
    v_physical = max(0.0, min(V_REF, v_physical))
    ser.write(f"{v_physical:.4f}\n".encode())
    ser.readline()


# ── ADC ───────────────────────────────────────────────────────────────────────

def read_adc(ser):
    """
    Requests an ADC reading from Arduino.
    Arduino reads A0 and A2, sends back one CSV line: "a0,a2\n"

    Returns
    -------
    v_electrochem : float
        Measured cell voltage in electrochemical units (V)
    current : float
        Measured current in amperes (A)
    
    Returns (None, None) if the read fails.
    """
    try:
        line = ser.readline().decode(errors="ignore").strip()
        if not line or "," not in line:
            return None, None
        parts = line.split(",")
        if len(parts) < 4:
            return None, None

        # Arduino sends: timestamp, setpoint, vA0, vA2
        v_a0 = float(parts[2])   # RE voltage (physical, 0-5V)
        v_a2 = float(parts[3])   # TIA voltage (physical, 0-5V)

        return v_a0, v_a2

    except Exception:
        return None, None


def convert_voltage(v_a0_physical):
    """
    Converts raw A0 physical voltage to electrochemical voltage.

    The JUAMI op-amp buffer inverts the RE signal, so:
      V_electrochem = -(V_physical - 2.5)

    Parameters
    ----------
    v_a0_physical : float
        Raw voltage read from A0 (0-5V)

    Returns
    -------
    float : electrochemical voltage in volts
    """
    return -(v_a0_physical - VIRTUAL_GND)


def convert_current(v_a2_physical, r_shunt):
    """
    Converts raw A2 physical voltage to current using Ohm's law.

    The TIA converts cell current to a voltage centered at virtual ground.
      I = (V_tia - 2.5) / R_shunt

    Parameters
    ----------
    v_a2_physical : float
        Raw voltage read from A2 (0-5V)
    r_shunt : float
        Shunt resistor value in ohms

    Returns
    -------
    float : current in amperes
    """
    return (v_a2_physical - VIRTUAL_GND) / r_shunt

def send_and_read(ser, v_electrochem):
    """
    Sends voltage setpoint to Arduino and reads back the response.
    Arduino responds immediately after receiving the setpoint.
    """
    v_physical = v_electrochem + VIRTUAL_GND
    v_physical = max(0.0, min(V_REF, v_physical))
    ser.write(f"{v_physical:.4f}\n".encode())
    
    try:
        line = ser.readline().decode(errors="ignore").strip()
        if not line or "," not in line:
            return None, None
        parts = line.split(",")
        if len(parts) < 4:
            return None, None
        v_a0 = float(parts[2])
        v_a2 = float(parts[3])
        return v_a0, v_a2
    except Exception:
        return None, None