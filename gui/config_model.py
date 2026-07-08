"""Controller configuration model, field metadata, and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    minimum: float
    maximum: float
    unit: str
    tooltip: str
    decimals: int = 2
    group: str = "controller"


SPECS: dict[str, ParameterSpec] = {
    "KP": ParameterSpec(0, 50, "PWM/%", "Hoe sterk de motor reageert op positiefout. Hoger reageert sneller, maar kan oscillatie en stroompieken veroorzaken."),
    "KI": ParameterSpec(0, 20, "PWM/(%·s)", "Telt blijvende fout op en werkt die langzaam weg. Te hoog veroorzaakt doorschieten en langdurige correctie."),
    "KD": ParameterSpec(0, 10, "PWM·s/%", "Reageert op de snelheid waarmee de fout verandert en remt de beweging. Te hoog versterkt meetruis.", 3),
    "DEADBAND": ParameterSpec(.01, 10, "%", "Binnen deze afstand van het doel staat de motor uit. Groter voorkomt trillen, maar verlaagt de nauwkeurigheid."),
    "MIN_PWM": ParameterSpec(0, 255, "PWM", "Kleinste PWM zodra beweging nodig is. Helpt tegen wrijving; te hoog geeft schokkerige starts." ,0),
    "MAX_PWM": ParameterSpec(1, 255, "PWM", "Absolute bovengrens van de motoruitgang. Hoger kan sneller bewegen maar trekt meer stroom." ,0),
    "PWM_SLEW": ParameterSpec(0, 10000, "PWM/s", "Maximale verandering van PWM per seconde. Lager maakt starts en omkeren zachter; nul schakelt begrenzing uit.", 0),
    "REVERSAL_MS": ParameterSpec(0, 2000, "ms", "Tijd met nul PWM voordat de draairichting omkeert. Beschermt tegen een harde directe omkering.", 0),
    "INTEGRAL_LIMIT": ParameterSpec(0, 1000, "%·s", "Begrenst hoeveel fout de I-regelaar mag onthouden. Voorkomt langdurig doorschieten na verzadiging."),
    "DERIV_FILTER": ParameterSpec(0, .99, "factor", "Filtert het D-signaal: 0 is direct, richting 0,99 wordt rustiger maar trager.", 3),
    "FEEDBACK_FILTER": ParameterSpec(0, .99, "factor", "Maakt de gemeten actuatorpositie gladder. 0 is ongefilterd; hoog geeft meer vertraging.", 3),
    "COMMAND_FILTER": ParameterSpec(0, .99, "factor", "Maakt de commandopotmeter rustiger. 0 volgt direct; hoog onderdrukt hand- en contactruis.", 3),
    "SLOWDOWN_ZONE": ParameterSpec(0, 40, "%", "Afstand tot een software-eindgrens waarbinnen de maximale PWM geleidelijk wordt verlaagd."),
    "NEAR_LIMIT_PWM": ParameterSpec(0, 255, "PWM", "Maximale PWM vlak bij een software-eindgrens.", 0),
    "LOWER_LIMIT": ParameterSpec(0, 99, "%", "Onderste toegestane softwarepositie. Verder naar beneden bewegen wordt geblokkeerd."),
    "UPPER_LIMIT": ParameterSpec(1, 100, "%", "Bovenste toegestane softwarepositie. Verder naar boven bewegen wordt geblokkeerd."),
    "CONTROL_HZ": ParameterSpec(50, 1000, "Hz", "Hoe vaak per seconde PID en motoruitgang worden bijgewerkt. Standaard 500 Hz; alleen wijzigen als je timing begrijpt.", 0),
    "POT_HZ": ParameterSpec(50, 1000, "Hz", "Hoe vaak beide potmeters worden gemeten. Standaard gelijk aan de control-loop: 500 Hz.", 0),
    "FB_MIN": ParameterSpec(0, 973, "ADC", "Feedback raw value at physical minimum.", 0, "calibration"),
    "FB_MAX": ParameterSpec(50, 1023, "ADC", "Feedback raw value at physical maximum.", 0, "calibration"),
    "CMD_MIN": ParameterSpec(0, 973, "ADC", "Command-pot raw value at minimum.", 0, "calibration"),
    "CMD_MAX": ParameterSpec(50, 1023, "ADC", "Command-pot raw value at maximum.", 0, "calibration"),
    "FB_INVERT": ParameterSpec(0, 1, "bool", "Invert normalized feedback direction.", 0, "calibration"),
    "CMD_INVERT": ParameterSpec(0, 1, "bool", "Invert normalized command-pot direction.", 0, "calibration"),
    "MOTOR_INVERT": ParameterSpec(0, 1, "bool", "Swap positive and negative motor direction.", 0),
    "CURRENT_WARN": ParameterSpec(0, 10, "A", "Visual warning threshold.", 2, "current"),
    "SOFT_CURRENT": ParameterSpec(.1, 10, "A", "PWM is progressively reduced above this supply current.", 2, "current"),
    "HARD_CURRENT": ParameterSpec(.2, 10.5, "A", "Persistent current above this level latches a stop.", 2, "current"),
    "HARD_CURRENT_MS": ParameterSpec(10, 5000, "ms", "Qualification time for hard overcurrent.", 0, "current"),
    "STALL_CURRENT": ParameterSpec(0, 10, "A", "Minimum supply current for stall detection.", 2, "current"),
    "STALL_PWM": ParameterSpec(1, 255, "PWM", "Minimum command magnitude for stall detection.", 0, "current"),
    "STALL_MOVEMENT": ParameterSpec(.01, 20, "%", "Required movement during the stall time window.", 2, "current"),
    "STALL_MS": ParameterSpec(50, 10000, "ms", "Time before a no-movement stall is latched.", 0, "current"),
    "STALL_ENABLE": ParameterSpec(0, 1, "bool", "Enable the optional slow stall detector.", 0, "current"),
}


@dataclass(slots=True)
class ControllerConfig:
    values: dict[str, float] = field(default_factory=dict)

    @classmethod
    def defaults(cls) -> "ControllerConfig":
        path = Path(__file__).resolve().parents[1] / "configs" / "default_config.json"
        return cls({k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()})

    def update(self, name: str, value: float) -> None:
        name = name.upper()
        spec = SPECS.get(name)
        if spec and not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{name} must be between {spec.minimum:g} and {spec.maximum:g} {spec.unit}")
        candidate = dict(self.values)
        candidate[name] = float(value)
        self.validate(candidate)
        self.values = candidate

    @staticmethod
    def validate(values: dict[str, float]) -> None:
        for name, spec in SPECS.items():
            if name in values and not spec.minimum <= float(values[name]) <= spec.maximum:
                raise ValueError(f"{name} is outside {spec.minimum:g}..{spec.maximum:g}")
        if values.get("FB_MAX", 1023) - values.get("FB_MIN", 0) < 50:
            raise ValueError("Feedback calibration span must be at least 50 ADC counts")
        if values.get("CMD_MAX", 1023) - values.get("CMD_MIN", 0) < 50:
            raise ValueError("Command calibration span must be at least 50 ADC counts")
        if values.get("UPPER_LIMIT", 100) <= values.get("LOWER_LIMIT", 0) + 1:
            raise ValueError("Upper limit must exceed lower limit by more than 1%")
        if values.get("MAX_PWM", 255) < values.get("MIN_PWM", 0):
            raise ValueError("Maximum PWM must be at least minimum PWM")
        if values.get("NEAR_LIMIT_PWM", 0) > values.get("MAX_PWM", 255):
            raise ValueError("Near-limit PWM must not exceed maximum PWM")
        if values.get("HARD_CURRENT", 10) <= values.get("SOFT_CURRENT", 0):
            raise ValueError("Hard current limit must exceed soft current limit")

    def merge(self, values: dict[str, Any]) -> None:
        candidate = dict(self.values)
        candidate.update({str(k).upper(): float(v) for k, v in values.items()})
        self.validate(candidate)
        self.values = candidate

    def export_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.values, indent=2, sort_keys=True), encoding="utf-8")

    def import_json(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Configuration JSON must contain an object")
        self.merge(data)
