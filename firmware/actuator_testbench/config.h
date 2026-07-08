#pragma once

#include <Arduino.h>

// ---------------------------------------------------------------------------
// Pin defaults. Change these definitions to match the rig; pin numbers are not
// repeated elsewhere. On a classic Arduino Nano, SDA=A4 and SCL=A5 are fixed
// by Wire. D9 and D10 both support PWM.
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_FEEDBACK_POT = A0;
constexpr uint8_t PIN_COMMAND_POT  = A1;
constexpr uint8_t PIN_RPWM         = 9;
constexpr uint8_t PIN_LPWM         = 10;
constexpr uint8_t PIN_R_EN         = 7;
constexpr uint8_t PIN_L_EN         = 8;
constexpr uint8_t PIN_ESTOP        = 2;   // active LOW, INPUT_PULLUP
constexpr uint8_t INA228_ADDRESS   = 0x40;

constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint16_t CONFIG_VERSION = 3;
constexpr uint32_t CONFIG_MAGIC = 0x41544231UL; // "ATB1"
// All defined commands fit comfortably in 79 characters.
constexpr size_t SERIAL_RX_SIZE = 80;

enum class Mode : uint8_t {
  DISABLED, MANUAL, PWM, POSITION, FOLLOW, STEP, CURRENT_TEST
};

enum class FaultCode : uint8_t {
  NONE = 0, EMERGENCY_STOP = 1, WATCHDOG_TIMEOUT = 2,
  FEEDBACK_POT_INVALID = 3, COMMAND_POT_INVALID = 4,
  INA_NOT_DETECTED = 5, CURRENT_INVALID = 6, HARD_OVERCURRENT = 7,
  STALL_DETECTED = 8, LOWER_LIMIT = 9, UPPER_LIMIT = 10,
  INVALID_COMMAND = 11, INTERNAL_CONFIG = 12
};

struct Config {
  // Timing
  uint16_t controlHz;
  uint16_t potHz;
  uint16_t currentHz;
  uint16_t telemetryHz;
  uint16_t watchdogMs;

  // Controller values use position in percentage points and time in seconds.
  float kp;
  float ki;
  float kd;
  float deadbandPct;
  uint8_t minPwm;
  uint8_t maxPwm;
  float pwmSlewPerSec;       // 0 disables slew limiting
  uint16_t reversalDelayMs;  // zero-output pause before changing direction
  float integralLimit;
  float derivativeFilter;    // 0=no filtering, 0.95=heavy filtering
  float feedbackFilter;      // 0=unfiltered, 0.95=heavy filtering
  float commandFilter;

  // Calibration and travel limits.
  uint16_t feedbackMin;
  uint16_t feedbackMax;
  uint16_t commandMin;
  uint16_t commandMax;
  bool feedbackInvert;
  bool commandInvert;
  bool motorInvert;
  bool brakeStop;             // board-dependent; false disables both EN pins
  float lowerLimitPct;
  float upperLimitPct;
  float slowdownZonePct;
  uint8_t nearLimitMaxPwm;

  // Supply-current protection. INA228 is upstream of the PWM bridge, so this
  // is supply current, not instantaneous motor winding/recirculation current.
  float currentWarningA;
  float softCurrentA;
  float hardCurrentA;
  uint16_t hardCurrentDurationMs;
  bool requireIna;
  float currentFilter;

  // Optional slow stall detector; intentionally disabled by default.
  bool stallEnabled;
  float stallCurrentA;
  uint8_t stallPwmThreshold;
  float stallMinMovementPct;
  uint16_t stallDurationMs;
};

inline Config safeDefaults() {
  Config c{};
  c.controlHz = 500; c.potHz = 500; c.currentHz = 100;
  c.telemetryHz = 50; c.watchdogMs = 750;
  c.kp = 4.0f; c.ki = 0.0f; c.kd = 0.08f; c.deadbandPct = 0.5f;
  c.minPwm = 55; c.maxPwm = 220; c.pwmSlewPerSec = 900.0f;
  c.reversalDelayMs = 80; c.integralLimit = 40.0f;
  c.derivativeFilter = 0.75f; c.feedbackFilter = 0.25f; c.commandFilter = 0.2f;
  c.feedbackMin = 80; c.feedbackMax = 940;
  c.commandMin = 80; c.commandMax = 940;
  c.feedbackInvert = false; c.commandInvert = false; c.motorInvert = false;
  c.brakeStop = false; c.lowerLimitPct = 2.0f; c.upperLimitPct = 98.0f;
  c.slowdownZonePct = 8.0f; c.nearLimitMaxPwm = 120;
  c.currentWarningA = 5.0f; c.softCurrentA = 7.0f; c.hardCurrentA = 9.0f;
  c.hardCurrentDurationMs = 100; c.requireIna = true; c.currentFilter = 0.3f;
  c.stallEnabled = false; c.stallCurrentA = 5.0f;
  c.stallPwmThreshold = 100; c.stallMinMovementPct = 0.25f;
  c.stallDurationMs = 800;
  return c;
}
