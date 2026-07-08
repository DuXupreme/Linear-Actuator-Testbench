"""Main PySide6 window for the actuator laboratory testbench."""
from __future__ import annotations

from collections import deque
import csv
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import GUI_VERSION
from .config_model import ControllerConfig, SPECS
from .comparison_sequence import (
    PROFILE_PARAMETERS, ComparisonSettings, ControllerComparisonSequence,
    ControllerProfile, ProfileResult, RunMetrics,
)
from .graph_manager import GraphPanel
from .logger import TelemetryLogger
from .log_report import save_log_graphs
from .serial_worker import DataWorker, SerialWorker, SimulationWorker, available_ports
from .telemetry import Telemetry
from .test_sequence import StepSequence, StepSettings
from .widgets.value_label import ValueLabel
from .widgets.controller_parameter import ControllerResponsePreview, ParameterCard


WARNING = (
    "Laboratory test software. Keep an accessible physical emergency stop and "
    "current-limited power supply. Do not test unattended."
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Actuator Testbench {GUI_VERSION}")
        self.resize(1500, 960)
        self.worker: DataWorker | None = None
        self.latest: Telemetry | None = None
        self.config = ControllerConfig.defaults()
        self.logger = TelemetryLogger()
        self.packet_times: deque[float] = deque(maxlen=300)
        self.rx_packets = self.tx_packets = self.invalid_packets = 0
        self.console_paused = False
        self.parameter_fields: dict[str, QDoubleSpinBox] = {}
        self.parameter_sliders: dict[str, QSlider] = {}
        self.parameter_cards: dict[str, ParameterCard] = {}
        self._building_fields = False
        self.step_sequence = StepSequence(self.send)
        self.step_sequence.status_changed.connect(self._step_status)
        self.step_sequence.finished.connect(self._step_finished)
        self.step_sequence.aborted.connect(lambda why: self._show_status(f"Step test aborted: {why}", True))
        self.comparison_sequence = ControllerComparisonSequence(self.send)
        self.comparison_sequence.status_changed.connect(self._comparison_status)
        self.comparison_sequence.progress_changed.connect(self._comparison_progress)
        self.comparison_sequence.run_finished.connect(self._comparison_run_finished)
        self.comparison_sequence.finished.connect(self._comparison_finished)
        self.comparison_sequence.aborted.connect(self._comparison_aborted)
        self.comparison_results: list[ProfileResult] = []
        self.comparison_test_profiles: list[ControllerProfile] = []
        self.comparison_log_owned = False
        self._build_ui()
        self._style()
        self.refresh_ports()
        self.rate_timer = QTimer(self); self.rate_timer.timeout.connect(self._update_connection_stats); self.rate_timer.start(250)
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut); shortcut.activated.connect(self.emergency_stop)

    def _build_ui(self) -> None:
        central = QWidget(); root = QVBoxLayout(central); self.setCentralWidget(central)
        warning = QLabel(WARNING); warning.setWordWrap(True); warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warning.setStyleSheet("background:#662020;color:white;font-weight:700;padding:8px;border:2px solid #e05252")
        root.addWidget(warning)
        root.addWidget(self._connection_panel())
        root.addLayout(self._safety_bar())
        self.active_mode = QLabel("ACTIVE MODE: DISABLED"); self.active_mode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.active_mode.setStyleSheet("font-size:18px;font-weight:700;background:#263241;padding:7px")
        root.addWidget(self.active_mode)
        self.fault_banner = QLabel("FAULT 0: No fault | Latched: no | Age: 0 ms")
        self.fault_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fault_banner.setStyleSheet("font-size:16px;font-weight:700;background:#17392f;color:#8ff0c8;padding:6px")
        root.addWidget(self.fault_banner)
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._scroll(self._control_tab()), "Control")
        self.tabs.addTab(self._scroll(self._tuning_tab()), "Controller tuning")
        self.tabs.addTab(self._scroll(self._calibration_tab()), "Sensor calibration")
        self.tabs.addTab(self._scroll(self._current_tab()), "INA228 / protection")
        self.graphs = GraphPanel(); self.tabs.addTab(self.graphs, "Live graphs")
        self.tabs.addTab(self._scroll(self._step_tab()), "Step response")
        self.tabs.addTab(self._scroll(self._comparison_tab()), "Controller comparison")
        self.tabs.addTab(self._diagnostics_tab(), "Diagnostics / console")
        self.statusBar().showMessage("Disconnected — outputs are not enabled")

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(widget); return area

    def _connection_panel(self) -> QGroupBox:
        box = QGroupBox("Connection"); grid = QGridLayout(box)
        self.port_combo = QComboBox(); self.port_combo.setMinimumWidth(210)
        refresh = QPushButton("Refresh ports"); refresh.clicked.connect(self.refresh_ports)
        self.baud = QSpinBox(); self.baud.setRange(1200, 2000000); self.baud.setValue(115200)
        self.simulation = QCheckBox("Simulation mode (no hardware)")
        connect = QPushButton("Connect"); connect.clicked.connect(self.connect_source)
        disconnect = QPushButton("Disconnect"); disconnect.clicked.connect(self.disconnect_source)
        self.connection_status = QLabel("● Disconnected"); self.connection_status.setStyleSheet("color:#ef5350;font-weight:700")
        self.firmware = QLabel("—"); self.last_telemetry = QLabel("—"); self.packet_rate = QLabel("0.0 Hz"); self.invalid_count = QLabel("0")
        labels = [QLabel("COM port"), self.port_combo, refresh, QLabel("Baud"), self.baud,
                  self.simulation, connect, disconnect, self.connection_status,
                  QLabel("Firmware"), self.firmware, QLabel("Last telemetry"), self.last_telemetry,
                  QLabel("Packet rate"), self.packet_rate, QLabel("Invalid"), self.invalid_count]
        for i, widget in enumerate(labels): grid.addWidget(widget, i//9, i%9)
        return box

    def _safety_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        stop = QPushButton("STOP  [SPACE]"); stop.setObjectName("stopButton"); stop.setMinimumHeight(62); stop.clicked.connect(self.emergency_stop)
        enable = QPushButton("ENABLE CONTROL"); enable.setMinimumHeight(52); enable.clicked.connect(lambda: self.send("CMD,ENABLE"))
        reset = QPushButton("RESET FAULT"); reset.clicked.connect(lambda: self.send("CMD,RESET_FAULT"))
        clear = QPushButton("CLEAR GRAPHS"); clear.clicked.connect(lambda: self.graphs.clear())
        self.start_log_button = QPushButton("START LOGGING"); self.start_log_button.clicked.connect(self.start_logging)
        self.stop_log_button = QPushButton("STOP LOGGING"); self.stop_log_button.clicked.connect(self.stop_logging); self.stop_log_button.setEnabled(False)
        for widget in (stop, enable, reset, clear, self.start_log_button, self.stop_log_button): row.addWidget(widget)
        return row

    def _control_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        mode_box = QGroupBox("Operating mode — changing mode commands zero first"); modes = QGridLayout(mode_box)
        for i, (label, value) in enumerate([
            ("Disabled", "DISABLED"), ("Manual direction", "MANUAL"), ("Direct PWM", "PWM"),
            ("Position target", "POSITION"), ("Follow potentiometer", "FOLLOW"),
            ("Step-response test", "STEP"), ("Current / stall test", "CURRENT_TEST")]):
            button = QPushButton(label); button.setMinimumHeight(40); button.clicked.connect(lambda _=False, m=value: self.change_mode(m)); modes.addWidget(button, i//4, i%4)
        layout.addWidget(mode_box)
        readings = QGroupBox("Live values"); row = QGridLayout(readings)
        self.main_values: dict[str, ValueLabel] = {}
        for col, (key, title, color) in enumerate([
            ("position","Position %","#06d6a0"),("error","Error %","#ef476f"),("pwm","PWM","#4cc9f0"),
            ("current","Current A","#ffb703"),("voltage","Bus V","#80ed99"),("fault","Fault","#ff6b6b")]):
            row.addWidget(QLabel(title),0,col); value=ValueLabel("—",color); self.main_values[key]=value; row.addWidget(value,1,col)
        layout.addWidget(readings)
        columns = QHBoxLayout(); columns.addWidget(self._manual_box()); columns.addWidget(self._target_box()); columns.addWidget(self._follow_box()); layout.addLayout(columns)
        layout.addStretch(); return page

    def _manual_box(self) -> QGroupBox:
        box=QGroupBox("Manual and direct PWM"); form=QVBoxLayout(box)
        pwmrow=QHBoxLayout(); self.manual_pwm=QSpinBox(); self.manual_pwm.setRange(0,255); self.manual_pwm.setValue(100)
        self.manual_slider=QSlider(Qt.Orientation.Horizontal); self.manual_slider.setRange(0,255); self.manual_slider.setValue(100)
        self.manual_slider.valueChanged.connect(self.manual_pwm.setValue); self.manual_pwm.valueChanged.connect(self.manual_slider.setValue)
        pwmrow.addWidget(QLabel("PWM")); pwmrow.addWidget(self.manual_slider,1); pwmrow.addWidget(self.manual_pwm); form.addLayout(pwmrow)
        buttons=QHBoxLayout(); self.extend=QPushButton("EXTEND"); self.retract=QPushButton("RETRACT"); stop=QPushButton("Stop")
        self.extend.pressed.connect(lambda:self.manual_press(1)); self.extend.released.connect(self.manual_release)
        self.retract.pressed.connect(lambda:self.manual_press(-1)); self.retract.released.connect(self.manual_release); stop.clicked.connect(lambda:self.send("CMD,MANUAL,0,0"))
        for b in (self.extend,self.retract,stop): buttons.addWidget(b)
        form.addLayout(buttons); self.hold_to_run=QCheckBox("Hold-to-run (recommended)"); self.hold_to_run.setChecked(True); form.addWidget(self.hold_to_run)
        jogrow=QHBoxLayout(); self.jog_ms=QSpinBox(); self.jog_ms.setRange(0,10000); self.jog_ms.setSuffix(" ms"); self.jog_ms.setSpecialValueText("continuous")
        jogrow.addWidget(QLabel("Optional jog duration")); jogrow.addWidget(self.jog_ms); form.addLayout(jogrow)
        self.motor_invert=QCheckBox("Motor direction inverted"); self.motor_invert.toggled.connect(lambda v:self.set_one("MOTOR_INVERT",int(v))); form.addWidget(self.motor_invert)
        direct=QHBoxLayout(); self.direct_pwm=QSpinBox(); self.direct_pwm.setRange(-255,255); direct_send=QPushButton("Send signed PWM"); direct_send.clicked.connect(lambda:self.send(f"CMD,PWM,{self.direct_pwm.value()}"))
        direct.addWidget(self.direct_pwm); direct.addWidget(direct_send); form.addLayout(direct); return box

    def _target_box(self) -> QGroupBox:
        box=QGroupBox("Position target"); form=QVBoxLayout(box)
        self.target_slider=QSlider(Qt.Orientation.Horizontal); self.target_slider.setRange(0,1000); self.target_slider.setValue(500)
        self.target_spin=QDoubleSpinBox(); self.target_spin.setRange(0,100); self.target_spin.setDecimals(2); self.target_spin.setSuffix(" %"); self.target_spin.setValue(50)
        self.target_slider.valueChanged.connect(lambda v:self.target_spin.setValue(v/10)); self.target_spin.valueChanged.connect(lambda v:self.target_slider.setValue(round(v*10)))
        form.addWidget(self.target_slider); form.addWidget(self.target_spin); send=QPushButton("Send target"); send.clicked.connect(self.send_target); form.addWidget(send)
        self.target_info=QLabel("Target —   Actual —   Error —"); self.target_info.setStyleSheet("font-size:16px;font-weight:600"); form.addWidget(self.target_info); return box

    def _follow_box(self) -> QGroupBox:
        box=QGroupBox("Follow potentiometer"); form=QVBoxLayout(box)
        self.follow_info=QLabel("Command —\nFeedback —\nError —\nPWM —\nMode —"); self.follow_info.setStyleSheet("font-size:16px"); form.addWidget(self.follow_info)
        buttons=QHBoxLayout(); start=QPushButton("Start follow"); start.clicked.connect(lambda:self.change_mode("FOLLOW")); stop=QPushButton("Stop follow"); stop.clicked.connect(self.emergency_stop); buttons.addWidget(start);buttons.addWidget(stop);form.addLayout(buttons)
        self.cmd_invert=QCheckBox("Invert command pot"); self.fb_invert=QCheckBox("Invert feedback")
        self.cmd_invert.toggled.connect(lambda v:self.set_one("CMD_INVERT",int(v))); self.fb_invert.toggled.connect(lambda v:self.set_one("FB_INVERT",int(v)))
        form.addWidget(self.cmd_invert); form.addWidget(self.fb_invert)
        self.command_filter_enable=QCheckBox("Enable command filtering"); self.command_filter_enable.setChecked(True); self.command_filter_enable.toggled.connect(lambda v:self.set_one("COMMAND_FILTER",.2 if v else 0)); form.addWidget(self.command_filter_enable)
        presets=QHBoxLayout()
        for name,kp,kd,slew in [("Gentle",2.0,.12,400),("Balanced",4.0,.08,900),("Responsive",7.0,.05,1800)]:
            button=QPushButton(name);button.clicked.connect(lambda _=False,a=kp,b=kd,c=slew:self.apply_preset(a,b,c));presets.addWidget(button)
        form.addLayout(presets); return box

    def _tuning_tab(self) -> QWidget:
        page=QWidget(); layout=QVBoxLayout(page)
        intro=QLabel("Visueel tunen · beweeg een slider om de invloed direct te bekijken. De waarde wordt pas naar de Nano gestuurd wanneer je de slider loslaat of Enter drukt. Houd de muis boven een kaart of invoerveld voor extra uitleg.")
        intro.setWordWrap(True);intro.setStyleSheet("font-size:14px;font-weight:600;background:#243447;padding:11px;border-left:4px solid #4cc9f0");layout.addWidget(intro)
        self.controller_preview=ControllerResponsePreview(self.config.values);layout.addWidget(self.controller_preview)
        order=QLabel("Veilige tuningvolgorde:  1  kalibratie & limieten  →  2  MAX/MIN PWM & slew  →  3  Kp  →  4  deadband  →  5  Kd  →  6  alleen indien nodig Ki")
        order.setWordWrap(True);order.setStyleSheet("color:#f2d18a;background:#2a2519;padding:9px;font-weight:600");layout.addWidget(order)
        preset_row=QHBoxLayout();preset_row.addWidget(QLabel("Startprofielen:"))
        for name,kp,kd,slew in [("Rustig",2.0,.12,400),("Gebalanceerd",4.0,.08,900),("Direct",7.0,.05,1800)]:
            button=QPushButton(name);button.setToolTip("Past alleen zichtbare waarden Kp, Kd en PWM-slew aan; er zijn geen verborgen instellingen.");button.clicked.connect(lambda _=False,a=kp,b=kd,c=slew:self.apply_preset(a,b,c));preset_row.addWidget(button)
        preset_row.addStretch();layout.addLayout(preset_row)
        groups=[
            ("1. PID-respons",["KP","KI","KD","INTEGRAL_LIMIT","DERIV_FILTER"]),
            ("2. Motoruitgang en rust rond het doel",["DEADBAND","MIN_PWM","MAX_PWM","PWM_SLEW","REVERSAL_MS"]),
            ("3. Sensor- en commandofiltering",["FEEDBACK_FILTER","COMMAND_FILTER"]),
            ("4. Software-eindgrenzen",["LOWER_LIMIT","UPPER_LIMIT","SLOWDOWN_ZONE","NEAR_LIMIT_PWM"]),
            ("5. Geavanceerde looptiming",["CONTROL_HZ","POT_HZ"]),
        ]
        self._building_fields=True
        for group_title,names in groups:
            box=QGroupBox(group_title);grid=QGridLayout(box);grid.setHorizontalSpacing(12);grid.setVerticalSpacing(10)
            for index,name in enumerate(names):
                spec=SPECS[name];card=ParameterCard(name,spec,self.config.values.get(name,spec.minimum));card.valuePreviewed.connect(self._preview_controller_parameter);card.valueCommitted.connect(self.set_one)
                self.parameter_cards[name]=card;self.parameter_fields[name]=card.spin;self.parameter_sliders[name]=card.slider;grid.addWidget(card,index//2,index%2)
            layout.addWidget(box)
        self._building_fields=False
        buttons=QHBoxLayout()
        for label,slot in [("Apply all",self.apply_all),("Read from Arduino",lambda:self.send("GET,CONFIG")),("Save to EEPROM",lambda:self.send("SAVE,CONFIG")),("Load from EEPROM",lambda:self.send("LOAD,CONFIG")),("Restore safe defaults",self.restore_defaults),("Export JSON",self.export_config),("Import JSON",self.import_config)]:
            b=QPushButton(label);b.clicked.connect(slot);buttons.addWidget(b)
        layout.addLayout(buttons); layout.addStretch(); return page

    def _preview_controller_parameter(self,name:str,value:float)->None:
        self.controller_preview.set_parameter(name,value)

    def _calibration_tab(self) -> QWidget:
        page=QWidget();layout=QVBoxLayout(page)
        instructions=QLabel("1. Move the sensor to the physical minimum.  2. Capture minimum.  3. Move to physical maximum.  4. Capture maximum.  5. Verify direction.  6. Save configuration.\nThe application never moves the actuator during calibration.")
        instructions.setWordWrap(True);instructions.setStyleSheet("font-weight:600;background:#243447;padding:10px");layout.addWidget(instructions)
        holders=QHBoxLayout(); self.cal_widgets={}
        for sensor,title,prefix in [("FB","Actuator feedback","FB"),("CMD","Command potentiometer","CMD")]:
            box=QGroupBox(title);form=QFormLayout(box);raw=ValueLabel("—");pct=ValueLabel("—");minimum=QSpinBox();minimum.setRange(0,973);maximum=QSpinBox();maximum.setRange(50,1023);invert=QCheckBox("Invert normalized direction");status=QLabel("Awaiting readings")
            capmin=QPushButton("Capture current as minimum");capmax=QPushButton("Capture current as maximum")
            capmin.clicked.connect(lambda _=False,s=sensor:self.capture_cal(s,"MIN"));capmax.clicked.connect(lambda _=False,s=sensor:self.capture_cal(s,"MAX"))
            minimum.editingFinished.connect(lambda p=prefix,w=minimum:self.set_one(f"{p}_MIN",w.value()));maximum.editingFinished.connect(lambda p=prefix,w=maximum:self.set_one(f"{p}_MAX",w.value()));invert.toggled.connect(lambda v,p=prefix:self.set_one(f"{p}_INVERT",int(v)))
            form.addRow("Raw ADC",raw);form.addRow("Normalized",pct);form.addRow(capmin);form.addRow(capmax);form.addRow("Manual minimum",minimum);form.addRow("Manual maximum",maximum);form.addRow(invert);form.addRow("Status",status);holders.addWidget(box)
            self.cal_widgets[sensor]={"raw":raw,"pct":pct,"min":minimum,"max":maximum,"invert":invert,"status":status}
        layout.addLayout(holders);save=QPushButton("Save calibrated configuration to EEPROM");save.clicked.connect(lambda:self.send("SAVE,CONFIG"));layout.addWidget(save);layout.addStretch();return page

    def _current_tab(self) -> QWidget:
        page=QWidget();layout=QVBoxLayout(page);live=QGroupBox("INA228 live values (high-side supply measurements)");grid=QGridLayout(live);self.ina_values={}
        for i,(key,label,unit) in enumerate([("current","Instantaneous current","A"),("filtered","Filtered current","A"),("peak","Peak current","A"),("voltage","Bus voltage","V"),("shunt","Shunt voltage","mV"),("power","Power","W")]):
            grid.addWidget(QLabel(label),0,i);v=ValueLabel("—");self.ina_values[key]=v;grid.addWidget(v,1,i)
        self.ina_status=QLabel("INA228: — | Warning: — | Soft limit: — | Hard limit: — | Stall: — | Travel limit: —");self.ina_status.setStyleSheet("font-size:17px;font-weight:700");grid.addWidget(self.ina_status,2,0,1,6);layout.addWidget(live)
        note=QLabel("The INA228 is before the H-bridge: displayed current is supply current, not necessarily instantaneous motor-winding current during PWM recirculation. Limits are time-qualified protection, not cycle-by-cycle regulation.");note.setWordWrap(True);layout.addWidget(note)
        settings=QGroupBox("Current and stall settings");form=QFormLayout(settings);self.current_fields={}
        for name in ["CURRENT_WARN","SOFT_CURRENT","HARD_CURRENT","HARD_CURRENT_MS","STALL_CURRENT","STALL_PWM","STALL_MOVEMENT","STALL_MS"]:
            spec=SPECS[name];w=QDoubleSpinBox();w.setRange(spec.minimum,spec.maximum);w.setDecimals(spec.decimals);w.setValue(self.config.values[name]);w.setSuffix(" "+spec.unit);w.setToolTip(spec.tooltip);w.setKeyboardTracking(False);w.editingFinished.connect(lambda n=name,x=w:self.set_one(n,x.value()));self.current_fields[name]=w;form.addRow(name,w)
        self.stall_enable=QCheckBox("Enable stall detection (disabled by default)");self.stall_enable.toggled.connect(lambda v:self.set_one("STALL_ENABLE",int(v)));form.addRow(self.stall_enable)
        resetpeak=QPushButton("Reset peak current");resetpeak.clicked.connect(lambda:self.send("CMD,PEAK_RESET"));form.addRow(resetpeak);layout.addWidget(settings);layout.addStretch();return page

    def _step_tab(self) -> QWidget:
        page=QWidget();layout=QHBoxLayout(page);setup=QGroupBox("Step sequence");form=QFormLayout(setup);self.step_fields={}
        for key,label,lo,hi,value,suffix,dec in [("start","Start position",0,100,25," %",2),("end","End position",0,100,75," %",2),("delay","Delay / baseline",0,30,1," s",2),("hold","Hold time",.1,120,3," s",2),("reps","Repetitions",1,100,1,"",0),("max_current","Maximum allowed current",.1,10,8," A",2),("duration","Maximum test duration",1,3600,30," s",1),("tolerance","Position tolerance",.05,10,1," %",2)]:
            w=QDoubleSpinBox();w.setRange(lo,hi);w.setValue(value);w.setDecimals(dec);w.setSuffix(suffix);self.step_fields[key]=w;form.addRow(label,w)
        self.return_start=QCheckBox("Return to start after each step");self.return_start.setChecked(True);form.addRow(self.return_start)
        start=QPushButton("START STEP TEST");start.clicked.connect(self.start_step_test);abort=QPushButton("ABORT TEST / STOP");abort.setObjectName("stopButton");abort.clicked.connect(lambda:self.step_sequence.abort("Operator abort"));form.addRow(start);form.addRow(abort);layout.addWidget(setup)
        result=QGroupBox("Status and calculated results (raw telemetry remains in graphs/log)");v=QVBoxLayout(result);self.step_status=QLabel("IDLE");self.step_status.setStyleSheet("font-size:22px;font-weight:700");self.step_results=QPlainTextEdit();self.step_results.setReadOnly(True);v.addWidget(self.step_status);v.addWidget(self.step_results);layout.addWidget(result,1);return page

    def _comparison_tab(self) -> QWidget:
        page=QWidget();layout=QVBoxLayout(page)
        intro=QLabel(
            "Vergelijk regelprofielen automatisch met dezelfde herhaalde positiestap. "
            "De externe feedbackpotmeter bepaalt positie, snelheid en acceleratie. "
            "INA228-stroom is een inspannings-/energie-indicator en geen directe torque- of krachtmeting."
        )
        intro.setWordWrap(True);intro.setStyleSheet("font-size:14px;font-weight:600;background:#243447;padding:11px;border-left:4px solid #4cc9f0");layout.addWidget(intro)

        profiles=QGroupBox("1. Controllerprofielen — vink alleen profielen aan die veilig getest mogen worden");profile_layout=QVBoxLayout(profiles)
        self.comparison_profile_keys=("USE","NAME")+PROFILE_PARAMETERS
        labels=("Test","Naam","Kp","Ki","Kd","Deadband %","Min PWM","Max PWM","Slew PWM/s","D-filter","Feedback-filter")
        self.comparison_profiles=QTableWidget(0,len(labels));self.comparison_profiles.setHorizontalHeaderLabels(labels)
        self.comparison_profiles.setAlternatingRowColors(True);self.comparison_profiles.verticalHeader().setVisible(False)
        header=self.comparison_profiles.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.comparison_profiles.setMinimumHeight(190);profile_layout.addWidget(self.comparison_profiles)
        profile_buttons=QHBoxLayout()
        for label,slot in [
            ("Voeg huidige instellingen toe",self._add_current_comparison_profile),
            ("Dupliceer geselecteerde",self._duplicate_comparison_profile),
            ("Verwijder geselecteerde",self._remove_comparison_profile),
        ]:
            button=QPushButton(label);button.clicked.connect(slot);profile_buttons.addWidget(button)
        self.comparison_sweep_parameter=QComboBox();self.comparison_sweep_parameter.addItems(["KP","KD","MAX_PWM","PWM_SLEW","MIN_PWM","DEADBAND"])
        self.comparison_sweep_spread=QDoubleSpinBox();self.comparison_sweep_spread.setRange(5,80);self.comparison_sweep_spread.setValue(25);self.comparison_sweep_spread.setSuffix(" % spreiding")
        sweep=QPushButton("Maak laag / huidig / hoog sweep");sweep.setToolTip("Vervangt de tabel door drie profielen rond de huidige waarde van de gekozen parameter.");sweep.clicked.connect(self._generate_comparison_sweep)
        profile_buttons.addSpacing(18);profile_buttons.addWidget(self.comparison_sweep_parameter);profile_buttons.addWidget(self.comparison_sweep_spread);profile_buttons.addWidget(sweep);profile_buttons.addStretch();profile_layout.addLayout(profile_buttons);layout.addWidget(profiles)
        self._insert_comparison_profile("Huidige instellingen",self.config.values,True)

        middle=QHBoxLayout();settings_box=QGroupBox("2. Veilige testinstellingen");form=QFormLayout(settings_box);self.comparison_fields={}
        definitions=[
            ("start","Startpositie",0,100,25," %",2),("end","Eindpositie",0,100,75," %",2),
            ("baseline","Baseline per run",0.2,10,1," s",2),("hold","Meet-/houdtijd",0.5,30,4," s",2),
            ("reps","Herhalingen per profiel",1,20,3,"",0),("tolerance","Settling-tolerantie",.05,10,1," %",2),
            ("max_current","Teststroom-abort",.1,10,8," A",2),("move_timeout","Max. beweegtijd",2,120,20," s",1),
        ]
        for key,label,minimum,maximum,value,suffix,decimals in definitions:
            field=QDoubleSpinBox();field.setRange(minimum,maximum);field.setDecimals(decimals);field.setValue(value);field.setSuffix(suffix);field.setKeyboardTracking(False);self.comparison_fields[key]=field;form.addRow(label,field)
        self.comparison_auto_log=QCheckBox("Automatisch loggen en na afloop bewaren/verwijderen vragen");self.comparison_auto_log.setChecked(True);form.addRow(self.comparison_auto_log)
        middle.addWidget(settings_box)

        control=QGroupBox("3. Uitvoeren");control_layout=QVBoxLayout(control)
        self.comparison_status=QLabel("IDLE");self.comparison_status.setStyleSheet("font-size:21px;font-weight:700");self.comparison_status.setWordWrap(True);control_layout.addWidget(self.comparison_status)
        self.comparison_progress=QProgressBar();self.comparison_progress.setRange(0,1);self.comparison_progress.setValue(0);self.comparison_progress.setFormat("0 / 0 runs");control_layout.addWidget(self.comparison_progress)
        score_help=QLabel("Rangscore (lager is beter): 45% settling, 25% rise time, 15% overshoot, 10% piekstroom en 5% energie. Resultaten zijn relatief binnen deze testset.");score_help.setWordWrap(True);score_help.setStyleSheet("color:#b9cce0");control_layout.addWidget(score_help)
        start=QPushButton("START CONTROLLER COMPARISON");start.setMinimumHeight(48);start.clicked.connect(self.start_controller_comparison);control_layout.addWidget(start)
        abort=QPushButton("ABORT TEST / STOP");abort.setObjectName("stopButton");abort.clicked.connect(lambda:self.comparison_sequence.abort("Operator abort"));control_layout.addWidget(abort)
        export=QPushButton("Exporteer resultaten als CSV");export.clicked.connect(self.export_comparison_results);control_layout.addWidget(export);control_layout.addStretch();middle.addWidget(control,1);layout.addLayout(middle)

        results=QGroupBox("Ranglijst — medianen over alle geldige herhalingen");results_layout=QVBoxLayout(results)
        result_labels=("Rang","Profiel","Runs","Score","Rise s","Settling s","Overshoot %","Delay s","Piek A","RMS A","Energie J","Max snelheid %/s","Max accel. %/s²","Eindfout %")
        self.comparison_results_table=QTableWidget(0,len(result_labels));self.comparison_results_table.setHorizontalHeaderLabels(result_labels);self.comparison_results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.comparison_results_table.setAlternatingRowColors(True);self.comparison_results_table.verticalHeader().setVisible(False)
        result_header=self.comparison_results_table.horizontalHeader();result_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);result_header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.comparison_results_table.setMinimumHeight(220);results_layout.addWidget(self.comparison_results_table);layout.addWidget(results);layout.addStretch();return page

    def _diagnostics_tab(self) -> QWidget:
        page=QWidget();layout=QVBoxLayout(page);row=QHBoxLayout();self.diag_counts=QLabel("RX 0 | TX 0 | Invalid 0");self.diag_timing=QLabel("Control — | Telemetry — | Free RAM —");self.pause_console=QPushButton("Pause console");self.pause_console.clicked.connect(self.toggle_console);clear=QPushButton("Clear console");clear.clicked.connect(lambda:self.console.clear());row.addWidget(self.diag_counts);row.addWidget(self.diag_timing);row.addStretch();row.addWidget(self.pause_console);row.addWidget(clear);layout.addLayout(row)
        self.console=QPlainTextEdit();self.console.setReadOnly(True);self.console.document().setMaximumBlockCount(3000);layout.addWidget(self.console,1)
        manual=QHBoxLayout();self.manual_command=QLineEdit();self.manual_command.setPlaceholderText("Manual protocol line, e.g. GET,STATUS");self.manual_command.returnPressed.connect(self.send_manual);send=QPushButton("Send command");send.clicked.connect(self.send_manual);simfault=QPushButton("Simulate hard-overcurrent fault");simfault.clicked.connect(lambda:self.send("SIM,FAULT,7"));manual.addWidget(self.manual_command,1);manual.addWidget(send);manual.addWidget(simfault);layout.addLayout(manual);return page

    def _style(self) -> None:
        self.setStyleSheet("""
            QMainWindow,QWidget{background:#10151c;color:#dbe5ef;font-size:13px}
            QGroupBox{border:1px solid #435064;border-radius:4px;margin-top:10px;padding:9px;font-weight:600}
            QGroupBox::title{subcontrol-origin:margin;left:9px;padding:0 4px}
            QPushButton{background:#2c3d50;border:1px solid #59718c;padding:7px;border-radius:3px}
            QPushButton:hover{background:#38516b} QPushButton:pressed{background:#1e8e6e}
            QPushButton#stopButton{background:#a32020;border:2px solid #ff6b6b;color:white;font-size:17px;font-weight:800}
            QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QPlainTextEdit{background:#1a222c;border:1px solid #4b596c;padding:4px}
            QTabBar::tab{background:#202a36;padding:9px} QTabBar::tab:selected{background:#34516f}
            QFrame#parameterCard{background:#171f29;border:1px solid #3d4c5d;border-radius:5px}
            QFrame#parameterCard:hover{border:1px solid #5f88ad;background:#192431}
        """)

    # Connection and transport -------------------------------------------------
    def refresh_ports(self) -> None:
        selected=self.port_combo.currentData();self.port_combo.clear()
        for device,description in available_ports():self.port_combo.addItem(f"{device} — {description}",device)
        if selected:
            index=self.port_combo.findData(selected)
            if index>=0:self.port_combo.setCurrentIndex(index)

    def connect_source(self) -> None:
        if self.worker and self.worker.isRunning():self._show_status("Already connected",True);return
        if self.simulation.isChecked(): worker:DataWorker=SimulationWorker()
        else:
            port=self.port_combo.currentData()
            if not port:self._show_status("Select a COM port or enable simulation mode",True);return
            worker=SerialWorker(str(port),self.baud.value())
        self.worker=worker;worker.telemetry_received.connect(self.on_telemetry);worker.line_received.connect(self.on_line_received);worker.line_sent.connect(self.on_line_sent);worker.packet_received.connect(self.on_packet);worker.error.connect(self.on_worker_error);worker.connection_changed.connect(self.on_connection_changed);worker.start()

    def disconnect_source(self) -> None:
        if self.worker:
            self.send("CMD,STOP");self.step_sequence.abort("Communication disconnected");self.comparison_sequence.abort("Communication disconnected");self.worker.stop();self.worker.wait(1500);self.worker=None

    def send(self,line:str) -> None:
        if self.worker and self.worker.isRunning():self.worker.send(line)
        else:self._show_status(f"Not connected; command not sent: {line}",True)

    def on_connection_changed(self,connected:bool,text:str) -> None:
        self.connection_status.setText(("● " if connected else "● ")+text);self.connection_status.setStyleSheet(f"color:{'#38d996' if connected else '#ef5350'};font-weight:700");self._show_status(text,not connected)
        if not connected and self.step_sequence.running:self.step_sequence.abort("Communication lost")
        if not connected and self.comparison_sequence.running:self.comparison_sequence.abort("Communication lost")

    def on_worker_error(self,text:str) -> None:
        self.invalid_packets+=1;self._console(f"ERROR  {text}");self._show_status(text,True)

    def on_line_received(self,line:str) -> None:
        self.rx_packets+=1;self.packet_times.append(time.monotonic());self._console("RX  "+line)

    def on_line_sent(self,line:str) -> None:
        self.tx_packets+=1
        if line!="CMD,HEARTBEAT":self._console("TX  "+line)

    def on_packet(self,kind:str,payload:object) -> None:
        if kind=="VER":
            fields=payload if isinstance(payload,list) else []
            self.firmware.setText(" ".join(str(x) for x in fields[:3]))
        elif kind=="CFG" and isinstance(payload,tuple):
            name,value=payload
            if name=="END":self._refresh_config_widgets()
            else:
                try:self.config.values[str(name)]=float(value)
                except (ValueError,TypeError):
                    self.invalid_packets+=1;self._console(f"Invalid CFG value: {name}={value!r}")
        elif kind=="ERR":self._show_status("Arduino error: "+",".join(str(x) for x in payload),True)

    # Control ------------------------------------------------------------------
    def emergency_stop(self) -> None:
        if self.step_sequence.running:self.step_sequence.abort("STOP pressed")
        if self.comparison_sequence.running:self.comparison_sequence.abort("STOP pressed")
        self.send("CMD,STOP");self._show_status("STOP sent — actuator disabled",True)

    def change_mode(self,mode:str) -> None:
        self.send("CMD,PWM,0");self.send("CMD,MANUAL,0,0")
        if mode=="DISABLED":self.send("CMD,STOP")
        else:self.send(f"CMD,MODE,{mode}")

    def manual_press(self,direction:int) -> None:
        self.send(f"CMD,MANUAL,{direction},{self.manual_pwm.value()}")
        if not self.hold_to_run.isChecked() and self.jog_ms.value()>0:QTimer.singleShot(self.jog_ms.value(),lambda:self.send("CMD,MANUAL,0,0"))

    def manual_release(self) -> None:
        if self.hold_to_run.isChecked():self.send("CMD,MANUAL,0,0")

    def send_target(self) -> None:self.send(f"CMD,TARGET,{self.target_spin.value():.3f}")

    def set_one(self,name:str,value:float) -> None:
        if self._building_fields:return
        try:self.config.update(name,value)
        except ValueError as exc:self._show_status(str(exc),True);self._refresh_config_widgets();return
        self.send(f"SET,{name},{value:g}")

    def apply_all(self) -> None:
        candidate={name:field.value() for name,field in self.parameter_fields.items()}
        try:self.config.merge(candidate)
        except ValueError as exc:self._show_status(str(exc),True);return
        priority=["MAX_PWM","HARD_CURRENT","FB_MAX","CMD_MAX","UPPER_LIMIT"]
        names=priority+[n for n in candidate if n not in priority]
        for name in names:self.send(f"SET,{name},{candidate[name]:g}")
        self._show_status("Controller parameters queued")

    def apply_preset(self,kp:float,kd:float,slew:float) -> None:
        for name,value in (("KP",kp),("KD",kd),("PWM_SLEW",slew)):
            if name in self.parameter_fields:self.parameter_fields[name].setValue(value)
        self.apply_all()

    def restore_defaults(self) -> None:
        answer=QMessageBox.question(self,"Restore defaults","Stop the actuator and restore firmware safe defaults?")
        if answer==QMessageBox.StandardButton.Yes:self.send("CMD,STOP");self.send("DEFAULTS,CONFIG");self.send("GET,CONFIG")

    def export_config(self) -> None:
        path,_=QFileDialog.getSaveFileName(self,"Export configuration","actuator_config.json","JSON (*.json)")
        if path:
            try:self.config.export_json(path);self._show_status(f"Exported {path}")
            except OSError as exc:self._show_status(str(exc),True)

    def import_config(self) -> None:
        path,_=QFileDialog.getOpenFileName(self,"Import configuration","","JSON (*.json)")
        if not path:return
        try:self.config.import_json(path);self._refresh_config_widgets();self.apply_all()
        except (OSError,ValueError,json.JSONDecodeError) as exc:self._show_status(f"Import failed: {exc}",True)

    def capture_cal(self,sensor:str,endpoint:str) -> None:
        self.send("CMD,STOP");self.send(f"CMD,CAL,{sensor},{endpoint}");self.send("GET,CONFIG")

    # Telemetry and display -----------------------------------------------------
    def on_telemetry(self,t:Telemetry) -> None:
        self.latest=t;self.last_telemetry.setText(datetime.fromtimestamp(t.pc_time).strftime("%H:%M:%S.%f")[:-3]);self.active_mode.setText(f"ACTIVE MODE: {t.mode}")
        self.main_values["position"].setText(f"{t.feedback_pct:6.2f}");self.main_values["error"].setText(f"{t.error_pct:+6.2f}");self.main_values["pwm"].setText(str(t.pwm));self.main_values["current"].setText(f"{t.current_a:.3f}");self.main_values["voltage"].setText(f"{t.bus_voltage_v:.2f}");self.main_values["fault"].setText(t.fault_text if t.fault_code else "NO FAULT")
        self.fault_banner.setText(f"FAULT {t.fault_code}: {t.fault_text} | Latched: {'YES' if t.fault_latched else 'no'} | Age: {t.fault_age_ms} ms")
        self.fault_banner.setStyleSheet(f"font-size:16px;font-weight:700;background:{'#6b2020' if t.fault_code else '#17392f'};color:{'#fff' if t.fault_code else '#8ff0c8'};padding:6px")
        self.target_info.setText(f"Target {t.target_pct:.2f}%   Actual {t.feedback_pct:.2f}%   Error {t.error_pct:+.2f}%")
        self.follow_info.setText(f"Command {t.command_pct:.2f}%\nFeedback {t.feedback_pct:.2f}%\nError {t.error_pct:+.2f}%\nPWM {t.pwm}\nMode {t.mode}")
        fb=self.cal_widgets["FB"];cmd=self.cal_widgets["CMD"];fb["raw"].setText(str(t.feedback_raw));fb["pct"].setText(f"{t.feedback_pct:.2f}%");cmd["raw"].setText(str(t.command_raw));cmd["pct"].setText(f"{t.command_pct:.2f}%")
        for holder,prefix in ((fb,"FB"),(cmd,"CMD")):
            span=self.config.values.get(prefix+"_MAX",0)-self.config.values.get(prefix+"_MIN",0);holder["status"].setText("Valid calibration" if span>=50 else "INVALID: span < 50 ADC")
        for key,value in (("current",t.current_a),("filtered",t.filtered_current_a),("peak",t.peak_current_a),("voltage",t.bus_voltage_v),("shunt",t.shunt_voltage_mv),("power",t.power_w)):self.ina_values[key].setText(f"{value:.3f}")
        hard="ACTIVE / LATCHED" if t.fault_code==7 else "OK";warning=t.current_a>=self.config.values.get("CURRENT_WARN",999);travel="LOWER" if t.lower_limit else ("UPPER" if t.upper_limit else "OK")
        self.ina_status.setText(f"INA228: {'connected' if t.ina_ok else 'NOT DETECTED'} | Warning: {'ACTIVE' if warning else 'OK'} | Soft limit: {'ACTIVE' if t.soft_limit_active else 'OK'} | Hard limit: {hard} | Stall: {'ACTIVE' if t.stall_active else 'OK'} | Travel limit: {travel}")
        color="#ef5350" if t.fault_code or t.estop else ("#ffb703" if warning or t.soft_limit_active or t.lower_limit or t.upper_limit else "#80ed99");self.ina_status.setStyleSheet(f"font-size:17px;font-weight:700;color:{color}")
        self.diag_timing.setText(f"Control {t.control_period_us} µs | Telemetry {t.telemetry_period_us} µs | Free RAM {t.free_ram} B")
        self.graphs.add(t);self.logger.add(t);self.step_sequence.on_telemetry(t);self.comparison_sequence.on_telemetry(t)

    def _refresh_config_widgets(self) -> None:
        self._building_fields=True
        for name,field in self.parameter_fields.items():
            if name in self.config.values:field.setValue(self.config.values[name])
        for name,field in self.current_fields.items():
            if name in self.config.values:field.setValue(self.config.values[name])
        self.motor_invert.setChecked(bool(self.config.values.get("MOTOR_INVERT",0)));self.cmd_invert.setChecked(bool(self.config.values.get("CMD_INVERT",0)));self.fb_invert.setChecked(bool(self.config.values.get("FB_INVERT",0)));self.stall_enable.setChecked(bool(self.config.values.get("STALL_ENABLE",0)))
        for sensor,prefix in (("FB","FB"),("CMD","CMD")):
            self.cal_widgets[sensor]["min"].setValue(int(self.config.values.get(prefix+"_MIN",0)));self.cal_widgets[sensor]["max"].setValue(int(self.config.values.get(prefix+"_MAX",1023)));self.cal_widgets[sensor]["invert"].setChecked(bool(self.config.values.get(prefix+"_INVERT",0)))
        lo=self.config.values.get("LOWER_LIMIT",0);hi=self.config.values.get("UPPER_LIMIT",100);self.target_spin.setRange(lo,hi);self.target_slider.setRange(round(lo*10),round(hi*10));self._building_fields=False

    def _field_to_slider(self,name:str,value:float,slider:QSlider) -> None:
        spec=SPECS[name];slider.blockSignals(True);slider.setValue(round(1000*(value-spec.minimum)/(spec.maximum-spec.minimum)));slider.blockSignals(False)

    def _slider_to_field(self,name:str,value:int,field:QDoubleSpinBox) -> None:
        spec=SPECS[name];field.setValue(spec.minimum+(spec.maximum-spec.minimum)*value/1000)

    # Logging and test sequence -------------------------------------------------
    def start_logging(self) -> None:
        self._start_logging()

    def _start_logging(self,extra_metadata:dict|None=None) -> Path|None:
        if self.logger.active:return
        folder=Path(__file__).resolve().parents[1]/"logs"
        metadata={"gui_version":GUI_VERSION,"firmware_version":self.firmware.text(),"com_port":"SIMULATION" if self.simulation.isChecked() else self.port_combo.currentData(),"baud":self.baud.value(),"date_time":datetime.now().isoformat(),"configuration":self.config.values}
        if extra_metadata:metadata.update(extra_metadata)
        try:path=self.logger.start(folder,metadata)
        except (OSError,RuntimeError) as exc:self._show_status(f"Logging failed: {exc}",True);return None
        self.start_log_button.setEnabled(False);self.stop_log_button.setEnabled(True);self._show_status(f"Logging to {path}");return path

    def stop_logging(self) -> None:
        csv_path=self.logger.path;self.logger.stop();self.start_log_button.setEnabled(True);self.stop_log_button.setEnabled(False)
        message=f"Logging stopped; dropped rows: {self.logger.dropped}"
        if csv_path:
            dialog=QMessageBox(self);dialog.setWindowTitle("Logging gestopt")
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setText("Wil je deze meting bewaren of definitief verwijderen?")
            dialog.setInformativeText(f"{csv_path.name}\n\nBewaren maakt ook een PNG met alle grafieken uit de volledige CSV.")
            keep=dialog.addButton("Bewaren + grafieken maken",QMessageBox.ButtonRole.AcceptRole)
            remove=dialog.addButton("Definitief verwijderen",QMessageBox.ButtonRole.DestructiveRole)
            dialog.setDefaultButton(keep);dialog.exec()
            if dialog.clickedButton() is remove:
                try:
                    deleted=self._delete_log_files(csv_path);message+=f"; {deleted} logbestanden verwijderd"
                except OSError as exc:message+=f"; verwijderen mislukt: {exc}"
            else:
                try:message+=f"; grafieken opgeslagen als {save_log_graphs(csv_path).name}"
                except (OSError,ValueError) as exc:message+=f"; grafiekexport mislukt: {exc}"
        self._show_status(message, "mislukt" in message)

    @staticmethod
    def _delete_log_files(csv_path:Path)->int:
        """Delete only the CSV and its derived sidecars from the same log folder."""
        folder=csv_path.parent.resolve();candidates=[csv_path,csv_path.with_suffix(".json"),csv_path.with_name(f"{csv_path.stem}_graphs.png")];deleted=0
        for candidate in candidates:
            if candidate.parent.resolve()!=folder or not candidate.name.startswith("actuator_"):
                raise OSError(f"Onveilig logpad geweigerd: {candidate}")
            if candidate.exists():candidate.unlink();deleted+=1
        return deleted

    def start_step_test(self) -> None:
        if self.comparison_sequence.running:self._show_status("Stop eerst de controller comparison",True);return
        s=StepSettings(self.step_fields["start"].value(),self.step_fields["end"].value(),self.step_fields["delay"].value(),self.step_fields["hold"].value(),round(self.step_fields["reps"].value()),self.step_fields["max_current"].value(),self.step_fields["duration"].value(),self.return_start.isChecked(),self.step_fields["tolerance"].value())
        try:self.step_sequence.start(s,self.latest)
        except RuntimeError as exc:self._show_status(str(exc),True)

    def _step_status(self,text:str) -> None:self.step_status.setText(text)
    def _step_finished(self,metrics:dict) -> None:
        self.step_results.setPlainText("\n".join(f"{key.replace('_',' ').title()}: {value:.4g}" for key,value in metrics.items()));self._show_status("Step test complete")

    # Automated controller comparison ----------------------------------------
    def _insert_comparison_profile(self,name:str,values:dict[str,float],enabled:bool=True) -> None:
        row=self.comparison_profiles.rowCount();self.comparison_profiles.insertRow(row)
        use=QTableWidgetItem();use.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsSelectable);use.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked);self.comparison_profiles.setItem(row,0,use)
        self.comparison_profiles.setItem(row,1,QTableWidgetItem(name))
        for column,key in enumerate(PROFILE_PARAMETERS,start=2):
            value=float(values.get(key,self.config.values.get(key,0.0)));item=QTableWidgetItem(f"{value:g}");item.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
            if key in SPECS:item.setToolTip(f"{key}: {SPECS[key].tooltip}\nToegestaan: {SPECS[key].minimum:g}..{SPECS[key].maximum:g} {SPECS[key].unit}")
            self.comparison_profiles.setItem(row,column,item)
        self.comparison_profiles.selectRow(row)

    def _add_current_comparison_profile(self) -> None:
        self._insert_comparison_profile(f"Profiel {self.comparison_profiles.rowCount()+1}",self.config.values,True)

    def _duplicate_comparison_profile(self) -> None:
        row=self.comparison_profiles.currentRow()
        if row<0:self._show_status("Selecteer eerst een profiel",True);return
        try:profile=self._comparison_profile_from_row(row,require_enabled=False)
        except ValueError as exc:self._show_status(str(exc),True);return
        self._insert_comparison_profile(profile.name+" kopie",profile.values,True)

    def _remove_comparison_profile(self) -> None:
        row=self.comparison_profiles.currentRow()
        if row>=0:self.comparison_profiles.removeRow(row)

    def _comparison_profile_from_row(self,row:int,require_enabled:bool=True) -> ControllerProfile:
        use=self.comparison_profiles.item(row,0)
        if require_enabled and (use is None or use.checkState()!=Qt.CheckState.Checked):raise ValueError("Profiel is niet aangevinkt")
        name_item=self.comparison_profiles.item(row,1);name=name_item.text().strip() if name_item else ""
        if not name:raise ValueError(f"Profiel op rij {row+1} heeft geen naam")
        values=dict(self.config.values)
        for column,key in enumerate(PROFILE_PARAMETERS,start=2):
            item=self.comparison_profiles.item(row,column)
            try:value=float((item.text() if item else "").replace(",","."))
            except ValueError as exc:raise ValueError(f"{name}: ongeldige waarde voor {key}") from exc
            values[key]=value
        ControllerConfig.validate(values)
        return ControllerProfile(name,{key:values[key] for key in PROFILE_PARAMETERS})

    def _collect_comparison_profiles(self) -> list[ControllerProfile]:
        profiles=[]
        for row in range(self.comparison_profiles.rowCount()):
            use=self.comparison_profiles.item(row,0)
            if use and use.checkState()==Qt.CheckState.Checked:profiles.append(self._comparison_profile_from_row(row))
        names=[profile.name.casefold() for profile in profiles]
        if len(names)!=len(set(names)):raise ValueError("Profielnamen moeten uniek zijn")
        if not profiles:raise ValueError("Vink minimaal één controllerprofiel aan")
        return profiles

    def _generate_comparison_sweep(self) -> None:
        parameter=self.comparison_sweep_parameter.currentText();spread=self.comparison_sweep_spread.value()/100.0;center=float(self.config.values[parameter]);spec=SPECS[parameter]
        delta=abs(center)*spread
        if delta<1e-9:delta=(spec.maximum-spec.minimum)*spread*0.10
        candidates=[max(spec.minimum,min(spec.maximum,center-delta)),center,max(spec.minimum,min(spec.maximum,center+delta))]
        generated=[]
        try:
            for value in candidates:
                values=dict(self.config.values);values[parameter]=value;ControllerConfig.validate(values);generated.append((f"{parameter} {value:g}",values))
        except ValueError as exc:self._show_status(f"Sweep past niet binnen veilige configuratie: {exc}",True);return
        self.comparison_profiles.setRowCount(0)
        for name,values in generated:self._insert_comparison_profile(name,values,True)
        self._show_status(f"Drie {parameter}-profielen gemaakt; controleer ze vóór START")

    def start_controller_comparison(self) -> None:
        if not self.worker or not self.worker.isRunning():self._show_status("Maak eerst verbinding of start simulatiemodus",True);return
        if self.step_sequence.running:self._show_status("Stop eerst de gewone step-test",True);return
        if self.comparison_sequence.running:self._show_status("Controller comparison draait al",True);return
        try:
            profiles=self._collect_comparison_profiles()
            settings=ComparisonSettings(
                start_pct=self.comparison_fields["start"].value(),end_pct=self.comparison_fields["end"].value(),
                baseline_s=self.comparison_fields["baseline"].value(),hold_s=self.comparison_fields["hold"].value(),
                repetitions=round(self.comparison_fields["reps"].value()),tolerance_pct=self.comparison_fields["tolerance"].value(),
                max_current_a=self.comparison_fields["max_current"].value(),move_timeout_s=self.comparison_fields["move_timeout"].value(),
            )
            lower=self.config.values.get("LOWER_LIMIT",0);upper=self.config.values.get("UPPER_LIMIT",100)
            if not lower<=settings.start_pct<=upper or not lower<=settings.end_pct<=upper:raise ValueError(f"Testposities moeten binnen softwarelimieten {lower:g}..{upper:g}% liggen")
        except ValueError as exc:self._show_status(str(exc),True);return
        total=len(profiles)*settings.repetitions
        answer=QMessageBox.question(self,"Controller comparison starten",f"De actuator beweegt automatisch {total} keer van {settings.start_pct:g}% naar {settings.end_pct:g}% en terug.\n\nProfielen: {len(profiles)}\nHerhalingen per profiel: {settings.repetitions}\nAbort boven: {settings.max_current_a:g} A\n\nControleer fysieke noodstop, vrije slag en stroombegrensde voeding. Starten?")
        if answer!=QMessageBox.StandardButton.Yes:return
        self.comparison_results=[];self.comparison_test_profiles=profiles;self.comparison_results_table.setRowCount(0);self.comparison_progress.setRange(0,total);self.comparison_progress.setValue(0);self.comparison_progress.setFormat(f"0 / {total} runs")
        self.comparison_log_owned=False
        if self.comparison_auto_log.isChecked() and not self.logger.active:
            metadata={"test_type":"controller_comparison","comparison_settings":asdict(settings),"controller_profiles":[asdict(profile) for profile in profiles],"torque_note":"INA228 is high-side supply current; no direct motor torque or actuator force measurement."}
            if self._start_logging(metadata) is None:return
            self.comparison_log_owned=True
        try:self.comparison_sequence.start(profiles,settings,self.latest,self.config.values)
        except RuntimeError as exc:
            self._show_status(str(exc),True)
            if self.comparison_log_owned:self.logger.stop();self.comparison_log_owned=False

    def _comparison_status(self,text:str) -> None:self.comparison_status.setText(text)
    def _comparison_progress(self,completed:int,total:int) -> None:self.comparison_progress.setRange(0,total);self.comparison_progress.setValue(completed);self.comparison_progress.setFormat(f"{completed} / {total} runs")
    def _comparison_run_finished(self,metrics:RunMetrics) -> None:self._show_status(f"Run klaar: {metrics.profile_name} #{metrics.repetition} — rise {metrics.rise_time_s:.3f} s")

    def _comparison_finished(self,results:list[ProfileResult]) -> None:
        self.comparison_results=results;self._populate_comparison_results();self.config.values.update(self.comparison_sequence.original_values);self._refresh_config_widgets();self._show_status("Controller comparison compleet — beste geldige profiel staat bovenaan")
        if self.comparison_log_owned:self.comparison_log_owned=False;self.stop_logging()

    def _comparison_aborted(self,reason:str) -> None:
        self.config.values.update(self.comparison_sequence.original_values);self._refresh_config_widgets();self.comparison_status.setText("ABORTED: "+reason);self._show_status("Controller comparison afgebroken: "+reason,True)
        if self.comparison_log_owned:self.comparison_log_owned=False;self.stop_logging()

    @staticmethod
    def _result_text(value:float,decimals:int=3) -> str:return "—" if not math.isfinite(value) else f"{value:.{decimals}f}"

    def _populate_comparison_results(self) -> None:
        self.comparison_results_table.setRowCount(0)
        for result in self.comparison_results:
            row=self.comparison_results_table.rowCount();self.comparison_results_table.insertRow(row);m=result.medians
            values=[str(result.rank),result.profile_name,str(len(result.runs)),self._result_text(result.score,1),self._result_text(m["rise_time_s"]),self._result_text(m["settling_time_s"]),self._result_text(m["overshoot_pct"],2),self._result_text(m["movement_delay_s"]),self._result_text(m["peak_current_a"]),self._result_text(m["rms_current_a"]),self._result_text(m["energy_j"],2),self._result_text(m["max_speed_pct_s"],1),self._result_text(m["max_acceleration_pct_s2"],1),self._result_text(m["steady_state_error_pct"],2)]
            for column,value in enumerate(values):self.comparison_results_table.setItem(row,column,QTableWidgetItem(value))

    def export_comparison_results(self) -> None:
        if not self.comparison_results:self._show_status("Voer eerst een controller comparison uit",True);return
        default=Path(__file__).resolve().parents[1]/"logs"/f"controller_comparison_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
        path,_=QFileDialog.getSaveFileName(self,"Exporteer controllervergelijking",str(default),"CSV (*.csv)")
        if not path:return
        profile_map={profile.name:profile.values for profile in self.comparison_test_profiles}
        fields=["rank","profile","runs","valid","score"]+list(PROFILE_PARAMETERS)+list(self.comparison_results[0].medians)
        try:
            with Path(path).open("w",newline="",encoding="utf-8") as stream:
                writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
                for result in self.comparison_results:
                    row={"rank":result.rank,"profile":result.profile_name,"runs":len(result.runs),"valid":int(result.valid),"score":result.score};row.update(profile_map.get(result.profile_name,{}));row.update(result.medians);writer.writerow(row)
            self._show_status(f"Vergelijkingsresultaten opgeslagen: {path}")
        except OSError as exc:self._show_status(f"Export mislukt: {exc}",True)

    # Misc ---------------------------------------------------------------------
    def _update_connection_stats(self) -> None:
        now=time.monotonic()
        while self.packet_times and now-self.packet_times[0]>2:self.packet_times.popleft()
        rate=len(self.packet_times)/2;self.packet_rate.setText(f"{rate:.1f} Hz");self.invalid_count.setText(str(self.invalid_packets));self.diag_counts.setText(f"RX {self.rx_packets} | TX {self.tx_packets} | Invalid {self.invalid_packets}")
        if self.latest:
            age=max(0,time.time()-self.latest.pc_time)
            if age>1:self.last_telemetry.setText(f"STALE {age:.1f} s")

    def toggle_console(self) -> None:self.console_paused=not self.console_paused;self.pause_console.setText("Resume console" if self.console_paused else "Pause console")
    def _console(self,text:str) -> None:
        if not self.console_paused:self.console.appendPlainText(text)
    def send_manual(self) -> None:
        line=self.manual_command.text().strip()
        if line:self.send(line);self.manual_command.clear()
    def _show_status(self,text:str,error:bool=False) -> None:self.statusBar().showMessage(text,8000 if error else 4000)

    def closeEvent(self,event:QCloseEvent) -> None:
        self.emergency_stop()
        if self.logger.active:self.stop_logging()
        else:self.logger.stop()
        if self.worker:self.worker.stop();self.worker.wait(1500)
        event.accept()
