/*
  Actuator Testbench firmware 1.0.0 / protocol 1

  Proof-of-concept laboratory controller for a classic Arduino Nano
  (ATmega328P, 5 V) + BTS7960/HW-039 +
  Adafruit INA228. It starts disabled. Firmware safety remains active without
  the PC. See ../docs/SERIAL_PROTOCOL.md and ../README.md.

  Adafruit's INA228 is installed on the high side before the H-bridge. Its
  reading is supply current. During PWM recirculation it is not necessarily the
  instantaneous motor winding current, so it is suitable for monitoring and a
  time-qualified protection limit, not cycle-by-cycle current control.
*/
#include <Wire.h>
#include <EEPROM.h>
#include <string.h>
#include <Adafruit_INA228.h>
#include "config.h"

Adafruit_INA228 ina228;
Config cfg;

struct StoredConfig {
  uint32_t magic;
  uint16_t version;
  Config data;
  uint32_t checksum;
};

// Runtime state. All time comparisons use subtraction and tolerate millis()
// and micros() rollover.
Mode mode = Mode::DISABLED;
bool enabled = false;
FaultCode fault = FaultCode::NONE;
bool faultLatched = false;
uint32_t faultSinceMs = 0;
uint32_t lastValidCommandMs = 0;

uint16_t feedbackRaw = 0, commandRaw = 0;
float feedbackPct = 0.0f, commandPct = 0.0f, targetPct = 50.0f;
bool feedbackValid = false, commandValid = false;
float currentA = 0.0f, filteredCurrentA = 0.0f, peakCurrentA = 0.0f;
float busVoltageV = 0.0f, shuntVoltageMv = 0.0f, powerW = 0.0f;
bool inaOk = false, softLimitActive = false, stallActive = false;
bool lowerLimitActive = false, upperLimitActive = false;

int16_t requestedPwm = 0, appliedPwm = 0;
int8_t manualDirection = 0;
uint8_t manualPwm = 100;
float integral = 0.0f, previousError = 0.0f, filteredDerivative = 0.0f;
uint32_t reversalUntilMs = 0;
int8_t pendingDirection = 0;

uint32_t lastPotUs = 0, lastControlUs = 0, lastCurrentUs = 0;
uint32_t lastTelemetryUs = 0, lastControlPeriodUs = 0, lastTelemetryPeriodUs = 0;
uint32_t overcurrentSinceMs = 0, stallSinceMs = 0;
float stallStartPosition = 0.0f;

char rxLine[SERIAL_RX_SIZE];
uint8_t rxLength = 0;
bool rxReady = false;
int8_t telemetryField = -1;
uint32_t telemetryPacketTimeMs = 0;
bool faultEventPending = false;
FaultCode pendingFaultCode = FaultCode::NONE;
bool pendingFaultLatched = false;
int8_t configPrintIndex = -1;

static float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
static int16_t clampPwm(int16_t v) { return v < -255 ? -255 : (v > 255 ? 255 : v); }
static bool elapsedUs(uint32_t now, uint32_t then, uint32_t interval) { return uint32_t(now - then) >= interval; }
static bool elapsedMs(uint32_t now, uint32_t then, uint32_t interval) { return uint32_t(now - then) >= interval; }

const __FlashStringHelper* modeText(Mode m) {
  switch (m) {
    case Mode::MANUAL: return F("MANUAL"); case Mode::PWM: return F("PWM");
    case Mode::POSITION: return F("POSITION"); case Mode::FOLLOW: return F("FOLLOW");
    case Mode::STEP: return F("STEP"); case Mode::CURRENT_TEST: return F("CURRENT_TEST");
    default: return F("DISABLED");
  }
}

const __FlashStringHelper* faultText(FaultCode f) {
  switch (f) {
    case FaultCode::EMERGENCY_STOP: return F("Emergency stop active");
    case FaultCode::WATCHDOG_TIMEOUT: return F("Serial watchdog timeout");
    case FaultCode::FEEDBACK_POT_INVALID: return F("Feedback potentiometer invalid");
    case FaultCode::COMMAND_POT_INVALID: return F("Command potentiometer invalid");
    case FaultCode::INA_NOT_DETECTED: return F("INA228 not detected");
    case FaultCode::CURRENT_INVALID: return F("Current measurement invalid");
    case FaultCode::HARD_OVERCURRENT: return F("Hard overcurrent");
    case FaultCode::STALL_DETECTED: return F("Stall detected");
    case FaultCode::LOWER_LIMIT: return F("Lower software limit");
    case FaultCode::UPPER_LIMIT: return F("Upper software limit");
    case FaultCode::INVALID_COMMAND: return F("Invalid command");
    case FaultCode::INTERNAL_CONFIG: return F("Internal configuration error");
    default: return F("No fault");
  }
}

