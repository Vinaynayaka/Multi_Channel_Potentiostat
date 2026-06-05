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
Data Rate Quick Reference  (ADS1115 @ 860 SPS, 100 kHz I2C)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each ADC reading ≈ 1.7 ms  (conversion 1.16 ms + I2C overhead 0.5 ms).
DAC settle time  = 5 ms fixed.  Both channels read per measurement point.

  adc_samples │  ms / pt  │  max pts/s
 ─────────────┼───────────┼────────────
       1      │   ~8 ms   │  ~120 pts/s   (high noise — avoid if possible)
       5      │  ~22 ms   │   ~45 pts/s
      10      │  ~39 ms   │   ~26 pts/s
      20      │  ~73 ms   │   ~14 pts/s

  Tip: add  dtparam=i2c_arm_baudrate=400000  to /boot/firmware/config.txt
  to enable 400 kHz I2C — gives ~30 % faster reads:
       5 → ~18 ms / ~55 pts/s,  10 → ~31 ms / ~32 pts/s

Recommended steps_per_volt at common sweep rates
─────────────────────────────────────────────────
  Sweep Rate │ steps/V │ pts/s │ ms/pt │ adc_samples │ Status
 ────────────┼─────────┼───────┼───────┼─────────────┼────────
   50 mV/s  │   100   │   5   │  200  │   ≤ 20      │  ✓ safe
   50 mV/s  │   200   │  10   │  100  │   ≤ 10      │  ✓ safe
   50 mV/s  │   400   │  20   │   50  │   ≤  5      │  ✓ safe
   50 mV/s  │   600   │  30   │   33  │      2      │  ⚠ tight
   50 mV/s  │  1000   │  50   │   20  │      1      │  ✗ too fast
  100 mV/s  │    50   │   5   │  200  │   ≤ 20      │  ✓ safe
  100 mV/s  │   100   │  10   │  100  │   ≤ 10      │  ✓ safe
  100 mV/s  │   200   │  20   │   50  │   ≤  5      │  ✓ safe
  100 mV/s  │   300   │  30   │   33  │      2      │  ⚠ tight
  100 mV/s  │   500   │  50   │   20  │      1      │  ✗ too fast

Live plot impact: each matplotlib update adds 20–80 ms on RPi.
Updates are throttled to ≤ 5 Hz so data timing is preserved.
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


# ── Constants ─────────────────────────────────────────────────────────────────

VIRTUAL_GND  = 2.575    # V — physical voltage that maps to 0 V electrochemical
V_REF        = 5.157    # V — DAC reference / supply voltage
DAC_BITS     = 4095     # 12-bit DAC full-scale (2¹² − 1)
DAC_CS_PIN   = 8        # GPIO 8 = CE0 — MCP4921 chip select
ADC_GAIN     = 2 / 3    # ADS1115 PGA ±6.144 V — covers full 0–5 V rail
DAC_SETTLE_S = 0.005    # seconds to wait after DAC write before sampling ADC


# ── RPiBoard ──────────────────────────────────────────────────────────────────

class RPiBoard:
    """
    Holds all hardware handles for the RPi potentiostat.
    Passed as the first argument to every hardware function,
    replacing the serial.Serial object from the Arduino version.
    """

    def __init__(self):
        # GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DAC_CS_PIN, GPIO.OUT, initial=GPIO.HIGH)

        # SPI — MCP4921 DAC
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)               # bus 0, device 0 (CE0)
        self.spi.max_speed_hz = 1_000_000 # 1 MHz — safe for MCP4921
        self.spi.mode = 0b00              # CPOL=0, CPHA=0

        # I2C — ADS1115 ADC
        i2c      = busio.I2C(rpi_board.SCL, rpi_board.SDA)
        ads      = ADS.ADS1115(i2c, address=0x48)
        ads.gain = ADC_GAIN
        ads.data_rate = 860               # 860 SPS — maximum rate

        self.chan_re  = AnalogIn(ads, ADS.P0)   # A0 — RE buffer voltage
        self.chan_tia = AnalogIn(ads, ADS.P1)   # A1 — TIA output voltage

    # Compatibility stubs (Arduino version used these on serial.Serial)
    def reset_input_buffer(self):   pass
    def reset_output_buffer(self):  pass

    def close(self):
        self.spi.close()
        GPIO.cleanup()


# ── Connection ────────────────────────────────────────────────────────────────

