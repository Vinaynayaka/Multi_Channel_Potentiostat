"""
hardware.py  —  Raspberry Pi Hardware Layer
============================================
Replaces Arduino serial communication with direct RPi hardware:
  - MCP4921 DAC via SPI (same chip as Arduino version)
  - ADS1115 16-bit ADC via I2C (replaces Arduino's 10-bit ADC)

Same function signatures as the Arduino hardware.py so cv.py, lsv.py,
ca.py and main.py work without changes.

Virtual ground : 2.5V physical = 0V electrochemical
DAC            : MCP4921 12-bit SPI
ADC            : ADS1115 16-bit I2C (±6.144V range to cover full 0-5V)

Wiring:
  MCP4921
    VDD  -> 5V
    VSS  -> GND
    CS   -> GPIO 8  (CE0, SPI0)
    SCK  -> GPIO 11 (SCLK, SPI0)
    SDI  -> GPIO 10 (MOSI, SPI0)
    LDAC -> GND     (DAC latches immediately on CS rising edge)
    VREF -> 5V

  ADS1115 (powered at 5V, I2C level-shifted to 3.3V)
    VDD  -> 5V
    GND  -> GND
    SCL  -> GPIO 3  (SCL, I2C1) via level shifter
    SDA  -> GPIO 2  (SDA, I2C1) via level shifter
    ADDR -> GND     (I2C address = 0x48)
    A0   -> RE buffer output
    A1   -> TIA output (current voltage)

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
except ImportError as e:
    sys.exit(
        f"Missing library: {e}\n"
        "Install with:\n"
        "  pip install spidev RPi.GPIO adafruit-circuitpython-ads1x15"
    )

# ── Constants ─────────────────────────────────────────────────────────────────

VIRTUAL_GND  = 2.575      # volts — physical midpoint of the circuit
V_REF        = 5.157      # volts — supply voltage
DAC_BITS     = 4095     # 12-bit DAC max value
DAC_CS_PIN   = 8        # GPIO 8 = CE0 — chip select for MCP4921
ADC_AVERAGE  = 10       # number of ADS1115 readings to average per point
ADC_GAIN     = 2/3      # ADS1115 PGA ±6.144V — covers full 0-5V range


# ── RPiBoard class ────────────────────────────────────────────────────────────

class RPiBoard:
    """
    Holds all hardware handles for the RPi potentiostat.
    Passed as the first argument to all hardware functions,
    replacing the serial.Serial object used in the Arduino version.
    """

    def __init__(self):
        # GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DAC_CS_PIN, GPIO.OUT, initial=GPIO.HIGH)

        # SPI for MCP4921 DAC
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)             # SPI bus 0, device 0 (CE0)
        self.spi.max_speed_hz = 1000000 # 1 MHz — safe for MCP4921
        self.spi.mode = 0b00            # CPOL=0, CPHA=0

        # I2C + ADS1115 for ADC
        i2c      = busio.I2C(rpi_board.SCL, rpi_board.SDA)
        ads      = ADS.ADS1115(i2c, address=0x48)
        ads.gain = ADC_GAIN             # ±6.144V range
        ads.data_rate = 860             # maximum sample rate (860 SPS)

        self.chan_re  = AnalogIn(ads, 0)   # A0 — RE buffer voltage
        self.chan_tia = AnalogIn(ads, 1)   # A1 — TIA output voltage

    def reset_input_buffer(self):
        """No-op — exists for compatibility with Arduino version."""
        pass

    def reset_output_buffer(self):
        """No-op — exists for compatibility with Arduino version."""
        pass

    def close(self):
        self.spi.close()
        GPIO.cleanup()


# ── Connection ────────────────────────────────────────────────────────────────

def connect():
    """
    Initializes RPi SPI and I2C hardware.
    Returns an RPiBoard object (replaces serial.Serial from Arduino version).

    Returns
    -------
    board : RPiBoard
    """
    print("Initializing RPi potentiostat hardware...")
    try:
        board = RPiBoard()
        print("SPI (MCP4921 DAC) initialized on CE0.")
        print("I2C (ADS1115 ADC) initialized at address 0x48.")
        print("Hardware ready.\n")
        return board
    except Exception as e:
        sys.exit(f"Hardware initialization failed: {e}")


def close(board):
    """
    Returns DAC to virtual ground then cleans up SPI and GPIO.

    Parameters
    ----------
    board : RPiBoard
    """
    print("Returning to virtual ground and closing hardware...")
    send_and_read(board, 0.0)   # confirmed write + response
    time.sleep(2)
    board.close()
    print("Hardware closed.")


# ── DAC ───────────────────────────────────────────────────────────────────────

def voltage_to_dac(v_electrochem):
    """
    Converts electrochemical voltage to 12-bit DAC integer.

    Formula: dac = int(((V + 2.5) / 5.0) * 4095)

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

    MCP4921 16-bit command:
      Bit 15   : 0 = Channel A
      Bit 14   : 0 = Unbuffered VREF
      Bit 13   : 1 = 1x gain
      Bit 12   : 1 = DAC active
      Bits 11-0: 12-bit value

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float
    """
    dac_value = voltage_to_dac(v_electrochem)
    command   = 0x3000 | (dac_value & 0x0FFF)
    high_byte = (command >> 8) & 0xFF
    low_byte  =  command & 0xFF

    GPIO.output(DAC_CS_PIN, GPIO.LOW)
    board.spi.xfer2([high_byte, low_byte])
    GPIO.output(DAC_CS_PIN, GPIO.HIGH)


# ── ADC ───────────────────────────────────────────────────────────────────────

def send_and_read(board, v_electrochem):
    """
    Sends DAC setpoint then reads ADS1115 channels A0 and A1.
    Averages ADC_AVERAGE readings per channel to reduce noise.

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float

    Returns
    -------
    v_a0       : float       — averaged RE  voltage (physical, 0-5V)
    v_a2       : float       — averaged TIA voltage (physical, 0-5V)
    re_samples : list[float] — individual RE  readings before averaging
    tia_samples: list[float] — individual TIA readings before averaging
    """
    send_dac(board, v_electrochem)
    time.sleep(0.005)               # DAC settle time

    re_samples  = []
    tia_samples = []

    for _ in range(ADC_AVERAGE):
        re_samples.append(board.chan_re.voltage)
        tia_samples.append(board.chan_tia.voltage)

    v_a0 = sum(re_samples)  / ADC_AVERAGE
    v_a2 = sum(tia_samples) / ADC_AVERAGE

    return v_a0, v_a2, re_samples, tia_samples


def convert_voltage(v_a0_physical):
    """
    Converts raw A0 physical voltage to electrochemical voltage.
    Op-amp buffer in JUAMI inverts the RE signal.

    Formula: V_electrochem = -(V_physical - 2.5)

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

    Formula: I = (V_tia - 2.5) / R_shunt

    Parameters
    ----------
    v_a2_physical : float  (0 to 5V)
    r_shunt       : float  (ohms)

    Returns
    -------
    float : current in amperes
    """
    return (v_a2_physical - VIRTUAL_GND) / r_shunt