// The BTS7960 must never see both direction PWM inputs active. Optional brake
// stop keeps enables high with both PWM low; because clone behavior varies,
// the safer default is coast (both PWM low and both enables low).
void setMotorOutput(int16_t signedPwm) {
  signedPwm = clampPwm(signedPwm);
  if (cfg.motorInvert) signedPwm = -signedPwm;
  if (signedPwm == 0) {
    analogWrite(PIN_RPWM, 0); analogWrite(PIN_LPWM, 0);
    digitalWrite(PIN_R_EN, cfg.brakeStop ? HIGH : LOW);
    digitalWrite(PIN_L_EN, cfg.brakeStop ? HIGH : LOW);
  } else if (signedPwm > 0) {
    analogWrite(PIN_LPWM, 0);       // opposite side off first
    digitalWrite(PIN_R_EN, HIGH); digitalWrite(PIN_L_EN, HIGH);
    analogWrite(PIN_RPWM, signedPwm);
  } else {
    analogWrite(PIN_RPWM, 0);       // opposite side off first
    digitalWrite(PIN_R_EN, HIGH); digitalWrite(PIN_L_EN, HIGH);
    analogWrite(PIN_LPWM, -signedPwm);
  }
}

void resetController() {
  integral = previousError = filteredDerivative = 0.0f;
  requestedPwm = appliedPwm = 0; pendingDirection = 0; reversalUntilMs = 0;
  manualDirection = 0; setMotorOutput(0);
}

void stopAndDisable() { enabled = false; mode = Mode::DISABLED; resetController(); }

void setFault(FaultCode code, bool latch) {
  if (code == FaultCode::NONE) return;
  if (faultLatched && !latch) return;
  if (fault != code) {
    fault = code; faultSinceMs = millis();
    // Reporting is deferred until the active telemetry line has drained. The
    // safety action itself is immediate and never waits for UART capacity.
    pendingFaultCode = code; pendingFaultLatched = latch; faultEventPending = true;
  }
  faultLatched = faultLatched || latch;
  resetController();
  if (latch) enabled = false;
}

void clearTransientFault() {
  if (!faultLatched) { fault = FaultCode::NONE; faultSinceMs = 0; }
}

bool configValid(const Config &c) {
  return c.controlHz >= 50 && c.controlHz <= 1000 && c.potHz >= 50 && c.potHz <= 1000 &&
    c.currentHz >= 10 && c.currentHz <= 200 && c.telemetryHz >= 1 && c.telemetryHz <= 100 &&
    c.watchdogMs >= 200 && c.watchdogMs <= 5000 &&
    c.feedbackMax >= c.feedbackMin + 50 && c.commandMax >= c.commandMin + 50 &&
    c.feedbackMax <= 1023 && c.commandMax <= 1023 && c.maxPwm >= c.minPwm &&
    c.maxPwm <= 255 && c.nearLimitMaxPwm <= c.maxPwm && c.lowerLimitPct >= 0 &&
    c.upperLimitPct <= 100 && c.upperLimitPct > c.lowerLimitPct + 1 &&
    c.softCurrentA > 0 && c.hardCurrentA > c.softCurrentA &&
    c.hardCurrentA <= 10.5f && c.hardCurrentDurationMs >= 10;
}

uint32_t checksumBytes(const uint8_t *data, size_t len) {
  uint32_t h = 2166136261UL;
  for (size_t i = 0; i < len; ++i) { h ^= data[i]; h *= 16777619UL; }
  return h;
}

bool loadConfig() {
  StoredConfig stored; EEPROM.get(0, stored);
  uint32_t actual = checksumBytes(reinterpret_cast<const uint8_t*>(&stored.data), sizeof(Config));
  if (stored.magic != CONFIG_MAGIC || stored.version != CONFIG_VERSION ||
      stored.checksum != actual || !configValid(stored.data)) return false;
  cfg = stored.data; return true;
}

void saveConfig() {
  StoredConfig stored;
  stored.magic = CONFIG_MAGIC; stored.version = CONFIG_VERSION; stored.data = cfg;
  stored.checksum = checksumBytes(reinterpret_cast<const uint8_t*>(&stored.data), sizeof(Config));
  // EEPROM.update writes only changed bytes, avoiding unnecessary wear.
  const uint8_t *src = reinterpret_cast<const uint8_t*>(&stored);
  for (size_t i = 0; i < sizeof(stored); ++i) EEPROM.update(i, src[i]);
}

float normalizePot(uint16_t raw, uint16_t minimum, uint16_t maximum, bool invert) {
  float pct = 100.0f * (float(raw) - minimum) / float(maximum - minimum);
  pct = clampf(pct, 0.0f, 100.0f); return invert ? 100.0f - pct : pct;
}

