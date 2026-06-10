"""
hardware.py  —  Raspberry Pi Hardware Layer
============================================
Replaces Arduino serial communication with direct RPi hardware:
  - MCP4921 DAC via SPI (sets voltage)
  - ADS1115 16-bit ADC via I2C (reads voltage and current)

Virtual ground : 2.5V physical = 0V electrochemical
DAC            : MCP4921 12-bit SPI
ADC            : ADS1115 16-bit I2C (±6.144V range to cover full 0–5V)

Wiring:
  MCP4921
    VDD  -> 5V        VSS  -> GND
    CS   -> GPIO 8    SCK  -> GPIO 11
    SDI  -> GPIO 10   LDAC -> GND
    VREF -> 5V

  ADS1115 (5V powered, I2C level-shifted to 3.3V)
    VDD  -> 5V        GND  -> GND
    SCL  -> GPIO 3    SDA  -> GPIO 2
    ADDR -> GND  (address 0x48)
    A0   -> RE buffer output
    A1   -> TIA output (current sense)

Level shifters:
  SPI (3.3V→5V)  : 74AHCT125 unidirectional  (MOSI, SCLK, CE0)
  I2C (3.3V↔5V)  : BSS138-based bidirectional (SDA, SCL)
"""

import time
import sys

try:
    import spidev
    import RPi.GPIO as GPIO
    import board as rpi_board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    from adafruit_ads1x15.ads1x15 import Mode
except ImportError as e:
    sys.exit(
        f"Missing library: {e}\n"
        "Install with:\n"
        "  pip install spidev RPi.GPIO adafruit-circuitpython-ads1x15"
    )


# ── Constants ─────────────────────────────────────────────────────────────────

VIRTUAL_GND    = 2.57      # volts — physical midpoint of the circuit
V_REF          = 5.157      # volts — supply voltage
DAC_BITS       = 4095       # 12-bit DAC max value
DAC_CS_PIN     = 8          # GPIO 8 = CE0 — chip select for MCP4921
ADC_GAIN       = 2/3        # ADS1115 PGA ±6.144V — covers full 0–5V
DAC_SETTLE_S   = 0.001      # 1ms DAC settle — MCP4921 settles in microseconds,
                             # 1ms gives comfortable margin without wasting budget


# ── RPiBoard class ────────────────────────────────────────────────────────────

class RPiBoard:
    """
    Holds all hardware handles for the RPi potentiostat.
    Passed as the first argument to all hardware functions.
    """

    def __init__(self):
        # GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DAC_CS_PIN, GPIO.OUT, initial=GPIO.HIGH)

        # SPI for MCP4921 DAC
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)               # SPI bus 0, device 0 (CE0)
        self.spi.max_speed_hz = 1_000_000 # 1 MHz — safe for MCP4921
        self.spi.mode = 0b00              # CPOL=0, CPHA=0

        # I2C + ADS1115 — continuous mode for fast batch reading
        i2c           = busio.I2C(rpi_board.SCL, rpi_board.SDA)
        ads           = ADS.ADS1115(i2c, address=0x48)
        ads.gain      = ADC_GAIN          # ±6.144V range
        ads.data_rate = 860               # 860 SPS — maximum rate
        ads.mode      = Mode.CONTINUOUS   # continuous mode:
                                          #   same channel → register read only (~0.4ms)
                                          #   channel switch → config write + 2 conv wait (~3ms)
                                          # eliminates single-shot polling (~11ms per read)

        self.chan_re  = AnalogIn(ads, 0)  # A0 — RE buffer voltage
        self.chan_tia = AnalogIn(ads, 1)  # A1 — TIA output voltage

    def reset_input_buffer(self):
        """No-op — exists for compatibility."""
        pass

    def reset_output_buffer(self):
        """No-op — exists for compatibility."""
        pass

    def close(self):
        self.spi.close()
        GPIO.cleanup()


# ── Connection ────────────────────────────────────────────────────────────────

def connect():
    """
    Initializes RPi SPI and I2C hardware.
    Returns an RPiBoard object.
    """
    print("Initializing RPi potentiostat hardware...")
    try:
        board = RPiBoard()
        print("SPI (MCP4921 DAC)  : initialized on CE0.")
        print("I2C (ADS1115 ADC)  : initialized at 0x48, continuous mode, 860 SPS.")
        print("Hardware ready.\n")
        return board
    except Exception as e:
        sys.exit(f"Hardware initialization failed: {e}")


