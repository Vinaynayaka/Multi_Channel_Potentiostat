/*
  ArduinoMega_DAC_MCP4921.ino
  ============================
  JUAMI Potentiostat - Arduino Mega
  
  Protocol:
    - Python sends a physical voltage as a float string e.g. "3.0000\n"
    - Arduino writes that voltage to MCP4921 DAC via SPI
    - Arduino reads A0 (RE voltage) and A2 (TIA current voltage)
    - Arduino sends back one CSV line: "timestamp,setpoint,vA0,vA2\n"

  Virtual Ground: 2.5V physical = 0V electrochemical
  DAC: MCP4921 12-bit, SPI
  ADC: Arduino Mega built-in 10-bit (0-5V)

  Wiring:
    MCP4921 CS  -> Pin 10
    MCP4921 SCK -> Pin 52 (SPI SCK on Mega)
    MCP4921 SDI -> Pin 51 (SPI MOSI on Mega)
    RE buffer output -> A0
    TIA output       -> A2
*/

#include <SPI.h>

const int CS_PIN  = 10;
const float V_REF = 5.0;

void setup() {
  Serial.begin(115200);

  SPI.begin();
  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);

  // Set DAC to virtual ground (2.5V) at startup
  writeDAC(2.5);

  // Handshake — Python waits for this before proceeding
  Serial.println("READY");
}

void loop() {
  if (Serial.available()) {
    // Read the voltage string sent by Python e.g. "3.0000\n"
    float v = Serial.parseFloat();

    // Clamp to valid range
    if (v < 0.0) v = 0.0;
    if (v > V_REF) v = V_REF;

    // Write to DAC
    writeDAC(v);

    // Small settle time before reading
    delay(5);

    // Read A0 and A2, average 16 readings each to reduce noise
    long a0_sum = 0;
    long a2_sum = 0;
    for (int i = 0; i < 16; i++) {
      a0_sum += analogRead(A0);
      a2_sum += analogRead(A2);
      delayMicroseconds(200);
    }
    float vA0 = (a0_sum / 16.0) * (V_REF / 1023.0);
    float vA2 = (a2_sum / 16.0) * (V_REF / 1023.0);

    unsigned long t = millis();

    // Send back: timestamp, setpoint, vA0, vA2
    Serial.print(t);
    Serial.print(",");
    Serial.print(v, 4);
    Serial.print(",");
    Serial.print(vA0, 4);
    Serial.print(",");
    Serial.println(vA2, 4);
  }
}

/*
  writeDAC()
  ----------
  Sends a physical voltage (0-5V) to the MCP4921 DAC via SPI.

  MCP4921 16-bit write format:
    Bit 15   : 0 = Channel A
    Bit 14   : 0 = Unbuffered VREF
    Bit 13   : 1 = 1x gain (output = VREF x D/4096)
    Bit 12   : 1 = DAC active
    Bits 11-0: 12-bit data value
*/
void writeDAC(float voltage) {
  uint16_t dacValue = (uint16_t)((voltage / V_REF) * 4095);
  dacValue = constrain(dacValue, 0, 4095);

  uint16_t command = 0x3000 | (dacValue & 0x0FFF);

  digitalWrite(CS_PIN, LOW);
  SPI.transfer16(command);
  digitalWrite(CS_PIN, HIGH);
}