void samplePots() {
  uint16_t newFeedback = analogRead(PIN_FEEDBACK_POT);
  uint16_t newCommand = analogRead(PIN_COMMAND_POT);
  // ADC rails generally indicate open/short wiring. Calibration endpoints are
  // allowed; only values close to the electrical rails are declared invalid.
  feedbackValid = newFeedback >= 3 && newFeedback <= 1020;
  commandValid = newCommand >= 3 && newCommand <= 1020;
  feedbackRaw = newFeedback; commandRaw = newCommand;
  if (feedbackValid) {
    float v = normalizePot(newFeedback, cfg.feedbackMin, cfg.feedbackMax, cfg.feedbackInvert);
    feedbackPct += (1.0f - clampf(cfg.feedbackFilter, 0, .99f)) * (v - feedbackPct);
  }
  if (commandValid) {
    float v = normalizePot(newCommand, cfg.commandMin, cfg.commandMax, cfg.commandInvert);
    commandPct += (1.0f - clampf(cfg.commandFilter, 0, .99f)) * (v - commandPct);
  }
}

void sampleIna() {
  if (!inaOk) return;
  // Adafruit's readCurrent/readPower API returns mA/mW; convert to A/W.
  float i = ina228.readCurrent() / 1000.0f;
  float v = ina228.readBusVoltage();
  float s = ina228.readShuntVoltage();
  float p = ina228.readPower() / 1000.0f;
  if (isnan(i) || isnan(v) || isnan(s) || isnan(p) || v < 0 || v > 85 || fabs(i) > 20) {
    inaOk = false; setFault(FaultCode::CURRENT_INVALID, cfg.requireIna); return;
  }
  currentA = fabs(i); // absolute supply current for bidirectional actuator tests
  filteredCurrentA += (1.0f - clampf(cfg.currentFilter, 0, .99f)) * (currentA - filteredCurrentA);
  if (currentA > peakCurrentA) peakCurrentA = currentA;
  busVoltageV = v; shuntVoltageMv = s; powerW = p;
}

int16_t positionController(float target, float dt) {
  float error = target - feedbackPct;
  if (fabs(error) <= cfg.deadbandPct) {
    integral = 0; previousError = error; return 0; // no minimum PWM in deadband
  }
  float rawDerivative = (error - previousError) / dt;
  filteredDerivative = cfg.derivativeFilter * filteredDerivative +
                       (1.0f - cfg.derivativeFilter) * rawDerivative;
  float candidateIntegral = clampf(integral + error * dt, -cfg.integralLimit, cfg.integralLimit);
  float raw = cfg.kp * error + cfg.ki * candidateIntegral + cfg.kd * filteredDerivative;
  // Conditional integration: do not wind up farther into saturation.
  if (fabs(raw) < cfg.maxPwm || (raw > 0 && error < 0) || (raw < 0 && error > 0)) integral = candidateIntegral;
  raw = cfg.kp * error + cfg.ki * integral + cfg.kd * filteredDerivative;
  previousError = error;
  int16_t out = int16_t(clampf(raw, -cfg.maxPwm, cfg.maxPwm));
  if (out > 0 && out < cfg.minPwm) out = cfg.minPwm;
  if (out < 0 && out > -cfg.minPwm) out = -cfg.minPwm;
  return out;
}

int16_t applyOutputLimits(int16_t pwm, float dt) {
  lowerLimitActive = feedbackPct <= cfg.lowerLimitPct;
  upperLimitActive = feedbackPct >= cfg.upperLimitPct;
  if (pwm < 0 && lowerLimitActive) { setFault(FaultCode::LOWER_LIMIT, false); pwm = 0; }
  else if (pwm > 0 && upperLimitActive) { setFault(FaultCode::UPPER_LIMIT, false); pwm = 0; }
  else if (!faultLatched && (fault == FaultCode::LOWER_LIMIT || fault == FaultCode::UPPER_LIMIT)) clearTransientFault();

  // Reduce maximum output progressively near the endpoint being approached.
  float distance = pwm > 0 ? cfg.upperLimitPct - feedbackPct : feedbackPct - cfg.lowerLimitPct;
  if (pwm != 0 && cfg.slowdownZonePct > 0 && distance < cfg.slowdownZonePct) {
    float ratio = clampf(distance / cfg.slowdownZonePct, 0, 1);
    int16_t limit = cfg.nearLimitMaxPwm + int16_t((cfg.maxPwm - cfg.nearLimitMaxPwm) * ratio);
    pwm = constrain(pwm, -limit, limit);
  }

  // Progressive soft limit. It reduces allowed PWM; the INA228 is too slow for
  // cycle-by-cycle regulation. Hard overcurrent below is separately timed.
  softLimitActive = inaOk && filteredCurrentA > cfg.softCurrentA;
  if (softLimitActive) {
    float span = max(0.1f, cfg.hardCurrentA - cfg.softCurrentA);
    float scale = clampf(1.0f - (filteredCurrentA - cfg.softCurrentA) / span, 0.15f, 1.0f);
    pwm = int16_t(pwm * scale);
  }

  uint32_t nowMs = millis();
  int8_t desiredDirection = (pwm > 0) - (pwm < 0);
  int8_t appliedDirection = (appliedPwm > 0) - (appliedPwm < 0);
  if (desiredDirection && appliedDirection && desiredDirection != appliedDirection) {
    pendingDirection = desiredDirection; reversalUntilMs = nowMs + cfg.reversalDelayMs; pwm = 0;
  }
  if (reversalUntilMs && int32_t(nowMs - reversalUntilMs) < 0) pwm = 0;
  else reversalUntilMs = 0;

  if (cfg.pwmSlewPerSec > 0) {
    float maxDelta = cfg.pwmSlewPerSec * dt;
    int16_t delta = pwm - appliedPwm;
    if (delta > maxDelta) pwm = appliedPwm + int16_t(maxDelta + .5f);
    if (delta < -maxDelta) pwm = appliedPwm - int16_t(maxDelta + .5f);
  }
  return clampPwm(pwm);
}