def close(board):
    """
    Returns DAC to virtual ground, then cleans up SPI and GPIO.
    """
    print("Returning to virtual ground and closing hardware...")
    send_and_read(board, 0.0)
    time.sleep(2)
    board.close()
    print("Hardware closed.")


# ── DAC ───────────────────────────────────────────────────────────────────────

def voltage_to_dac(v_electrochem):
    """
    Converts electrochemical voltage to 12-bit DAC integer.
    Formula: dac = int(((V + VIRTUAL_GND) / V_REF) * 4095)

    Parameters
    ----------
    v_electrochem : float  (-2.5V to +2.5V)

    Returns
    -------
    int : 0 to 4095
    """
    normalized = (v_electrochem + VIRTUAL_GND) / V_REF
    normalized = max(0.0, min(1.0, normalized))
    return int(normalized * DAC_BITS)


def send_dac(board, v_electrochem):
    """
    Sends voltage setpoint to MCP4921 via SPI.

    MCP4921 16-bit command word:
      Bit 15   : 0 = Channel A
      Bit 14   : 0 = Unbuffered VREF
      Bit 13   : 1 = 1x gain
      Bit 12   : 1 = DAC active (SHDN = 0)
      Bits 11-0: 12-bit value

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float
    """
    dac_value = voltage_to_dac(v_electrochem)
    command   = 0x3000 | (dac_value & 0x0FFF)
    high_byte = (command >> 8) & 0xFF
    low_byte  =  command       & 0xFF

    GPIO.output(DAC_CS_PIN, GPIO.LOW)
    board.spi.xfer2([high_byte, low_byte])
    GPIO.output(DAC_CS_PIN, GPIO.HIGH)


# ── ADC ───────────────────────────────────────────────────────────────────────

def send_and_read(board, v_electrochem, adc_samples=10):
    """
    Sends DAC setpoint then reads ADS1115 channels A0 (RE) and A1 (TIA).

    OPTIMIZATION — Continuous mode + batch channel reading:
    ──────────────────────────────────────────────────────
    Old: alternating RE/TIA reads in single-shot mode
         → N×2 MUX switches, N×2 conversion waits (~11ms each) → ~264ms for 12 samples

    New: batch all RE reads first, then all TIA reads (continuous mode)
         → 2 MUX switches total
         → First read per channel: MUX switch + settle (~3ms)
         → Subsequent reads same channel: register read only (~0.4ms)
         → Total: ~18ms for 10 samples — fits 100ms budget easily ✓

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float
    adc_samples   : int — number of ADC readings to average per channel

    Returns
    -------
    v_a0        : float        — averaged RE  voltage (physical, 0–5V)
    v_a2        : float        — averaged TIA voltage (physical, 0–5V)
    re_samples  : list[float]  — individual RE  readings before averaging
    tia_samples : list[float]  — individual TIA readings before averaging
    """
    send_dac(board, v_electrochem)
    time.sleep(DAC_SETTLE_S)    # 1ms settle — reduced from 5ms (MCP4921 is µs-fast)

    # Batch read RE (A0) — all samples same channel → fast register reads after first
    re_samples  = [board.chan_re.voltage  for _ in range(adc_samples)]

    # Batch read TIA (A1) — same principle
    tia_samples = [board.chan_tia.voltage for _ in range(adc_samples)]

    v_a0 = sum(re_samples)  / adc_samples
    v_a2 = sum(tia_samples) / adc_samples

    return v_a0, v_a2, re_samples, tia_samples


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_voltage(v_a0_physical):
    """
    Converts raw A0 physical voltage to electrochemical voltage.
    Op-amp buffer inverts the RE signal.

    Formula: V_electrochem = -(V_physical - VIRTUAL_GND)

    Parameters
    ----------
    v_a0_physical : float  (0 to 5V)

    Returns
    -------
    float : electrochemical voltage in volts
    """
    return -(v_a0_physical - VIRTUAL_GND)


def convert_current(v_a2_physical, r_shunt):
    """
    Converts raw A1 (TIA) physical voltage to current in amperes.

    Formula: I = (V_tia - VIRTUAL_GND) / R_shunt

    Parameters
    ----------
    v_a2_physical : float  (0 to 5V)
    r_shunt       : float  (ohms)

    Returns
    -------
    float : current in amperes
    """
    return (v_a2_physical - VIRTUAL_GND) / r_shunt