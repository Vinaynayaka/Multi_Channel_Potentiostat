/*
  potentiostat_cv.ino
  
  JUAMI Potentiostat - Arduino Mega
  ----------------------------------
  - Receives DAC setpoints from Python over Serial
  - Writes voltage to MCP4921 DAC via SPI
  - Reads RE voltage (A0) and TIA current voltage (A2)
  - Streams readings back to Python as CSV: "voltage_raw,current_raw\n"

  Virtual Ground: 2.5V
  DAC: MCP4921 (12-bit, SPI)
  ADC: Arduino Mega built-in (10-bit, 0-5V)

  Wiring:
    MCP4921 CS  -> Pin 10
    MCP4921 SCK -> Pin 52 (SPI SCK)
    MCP4921 SDI -> Pin 51 (SPI MOSI)
    RE buffer output -> A0
    TIA output       -> A2
*/

#include <SPI.h>

// MCP4921 Chip Select pin
#define DAC_CS_PIN 10

// Commands from Python (single byte)
#define CMD_SET_DAC   0x01   // followed by 2 bytes: high_byte, low_byte (12-bit value)
#define CMD_READ_ADC  0x02   // Arduino reads A0 and A2, sends back two 16-bit integers

void setup() {
  Serial.begin(115200);
  
  // SPI setup for MCP4921
  SPI.begin();
  SPI.setDataMode(SPI_MODE0);
  SPI.setBitOrder(MSBFIRST);
  SPI.setClockDivider(SPI_CLOCK_DIV16); // 1 MHz SPI clock
  
  pinMode(DAC_CS_PIN, OUTPUT);
  digitalWrite(DAC_CS_PIN, HIGH); // deselect DAC
  
  // Set DAC to virtual ground (midpoint = 2048) at startup
  write_dac(2048);
  
  // Configure ADC
  analogReference(DEFAULT); // 5V reference
}

void loop() {
  if (Serial.available() >= 1) {
    byte cmd = Serial.read();

    if (cmd == CMD_SET_DAC) {
      // Wait for 2 bytes: high byte then low byte of 12-bit DAC value
      while (Serial.available() < 2);
      byte high_byte = Serial.read();
      byte low_byte  = Serial.read();
      uint16_t dac_value = ((uint16_t)high_byte << 8) | low_byte;
      dac_value = constrain(dac_value, 0, 4095);
      write_dac(dac_value);
    }

    else if (cmd == CMD_READ_ADC) {
      // Read A0 (RE voltage) and A2 (TIA / current voltage)
      // Average 8 readings to reduce noise
      long a0_sum = 0;
      long a2_sum = 0;
      for (int i = 0; i < 8; i++) {
        a0_sum += analogRead(A0);
        a2_sum += analogRead(A2);
        delayMicroseconds(200);
      }
      uint16_t a0_avg = a0_sum / 8;
      uint16_t a2_avg = a2_sum / 8;

      // Send back as 4 bytes: A0 high, A0 low, A2 high, A2 low
      Serial.write((a0_avg >> 8) & 0xFF);
      Serial.write(a0_avg & 0xFF);
      Serial.write((a2_avg >> 8) & 0xFF);
      Serial.write(a2_avg & 0xFF);
    }
  }
}

/*
  write_dac()
  Sends a 12-bit value to the MCP4921 DAC via SPI.
  
  MCP4921 16-bit write format:
  Bit 15: 0 = channel A (only channel on MCP4921)
  Bit 14: 0 = unbuffered VREF
  Bit 13: 1 = 1x gain  (output = VREF * D/4096, VREF = 5V)
  Bit 12: 1 = DAC active (SHDN = 1)
  Bits 11-0: 12-bit data
*/
void write_dac(uint16_t value) {
  uint16_t spi_data = 0x3000 | (value & 0x0FFF);
  // 0x3000 = 0011 0000 0000 0000
  //           ││
  //           │└── BUF=0 (unbuffered), GA=1 (1x gain), SHDN=1 (active)
  //           └─── A/B=0 (channel A)

  digitalWrite(DAC_CS_PIN, LOW);
  SPI.transfer((spi_data >> 8) & 0xFF); // high byte
  SPI.transfer(spi_data & 0xFF);        // low byte
  digitalWrite(DAC_CS_PIN, HIGH);
}