void runSafety() {
  uint32_t now = millis();
  if (digitalRead(PIN_ESTOP) == LOW) setFault(FaultCode::EMERGENCY_STOP, true);
  if (enabled && elapsedMs(now, lastValidCommandMs, cfg.watchdogMs)) {
    setFault(FaultCode::WATCHDOG_TIMEOUT, false); stopAndDisable();
  }
  if (!feedbackValid && enabled) setFault(FaultCode::FEEDBACK_POT_INVALID, true);
  if (mode == Mode::FOLLOW && !commandValid && enabled) setFault(FaultCode::COMMAND_POT_INVALID, true);
  if (!inaOk && cfg.requireIna && enabled) setFault(FaultCode::INA_NOT_DETECTED, true);

  if (inaOk && currentA > cfg.hardCurrentA) {
    if (!overcurrentSinceMs) overcurrentSinceMs = now;
    if (elapsedMs(now, overcurrentSinceMs, cfg.hardCurrentDurationMs))
      setFault(FaultCode::HARD_OVERCURRENT, true);
  } else overcurrentSinceMs = 0;

  bool stallConditions = cfg.stallEnabled && abs(appliedPwm) >= cfg.stallPwmThreshold &&
                         filteredCurrentA >= cfg.stallCurrentA;
  if (stallConditions) {
    if (!stallSinceMs) { stallSinceMs = now; stallStartPosition = feedbackPct; }
    stallActive = elapsedMs(now, stallSinceMs, cfg.stallDurationMs);
    if (stallActive && fabs(feedbackPct - stallStartPosition) < cfg.stallMinMovementPct)
      setFault(FaultCode::STALL_DETECTED, true);
    else if (fabs(feedbackPct - stallStartPosition) >= cfg.stallMinMovementPct) {
      stallSinceMs = now; stallStartPosition = feedbackPct; stallActive = false;
    }
  } else { stallSinceMs = 0; stallActive = false; }
}

void runControl(float dt) {
  runSafety();
  if (!enabled || faultLatched || mode == Mode::DISABLED) { appliedPwm = 0; setMotorOutput(0); return; }
  switch (mode) {
    case Mode::MANUAL: requestedPwm = manualDirection * manualPwm; break;
    case Mode::PWM: case Mode::CURRENT_TEST: break; // requestedPwm set by CMD,PWM
    case Mode::POSITION: case Mode::STEP: requestedPwm = positionController(targetPct, dt); break;
    case Mode::FOLLOW:
      if (!commandValid) requestedPwm = 0;
      else { targetPct = clampf(commandPct, cfg.lowerLimitPct, cfg.upperLimitPct); requestedPwm = positionController(targetPct, dt); }
      break;
    default: requestedPwm = 0;
  }
  appliedPwm = applyOutputLimits(requestedPwm, dt);
  setMotorOutput(appliedPwm);
}

// Telemetry is serialized one field at a time only when at least 16 bytes fit
// in the hardware UART ring. This uses no large SRAM line buffer and cannot
// block control on UART throughput. Protocol replies wait until the line ends.
void buildTelemetry() {
  if (telemetryField >= 0 || rxReady || faultEventPending || configPrintIndex >= 0) return;
  telemetryPacketTimeMs = millis(); telemetryField = 0;
}

