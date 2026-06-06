"""
hardware.py  —  Raspberry Pi Hardware Layer
============================================
RPi potentiostat: MCP4921 12-bit DAC (SPI) + ADS1115 16-bit ADC (I2C)

Virtual ground : 2.5V physical = 0V electrochemical
DAC            : MCP4921  — SPI0, CE0 (GPIO 8)
ADC            : ADS1115  — I2C1, address 0x48, 860 SPS

Wiring
------
  MCP4921
    VDD, VREF → 5 V       VSS → GND
    CS  → GPIO 8 (CE0)    SCK → GPIO 11 (SCLK)
    SDI → GPIO 10 (MOSI)  LDAC → GND

  ADS1115
    VDD → 5 V             GND → GND
    SCL → GPIO 3 (I2C1)   SDA → GPIO 2 (I2C1)
    ADDR → GND (0x48)     A0  → RE buffer    A1 → TIA output

  Level shifters
    SPI  (3.3V→5V) : 74AHCT125 unidirectional (MOSI, SCLK, CE0)
    I2C  (3.3V↔5V) : BSS138-based bidirectional (SDA, SCL)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Data Rate Reference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
t_meas = 5 + adc_samples × 3.4  ms   (100 kHz I²C, both channels)
t_meas = 5 + adc_samples × 2.64 ms   (400 kHz I²C)

  adc_samples │  t_meas (100kHz) │  max pts/s
 ─────────────┼──────────────────┼────────────
       1      │     8.4 ms       │   119
       5      │    22.0 ms       │    45
      10      │    39.0 ms       │    26
      20      │    73.0 ms       │    14

dt (from config) must be strictly greater than t_meas.
dE  = sweep_rate × dt  must be ≥ DAC_LSB (≈1.259 mV).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ── Calibration constants (edit only here, never in experiment files) ─────────

VIRTUAL_GND  = 2.575    # V — physical voltage that maps to 0 V electrochemical
V_REF        = 5.157    # V — DAC reference / supply voltage
DAC_BITS     = 4095     # 12-bit full scale (2¹² − 1)
DAC_CS_PIN   = 8        # GPIO 8 = CE0
ADC_GAIN     = 2 / 3    # ADS1115 PGA ±6.144 V — covers full 0–5 V rail
DAC_SETTLE_S = 0.005    # seconds to wait after DAC write before sampling ADC

# Derived calibration values (read-only, used by main.py validation)
DAC_LSB_V    = V_REF / DAC_BITS          # ≈ 1.259 mV — smallest DAC step
ADC_LSB_V    = 6.144 / 32767             # ≈ 187.5 µV — smallest ADC step


# ── RPiBoard ──────────────────────────────────────────────────────────────────

class RPiBoard:
    """
    Holds all hardware handles for the RPi potentiostat.
    Passed as the first argument to every hardware function.
    """

    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DAC_CS_PIN, GPIO.OUT, initial=GPIO.HIGH)

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 1_000_000
        self.spi.mode = 0b00

        i2c      = busio.I2C(rpi_board.SCL, rpi_board.SDA)
        ads      = ADS.ADS1115(i2c, address=0x48)
        ads.gain = ADC_GAIN
        ads.data_rate = 860

        self.chan_re  = AnalogIn(ads, ADS.P0)   # A0 — RE voltage
        self.chan_tia = AnalogIn(ads, ADS.P1)   # A1 — TIA voltage

    def reset_input_buffer(self):   pass   # compatibility stub
    def reset_output_buffer(self):  pass   # compatibility stub

    def close(self):
        self.spi.close()
        GPIO.cleanup()


# ── Connection ────────────────────────────────────────────────────────────────

def connect():
    """Initializes RPi SPI + I2C hardware. Returns RPiBoard."""
    print("Initializing RPi potentiostat hardware...")
    try:
        board = RPiBoard()
        print("  SPI (MCP4921 DAC) → CE0, 1 MHz")
        print("  I2C (ADS1115 ADC) → 0x48, 860 SPS, ±6.144 V")
        print(f"  DAC LSB = {DAC_LSB_V*1000:.3f} mV")
        print(f"  ADC LSB = {ADC_LSB_V*1e6:.1f} µV")
        print("Hardware ready.\n")
        return board
    except Exception as e:
        sys.exit(f"Hardware initialization failed: {e}")


def close(board):
    """Returns DAC to 0 V then cleans up SPI and GPIO."""
    print("Returning to 0 V and closing hardware...")
    send_dac(board, 0.0)
    time.sleep(0.5)
    board.close()
    print("Hardware closed.")


# ── DAC ───────────────────────────────────────────────────────────────────────

def voltage_to_dac(v_electrochem):
    """
    Converts electrochemical voltage (−2.575 V to +2.582 V) to 12-bit integer.
    Formula: dac = int( ((V_echem + V_gnd) / V_ref) × 4095 )
    """
    normalized = (v_electrochem + VIRTUAL_GND) / V_REF
    normalized = max(0.0, min(1.0, normalized))
    return int(normalized * DAC_BITS)


def send_dac(board, v_electrochem):
    """
    Sends voltage setpoint to MCP4921 via SPI.
    MCP4921 16-bit word: [0][BUF][GA][SHDN][D11..D0]
    Config: unbuffered VREF, 1× gain, output active.
    """
    dac_val   = voltage_to_dac(v_electrochem)
    command   = 0x3000 | (dac_val & 0x0FFF)
    high_byte = (command >> 8) & 0xFF
    low_byte  =  command       & 0xFF
    GPIO.output(DAC_CS_PIN, GPIO.LOW)
    board.spi.xfer2([high_byte, low_byte])
    GPIO.output(DAC_CS_PIN, GPIO.HIGH)


# ── ADC ───────────────────────────────────────────────────────────────────────

def send_and_read(board, v_electrochem, n_samples=10):
    """
    Sends DAC setpoint, waits for settle, then reads both ADS1115
    channels n_samples times each.

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float   target electrochemical voltage (V)
    n_samples     : int     readings per channel per call
                            t_meas ≈ 5 + n_samples × 3.4 ms  (100 kHz I²C)

    Returns
    -------
    v_a0_avg : float          averaged A0 physical voltage (V)
    v_a2_avg : float          averaged A1 physical voltage (V)
    raw_a0   : list[float]    all n_samples A0 readings (V)  — physical
    raw_a2   : list[float]    all n_samples A1 readings (V)  — physical

    Note
    ----
    raw_a0 and raw_a2 hold raw physical voltages (0–5 V).
    Convert using convert_voltage() and convert_current() to obtain
    electrochemical voltage and current for each individual sample.
    These conversions are performed in save_data() so every row in
    the raw-ADC CSV carries both physical AND electrochemical values.
    """
    send_dac(board, v_electrochem)
    time.sleep(DAC_SETTLE_S)

    raw_a0 = [board.chan_re.voltage  for _ in range(n_samples)]
    raw_a2 = [board.chan_tia.voltage for _ in range(n_samples)]

    v_a0_avg = sum(raw_a0) / n_samples
    v_a2_avg = sum(raw_a2) / n_samples

    return v_a0_avg, v_a2_avg, raw_a0, raw_a2


# ── Signal conversion ─────────────────────────────────────────────────────────

def convert_voltage(v_a0_physical):
    """
    Converts A0 physical voltage to electrochemical voltage.
    Formula: V_echem = −(V_A0 − V_gnd)
    The JUAMI op-amp buffer inverts the RE signal.
    """
    return -(v_a0_physical - VIRTUAL_GND)


def convert_current(v_a2_physical, r_shunt):
    """
    Converts A1 (TIA) physical voltage to current in amperes.
    Formula: I = (V_A1 − V_gnd) / R_shunt
    """
    return (v_a2_physical - VIRTUAL_GND) / r_shunt