def connect():
    """
    Initializes RPi SPI and I2C hardware.

    Returns
    -------
    board : RPiBoard
    """
    print("Initializing RPi potentiostat hardware...")
    try:
        board = RPiBoard()
        print("  SPI (MCP4921 DAC) → CE0, 1 MHz")
        print("  I2C (ADS1115 ADC) → 0x48, 860 SPS, ±6.144 V")
        print("Hardware ready.\n")
        return board
    except Exception as e:
        sys.exit(f"Hardware initialization failed: {e}")


def close(board):
    """
    Returns DAC to virtual ground (0 V electrochemical) then cleans up
    SPI and GPIO resources.

    Parameters
    ----------
    board : RPiBoard
    """
    print("Returning to 0 V and closing hardware...")
    send_dac(board, 0.0)
    time.sleep(0.5)        # allow cell to relax before GPIO cleanup
    board.close()
    print("Hardware closed.")


# ── DAC ───────────────────────────────────────────────────────────────────────

def voltage_to_dac(v_electrochem):
    """
    Converts electrochemical voltage to 12-bit DAC integer.

    Formula : dac = int( ((V_echem + V_gnd) / V_ref) × 4095 )

    Parameters
    ----------
    v_electrochem : float   −2.5 V to +2.5 V

    Returns
    -------
    int : 0 to 4095
    """
    normalized = (v_electrochem + VIRTUAL_GND) / V_REF
    normalized = max(0.0, min(1.0, normalized))   # clamp to valid DAC range
    return int(normalized * DAC_BITS)


def send_dac(board, v_electrochem):
    """
    Sends voltage setpoint to MCP4921 via SPI.

    MCP4921 16-bit word layout:
      Bit 15   : 0 = Channel A
      Bit 14   : 0 = Unbuffered VREF
      Bit 13   : 1 = 1× gain
      Bit 12   : 1 = DAC active (not shutdown)
      Bits 11–0: 12-bit value

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float
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
    Sends DAC setpoint, waits for the output to settle, then reads
    ADS1115 channels A0 (RE) and A1 (TIA) n_samples times each.

    The averaged values are returned for use in experiment plots and
    processed CSV. The full raw lists are returned so every individual
    reading can be saved to the raw-ADC CSV for post-processing.

    Parameters
    ----------
    board         : RPiBoard
    v_electrochem : float   target electrochemical voltage (V)
    n_samples     : int     ADC readings to collect and average per point.
                            Higher → less noise, but longer per-point time.
                            See the data-rate table in the module docstring.

    Returns
    -------
    v_a0_avg : float         averaged A0 physical voltage (V)
    v_a2_avg : float         averaged A1 physical voltage (V)
    raw_a0   : list[float]   all n_samples individual A0 readings (V)
    raw_a2   : list[float]   all n_samples individual A1 readings (V)

    Notes
    -----
    raw_a0 and raw_a2 contain every single ADC reading taken during this
    measurement point. Save them to the raw-ADC CSV to retain full
    resolution. Apply different averaging schemes in post-processing
    without re-running the experiment.
    """
    send_dac(board, v_electrochem)
    time.sleep(DAC_SETTLE_S)   # allow DAC output and op-amps to settle

    raw_a0 = [board.chan_re.voltage  for _ in range(n_samples)]
    raw_a2 = [board.chan_tia.voltage for _ in range(n_samples)]

    v_a0_avg = sum(raw_a0) / n_samples
    v_a2_avg = sum(raw_a2) / n_samples

    return v_a0_avg, v_a2_avg, raw_a0, raw_a2


# ── Signal conversion ─────────────────────────────────────────────────────────

def convert_voltage(v_a0_physical):
    """
    Converts A0 physical voltage to electrochemical voltage (V).

    The op-amp buffer in the JUAMI design inverts the RE signal:
      V_echem = −(V_physical − V_gnd)

    Parameters
    ----------
    v_a0_physical : float   0 to 5 V (raw physical ADC reading)

    Returns
    -------
    float : electrochemical voltage in volts
    """
    return -(v_a0_physical - VIRTUAL_GND)


def convert_current(v_a2_physical, r_shunt):
    """
    Converts A1 (TIA output) physical voltage to current in amperes.

      I = (V_tia − V_gnd) / R_shunt

    Parameters
    ----------
    v_a2_physical : float   0 to 5 V (raw physical ADC reading)
    r_shunt       : float   shunt / feedback resistor in ohms

    Returns
    -------
    float : current in amperes
    """
    return (v_a2_physical - VIRTUAL_GND) / r_shunt