void pumpTelemetry() {
  if (telemetryField < 0 || Serial.availableForWrite() < 16) return;
  switch (telemetryField) {
    case 0: Serial.print(F("TEL,")); break;
    case 1: Serial.print(telemetryPacketTimeMs); Serial.print(','); break;
    case 2: Serial.print(modeText(mode)); Serial.print(','); break;
    case 3: Serial.print(targetPct,2); Serial.print(','); break;
    case 4: Serial.print(commandRaw); Serial.print(','); break;
    case 5: Serial.print(commandPct,2); Serial.print(','); break;
    case 6: Serial.print(feedbackRaw); Serial.print(','); break;
    case 7: Serial.print(feedbackPct,2); Serial.print(','); break;
    case 8: Serial.print(targetPct-feedbackPct,2); Serial.print(','); break;
    case 9: Serial.print(appliedPwm); Serial.print(','); break;
    case 10: Serial.print(currentA,3); Serial.print(','); break;
    case 11: Serial.print(filteredCurrentA,3); Serial.print(','); break;
    case 12: Serial.print(peakCurrentA,3); Serial.print(','); break;
    case 13: Serial.print(busVoltageV,2); Serial.print(','); break;
    case 14: Serial.print(shuntVoltageMv,3); Serial.print(','); break;
    case 15: Serial.print(powerW,2); Serial.print(','); break;
    case 16: Serial.print(uint8_t(fault)); Serial.print(','); break;
    case 17: Serial.print(faultLatched); Serial.print(','); break;
    case 18: Serial.print(fault==FaultCode::NONE ? 0 : millis()-faultSinceMs); Serial.print(','); break;
    case 19: Serial.print(digitalRead(PIN_ESTOP)==LOW); Serial.print(','); break;
    case 20: Serial.print(lowerLimitActive); Serial.print(','); break;
    case 21: Serial.print(upperLimitActive); Serial.print(','); break;
    case 22: Serial.print(inaOk); Serial.print(','); break;
    case 23: Serial.print(softLimitActive); Serial.print(','); break;
    case 24: Serial.print(stallActive); Serial.print(','); break;
    case 25: Serial.print(lastControlPeriodUs); Serial.print(','); break;
    case 26: Serial.print(lastTelemetryPeriodUs); Serial.print(','); break;
    case 27:
#ifdef __AVR__
      { extern int __heap_start, *__brkval; int stackTop;
        Serial.println(reinterpret_cast<int>(&stackTop)-(__brkval==0 ? reinterpret_cast<int>(&__heap_start) : reinterpret_cast<int>(__brkval))); }
#else
      Serial.println(-1);
#endif
      telemetryField = -2; break;
  }
  if (telemetryField >= 0) ++telemetryField;
  else if (telemetryField == -2) telemetryField = -1;
}

Mode parseMode(const char *s) {
  if (!strcasecmp(s,"MANUAL")) return Mode::MANUAL; if (!strcasecmp(s,"PWM")) return Mode::PWM;
  if (!strcasecmp(s,"POSITION")) return Mode::POSITION; if (!strcasecmp(s,"FOLLOW")) return Mode::FOLLOW;
  if (!strcasecmp(s,"STEP")) return Mode::STEP; if (!strcasecmp(s,"CURRENT_TEST")) return Mode::CURRENT_TEST;
  return Mode::DISABLED;
}

void ack(const char *a, const char *b = nullptr) { Serial.print(F("ACK,")); Serial.print(a); if (b) { Serial.print(','); Serial.print(b); } Serial.println(); }
void errorReply(const __FlashStringHelper *text) { Serial.print(F("ERR,INVALID_COMMAND,")); Serial.println(text); }

bool setParameter(const char *name, float value) {
#define SETF(n, field, lo, hi) if (!strcasecmp(name,n) && value >= lo && value <= hi) { cfg.field=value; return true; }
#define SETU(n, field, lo, hi) if (!strcasecmp(name,n) && value >= lo && value <= hi) { cfg.field=uint16_t(value); return true; }
#define SETB(n, field) if (!strcasecmp(name,n) && (value==0 || value==1)) { cfg.field=(value!=0); return true; }
  SETF("KP",kp,0,50) SETF("KI",ki,0,20) SETF("KD",kd,0,10)
  SETF("DEADBAND",deadbandPct,0.01,10) SETU("MIN_PWM",minPwm,0,255) SETU("MAX_PWM",maxPwm,1,255)
  SETF("PWM_SLEW",pwmSlewPerSec,0,10000) SETU("REVERSAL_MS",reversalDelayMs,0,2000)
  SETF("INTEGRAL_LIMIT",integralLimit,0,1000) SETF("DERIV_FILTER",derivativeFilter,0,.99)
  SETF("FEEDBACK_FILTER",feedbackFilter,0,.99) SETF("COMMAND_FILTER",commandFilter,0,.99)
  SETU("FB_MIN",feedbackMin,0,973) SETU("FB_MAX",feedbackMax,50,1023)
  SETU("CMD_MIN",commandMin,0,973) SETU("CMD_MAX",commandMax,50,1023)
  SETB("FB_INVERT",feedbackInvert) SETB("CMD_INVERT",commandInvert) SETB("MOTOR_INVERT",motorInvert)
  SETB("BRAKE_STOP",brakeStop) SETF("LOWER_LIMIT",lowerLimitPct,0,99) SETF("UPPER_LIMIT",upperLimitPct,1,100)
  SETF("SLOWDOWN_ZONE",slowdownZonePct,0,40) SETU("NEAR_LIMIT_PWM",nearLimitMaxPwm,0,255)
  SETF("CURRENT_WARN",currentWarningA,0,10) SETF("SOFT_CURRENT",softCurrentA,.1,10)
  SETF("HARD_CURRENT",hardCurrentA,.2,10.5) SETU("HARD_CURRENT_MS",hardCurrentDurationMs,10,5000)
  SETB("REQUIRE_INA",requireIna) SETF("CURRENT_FILTER",currentFilter,0,.99) SETB("STALL_ENABLE",stallEnabled)
  SETF("STALL_CURRENT",stallCurrentA,0,10) SETU("STALL_PWM",stallPwmThreshold,1,255)
  SETF("STALL_MOVEMENT",stallMinMovementPct,.01,20) SETU("STALL_MS",stallDurationMs,50,10000)
  SETU("CONTROL_HZ",controlHz,50,1000) SETU("POT_HZ",potHz,50,1000) SETU("CURRENT_HZ",currentHz,10,200)
  SETU("TELEMETRY_HZ",telemetryHz,1,100) SETU("WATCHDOG_MS",watchdogMs,200,5000)
#undef SETF
#undef SETU
#undef SETB
  return false;
}

void printConfigItem(const __FlashStringHelper *name, float value, uint8_t digits=3) { Serial.print(F("CFG,")); Serial.print(name); Serial.print(','); Serial.println(value,digits); }

// GET,CONFIG is streamed one short line per available UART slot. This avoids a
// burst of Serial.print calls blocking the scheduler for many milliseconds.
void pumpConfigLine() {
  if (configPrintIndex < 0 || Serial.availableForWrite() < 63) return;
#define CF(index, name, field) case index: printConfigItem(F(name), cfg.field); break
  switch (configPrintIndex) {
    CF(0,"KP",kp); CF(1,"KI",ki); CF(2,"KD",kd); CF(3,"DEADBAND",deadbandPct);
    CF(4,"MIN_PWM",minPwm); CF(5,"MAX_PWM",maxPwm); CF(6,"PWM_SLEW",pwmSlewPerSec);
    CF(7,"REVERSAL_MS",reversalDelayMs); CF(8,"INTEGRAL_LIMIT",integralLimit);
    CF(9,"DERIV_FILTER",derivativeFilter); CF(10,"FEEDBACK_FILTER",feedbackFilter);
    CF(11,"COMMAND_FILTER",commandFilter); CF(12,"FB_MIN",feedbackMin); CF(13,"FB_MAX",feedbackMax);
    CF(14,"CMD_MIN",commandMin); CF(15,"CMD_MAX",commandMax); CF(16,"FB_INVERT",feedbackInvert);
    CF(17,"CMD_INVERT",commandInvert); CF(18,"MOTOR_INVERT",motorInvert); CF(19,"BRAKE_STOP",brakeStop);
    CF(20,"LOWER_LIMIT",lowerLimitPct); CF(21,"UPPER_LIMIT",upperLimitPct);
    CF(22,"SLOWDOWN_ZONE",slowdownZonePct); CF(23,"NEAR_LIMIT_PWM",nearLimitMaxPwm);
    CF(24,"CURRENT_WARN",currentWarningA); CF(25,"SOFT_CURRENT",softCurrentA);
    CF(26,"HARD_CURRENT",hardCurrentA); CF(27,"HARD_CURRENT_MS",hardCurrentDurationMs);
    CF(28,"REQUIRE_INA",requireIna); CF(29,"CURRENT_FILTER",currentFilter);
    CF(30,"STALL_ENABLE",stallEnabled); CF(31,"STALL_CURRENT",stallCurrentA);
    CF(32,"STALL_PWM",stallPwmThreshold); CF(33,"STALL_MOVEMENT",stallMinMovementPct);
    CF(34,"STALL_MS",stallDurationMs); CF(35,"WATCHDOG_MS",watchdogMs);
    CF(36,"CONTROL_HZ",controlHz); CF(37,"POT_HZ",potHz); CF(38,"CURRENT_HZ",currentHz);
    CF(39,"TELEMETRY_HZ",telemetryHz);
    case 40: Serial.println(F("CFG,END")); break;
    default: ack("GET","CONFIG"); configPrintIndex = -2; break;
  }
#undef CF
  if (configPrintIndex >= 0) ++configPrintIndex;
  else if (configPrintIndex == -2) configPrintIndex = -1;
}

void handleCommand(char *line) {
  char *save = nullptr; char *head = strtok_r(line, ",", &save);
  if (!head) return;
  lastValidCommandMs = millis();
  if (!strcasecmp(head,"CMD")) {
    char *action = strtok_r(nullptr, ",", &save); if (!action) { errorReply(F("missing action")); return; }
    if (!strcasecmp(action,"HEARTBEAT")) { ack("HEARTBEAT"); return; }
    if (!strcasecmp(action,"STOP")) { stopAndDisable(); clearTransientFault(); ack("STOP"); return; }
    if (!strcasecmp(action,"ENABLE")) {
      if (digitalRead(PIN_ESTOP)==LOW) setFault(FaultCode::EMERGENCY_STOP,true);
      else if (!feedbackValid) setFault(FaultCode::FEEDBACK_POT_INVALID,true);
      else if (cfg.requireIna && !inaOk) setFault(FaultCode::INA_NOT_DETECTED,true);
      if (faultLatched) { errorReply(F("safety condition active")); return; }
      clearTransientFault(); enabled=true; mode=Mode::DISABLED; resetController(); ack("ENABLE"); return;
    }
    if (!strcasecmp(action,"RESET_FAULT")) {
      if (digitalRead(PIN_ESTOP)==LOW || (cfg.requireIna && !inaOk) || !feedbackValid) { errorReply(F("fault condition remains")); return; }
      fault=FaultCode::NONE; faultLatched=false; faultSinceMs=0; resetController(); ack("RESET_FAULT"); return;
    }
    if (!strcasecmp(action,"MODE")) {
      char *value=strtok_r(nullptr,",",&save); if(!value){errorReply(F("missing mode"));return;}
      if (strcasecmp(value,"DISABLED") && strcasecmp(value,"MANUAL") && strcasecmp(value,"PWM") &&
          strcasecmp(value,"POSITION") && strcasecmp(value,"FOLLOW") && strcasecmp(value,"STEP") &&
          strcasecmp(value,"CURRENT_TEST")) { errorReply(F("unknown mode")); return; }
      Mode next=parseMode(value); resetController(); mode=next;
      if(next==Mode::DISABLED) enabled=false; ack("MODE",value); return;
    }
    if (!strcasecmp(action,"PWM")) {
      char *value=strtok_r(nullptr,",",&save); if(!value){errorReply(F("missing PWM"));return;}
      requestedPwm=clampPwm(atoi(value)); ack("PWM",value); return;
    }
    if (!strcasecmp(action,"TARGET")) {
      char *value=strtok_r(nullptr,",",&save); if(!value){errorReply(F("missing target"));return;}
      targetPct=clampf(atof(value),cfg.lowerLimitPct,cfg.upperLimitPct); resetController(); ack("TARGET",value); return;
    }
    if (!strcasecmp(action,"MANUAL")) {
      char *dir=strtok_r(nullptr,",",&save), *pwm=strtok_r(nullptr,",",&save);
      if(!dir||!pwm){errorReply(F("MANUAL needs direction,pwm"));return;}
      manualDirection=constrain(atoi(dir),-1,1); manualPwm=constrain(atoi(pwm),0,255); ack("MANUAL"); return;
    }
    if (!strcasecmp(action,"CAL")) {
      char *sensor=strtok_r(nullptr,",",&save), *endpoint=strtok_r(nullptr,",",&save);
      if(!sensor||!endpoint){errorReply(F("CAL needs sensor,endpoint"));return;}
      Config candidate=cfg; uint16_t raw=!strcasecmp(sensor,"FB")?feedbackRaw:commandRaw;
      if(!strcasecmp(sensor,"FB")){if(!strcasecmp(endpoint,"MIN"))candidate.feedbackMin=raw;else candidate.feedbackMax=raw;}
      else if(!strcasecmp(sensor,"CMD")){if(!strcasecmp(endpoint,"MIN"))candidate.commandMin=raw;else candidate.commandMax=raw;}
      else {errorReply(F("unknown sensor"));return;}
      if(!configValid(candidate)){errorReply(F("calibration span too small"));return;} cfg=candidate; ack("CAL"); return;
    }
    if (!strcasecmp(action,"PEAK_RESET")) { peakCurrentA=currentA; ack("PEAK_RESET"); return; }
  } else if (!strcasecmp(head,"SET")) {
    char *name=strtok_r(nullptr,",",&save), *value=strtok_r(nullptr,",",&save);
    if(!name||!value){errorReply(F("SET needs name,value"));return;}
    Config before=cfg; if(!setParameter(name,atof(value))||!configValid(cfg)){cfg=before;errorReply(F("invalid parameter/value"));return;}
    resetController(); ack("SET",name); return;
  } else if (!strcasecmp(head,"GET")) {
    char *what=strtok_r(nullptr,",",&save); if(!what){errorReply(F("GET needs item"));return;}
    if(!strcasecmp(what,"CONFIG")){configPrintIndex=0;return;}
    if(!strcasecmp(what,"VERSION")){Serial.println(F("VER,ACTUATOR_TESTBENCH,1.0.0,PROTOCOL,1"));return;}
    if(!strcasecmp(what,"STATUS")){Serial.print(F("STATUS,"));Serial.print(enabled);Serial.print(',');Serial.print(modeText(mode));Serial.print(',');Serial.print(uint8_t(fault));Serial.print(',');Serial.println(faultLatched);return;}
  } else if (!strcasecmp(head,"SAVE")) {
    // EEPROM writes can take milliseconds. Stop before the explicit save so
    // control timing is never suspended while the bridge is active.
    stopAndDisable(); saveConfig(); ack("SAVE","CONFIG"); return;
  } else if (!strcasecmp(head,"LOAD")) {
    if(loadConfig()){stopAndDisable();ack("LOAD","CONFIG");}
    else errorReply(F("EEPROM data invalid"));
    return;
  } else if (!strcasecmp(head,"DEFAULTS")) {
    cfg=safeDefaults();stopAndDisable();ack("DEFAULTS","CONFIG");return;
  }
  setFault(FaultCode::INVALID_COMMAND,false); errorReply(F("unknown command"));
}

void receiveSerial() {
  if (rxReady) return; // preserve the complete line until it can be replied to
  while (Serial.available()) {
    char c=Serial.read();
    if(c=='\n') { rxLine[rxLength]=0; if(rxLength) rxReady=true; rxLength=0; return; }
    else if(c!='\r') {
      if(rxLength<SERIAL_RX_SIZE-1) rxLine[rxLength++]=c;
      else { rxLength=0; errorReply(F("line too long")); }
    }
  }
}

void pumpProtocolOutput() {
  if (telemetryField >= 0 || Serial.availableForWrite() < 63) return;
  if (faultEventPending) {
    Serial.print(F("EVT,FAULT,")); Serial.print(uint8_t(pendingFaultCode)); Serial.print(',');
    Serial.print(pendingFaultLatched ? 1 : 0); Serial.print(','); Serial.println(faultText(pendingFaultCode));
    faultEventPending = false; return;
  }
  if (configPrintIndex >= 0) { pumpConfigLine(); return; }
  if (rxReady) { rxReady=false; handleCommand(rxLine); }
}

void setup() {
  pinMode(PIN_RPWM,OUTPUT); pinMode(PIN_LPWM,OUTPUT); pinMode(PIN_R_EN,OUTPUT); pinMode(PIN_L_EN,OUTPUT);
  pinMode(PIN_ESTOP,INPUT_PULLUP); setMotorOutput(0);
  Serial.begin(SERIAL_BAUD); Wire.begin();
  cfg=safeDefaults(); loadConfig();
  inaOk=ina228.begin(INA228_ADDRESS,&Wire);
  if(inaOk) ina228.setShunt(0.015f,10.0f);
  samplePots(); feedbackPct=normalizePot(feedbackRaw,cfg.feedbackMin,cfg.feedbackMax,cfg.feedbackInvert);
  commandPct=normalizePot(commandRaw,cfg.commandMin,cfg.commandMax,cfg.commandInvert);
  targetPct=feedbackPct; lastValidCommandMs=millis();
  uint32_t now=micros(); lastPotUs=lastControlUs=lastCurrentUs=lastTelemetryUs=now;
  Serial.println(F("VER,ACTUATOR_TESTBENCH,1.0.0,PROTOCOL,1"));
}

void loop() {
  receiveSerial(); pumpTelemetry(); pumpProtocolOutput(); runSafety();
  uint32_t now=micros();
  if(elapsedUs(now,lastPotUs,1000000UL/cfg.potHz)){lastPotUs+=1000000UL/cfg.potHz;samplePots();}
  if(elapsedUs(now,lastCurrentUs,1000000UL/cfg.currentHz)){lastCurrentUs+=1000000UL/cfg.currentHz;sampleIna();}
  if(elapsedUs(now,lastControlUs,1000000UL/cfg.controlHz)){
    lastControlPeriodUs=now-lastControlUs;lastControlUs+=1000000UL/cfg.controlHz;runControl(1.0f/cfg.controlHz);
  }
  if(elapsedUs(now,lastTelemetryUs,1000000UL/cfg.telemetryHz)){
    lastTelemetryPeriodUs=now-lastTelemetryUs;lastTelemetryUs+=1000000UL/cfg.telemetryHz;buildTelemetry();
  }
}
