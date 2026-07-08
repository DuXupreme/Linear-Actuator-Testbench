"""Visual, self-explanatory controls for controller tuning parameters."""
from __future__ import annotations

import math
from typing import Mapping

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget,
)

from ..config_model import ParameterSpec


# Friendly labels and the practical consequence of moving a slider. These are
# deliberately separate from the protocol names, which remain visible for
# diagnostics and documentation.
PARAMETER_UI: dict[str, tuple[str, str, str]] = {
    "KP": ("Proportionele kracht", "Rustiger / langzamer", "Sterker / sneller"),
    "KI": ("Blijvende fout wegwerken", "Minder correctie", "Meer correctie / wind-up"),
    "KD": ("Bewegingsdemping", "Minder remming", "Meer demping / ruisgevoeliger"),
    "DEADBAND": ("Rustzone rond doel", "Preciezer / kans op trillen", "Rustiger / minder precies"),
    "MIN_PWM": ("Minimale bewegingskracht", "Zachtere start", "Makkelijker loskomen / hardere start"),
    "MAX_PWM": ("Maximale motorsnelheid", "Langzamer / minder stroom", "Sneller / meer stroom"),
    "PWM_SLEW": ("PWM-oploopsnelheid", "Zacht en geleidelijk", "Direct en feller"),
    "REVERSAL_MS": ("Pauze bij omkeren", "Sneller omkeren", "Langere beschermpauze"),
    "INTEGRAL_LIMIT": ("Geheugenlimiet I-regelaar", "Minder opgeslagen correctie", "Meer correctiereserve"),
    "DERIV_FILTER": ("Filtering van D-signaal", "Directer / meer ruis", "Rustiger / meer vertraging"),
    "FEEDBACK_FILTER": ("Filtering positiefeedback", "Directer / meer ruis", "Glad / meer vertraging"),
    "COMMAND_FILTER": ("Filtering commandopotmeter", "Direct volgen", "Rustiger volgen"),
    "LOWER_LIMIT": ("Onderste softwaregrens", "Meer lage slag", "Minder lage slag"),
    "UPPER_LIMIT": ("Bovenste softwaregrens", "Minder hoge slag", "Meer hoge slag"),
    "SLOWDOWN_ZONE": ("Afremzone bij eindgrens", "Later afremmen", "Eerder afremmen"),
    "NEAR_LIMIT_PWM": ("PWM nabij eindgrens", "Langzamer bij einde", "Sneller bij einde"),
    "CONTROL_HZ": ("Regelfrequentie", "Minder CPU / trager", "Snellere updates"),
    "POT_HZ": ("Potmetermeting", "Minder vaak meten", "Vaker meten"),
}


class MiniInfluenceGraph(QWidget):
    """Small qualitative graph; it explains direction, not a plant simulation."""

    def __init__(self, name: str, spec: ParameterSpec, value: float) -> None:
        super().__init__(); self.name = name; self.spec = spec; self.value = value
        self.setMinimumHeight(58); self.setMaximumHeight(68)
        self.setToolTip("Kwalitatieve weergave van de invloed. Dit is geen simulatie van de echte actuator.")

    def set_value(self, value: float) -> None:
        self.value = value; self.update()

    def _ratio(self) -> float:
        return max(0.0, min(1.0, (self.value-self.spec.minimum)/max(self.spec.maximum-self.spec.minimum, 1e-9)))

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        painter.fillRect(rect, QColor("#111820")); painter.setPen(QPen(QColor("#344252"), 1)); painter.drawRect(rect)
        painter.setPen(QPen(QColor("#273443"), 1));
        painter.drawLine(QPointF(rect.left()+4, rect.center().y()), QPointF(rect.right()-4, rect.center().y()))
        r = self._ratio(); accent = QPen(QColor("#4cc9f0"), 2.2); muted = QPen(QColor("#586779"), 1.2)
        path = QPainterPath()

        if self.name in {"LOWER_LIMIT","UPPER_LIMIT","SLOWDOWN_ZONE","NEAR_LIMIT_PWM"}:
            lower = r if self.name == "LOWER_LIMIT" else 0.12
            upper = r if self.name == "UPPER_LIMIT" else 0.88
            if self.name == "SLOWDOWN_ZONE": lower, upper = 0.12+r*.25, 0.88-r*.25
            painter.fillRect(QRectF(rect.left()+lower*rect.width(),rect.top()+17,(upper-lower)*rect.width(),16),QColor("#164d41"))
            painter.setPen(accent); painter.drawLine(QPointF(rect.left()+lower*rect.width(),rect.top()+10),QPointF(rect.left()+lower*rect.width(),rect.bottom()-8)); painter.drawLine(QPointF(rect.left()+upper*rect.width(),rect.top()+10),QPointF(rect.left()+upper*rect.width(),rect.bottom()-8))
        elif self.name in {"FEEDBACK_FILTER","COMMAND_FILTER","DERIV_FILTER"}:
            raw = [.5,.18,.78,.30,.67,.40,.82,.28,.62,.46]
            painter.setPen(muted); raw_path=QPainterPath()
            for i,yv in enumerate(raw):
                x=rect.left()+i/(len(raw)-1)*rect.width(); y=rect.bottom()-yv*rect.height(); raw_path.moveTo(x,y) if i==0 else raw_path.lineTo(x,y)
            painter.drawPath(raw_path)
            alpha=max(.03,1-self.value); filtered=raw[0]; painter.setPen(accent); smooth=QPainterPath()
            for i,yv in enumerate(raw):
                filtered += alpha*(yv-filtered); x=rect.left()+i/(len(raw)-1)*rect.width(); y=rect.bottom()-filtered*rect.height(); smooth.moveTo(x,y) if i==0 else smooth.lineTo(x,y)
            painter.drawPath(smooth)
        elif self.name == "REVERSAL_MS":
            pause=.05+r*.45; painter.setPen(accent); path.moveTo(rect.left(),rect.top()+10); path.lineTo(rect.center().x()-pause*rect.width()/2,rect.top()+10);path.lineTo(rect.center().x()-pause*rect.width()/2,rect.center().y());path.lineTo(rect.center().x()+pause*rect.width()/2,rect.center().y());path.lineTo(rect.center().x()+pause*rect.width()/2,rect.bottom()-10);path.lineTo(rect.right(),rect.bottom()-10);painter.drawPath(path)
        elif self.name in {"CONTROL_HZ","POT_HZ"}:
            painter.setPen(accent); count=3+round(r*17)
            for i in range(count):
                x=rect.left()+i/max(1,count-1)*rect.width();painter.drawLine(QPointF(x,rect.top()+12),QPointF(x,rect.bottom()-12))
        else:
            painter.setPen(accent)
            if self.name == "KD":
                damping=.5+3*r
                for i in range(60):
                    t=i/59*5; yv=1-math.exp(-damping*t/3)*math.cos(t*2.2);x=rect.left()+i/59*rect.width();y=rect.bottom()-min(1.5,max(-.1,yv))/1.5*rect.height();path.moveTo(x,y) if i==0 else path.lineTo(x,y)
            elif self.name in {"PWM_SLEW","KI","INTEGRAL_LIMIT"}:
                slope=.15+.85*r; cap=.35+.6*r if self.name=="INTEGRAL_LIMIT" else .95
                for i in range(30):
                    yv=min(cap,i/29*(.15+1.5*slope));x=rect.left()+i/29*rect.width();y=rect.bottom()-yv*rect.height();path.moveTo(x,y) if i==0 else path.lineTo(x,y)
            else:
                width=.05+.38*r if self.name=="DEADBAND" else .08
                gain=.25+.7*r
                for i in range(41):
                    xnorm=i/40*2-1
                    if self.name=="DEADBAND" and abs(xnorm)<width: yv=0
                    else: yv=max(-1,min(1,xnorm*gain*2.2))
                    if self.name=="MIN_PWM" and abs(yv)>0: yv=math.copysign(max(abs(yv),.1+.65*r),yv)
                    if self.name=="MAX_PWM": yv=max(-(.2+.8*r),min(.2+.8*r,xnorm*2))
                    x=rect.left()+i/40*rect.width();y=rect.center().y()-yv*rect.height()*.45;path.moveTo(x,y) if i==0 else path.lineTo(x,y)
            painter.drawPath(path)
        painter.end()


class ParameterCard(QFrame):
    valuePreviewed = Signal(str, float)
    valueCommitted = Signal(str, float)

    def __init__(self, name: str, spec: ParameterSpec, value: float) -> None:
        super().__init__(); self.name = name; self.spec = spec
        title, low_effect, high_effect = PARAMETER_UI.get(name,(name,"Lager","Hoger"))
        self.setFrameShape(QFrame.Shape.StyledPanel); self.setObjectName("parameterCard")
        self.setToolTip(spec.tooltip); self.setMinimumHeight(188)
        root=QVBoxLayout(self);root.setContentsMargins(11,9,11,9);root.setSpacing(5)
        header=QHBoxLayout();label=QLabel(title);label.setStyleSheet("font-size:15px;font-weight:700;color:#eef5fb");code=QLabel(name);code.setStyleSheet("color:#86a5c4;background:#172433;padding:2px 6px;border-radius:3px");header.addWidget(label);header.addStretch();header.addWidget(code);root.addLayout(header)
        explanation=QLabel(spec.tooltip);explanation.setWordWrap(True);explanation.setStyleSheet("color:#aebdcb");explanation.setToolTip(spec.tooltip);root.addWidget(explanation)
        effects=QHBoxLayout();left=QLabel("← "+low_effect);right=QLabel(high_effect+" →");left.setStyleSheet("color:#88b7dc");right.setStyleSheet("color:#f2b66d");effects.addWidget(left);effects.addStretch();effects.addWidget(right);root.addLayout(effects)
        control=QHBoxLayout();self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,1000);self.slider.setToolTip(spec.tooltip);self.spin=QDoubleSpinBox();self.spin.setRange(spec.minimum,spec.maximum);self.spin.setDecimals(spec.decimals);self.spin.setKeyboardTracking(False);self.spin.setSuffix(" "+spec.unit if spec.unit else "");self.spin.setMinimumWidth(128);self.spin.setToolTip(spec.tooltip);control.addWidget(self.slider,1);control.addWidget(self.spin);root.addLayout(control)
        ranges=QHBoxLayout();ranges.addWidget(QLabel(f"min {spec.minimum:g}"));ranges.addStretch();ranges.addWidget(QLabel(f"toegestaan bereik · {spec.unit}"));ranges.addStretch();ranges.addWidget(QLabel(f"max {spec.maximum:g}"));root.addLayout(ranges)
        self.graph=MiniInfluenceGraph(name,spec,value);root.addWidget(self.graph)
        self.slider.valueChanged.connect(self._from_slider);self.spin.valueChanged.connect(self._from_spin);self.slider.sliderReleased.connect(self._commit);self.spin.editingFinished.connect(self._commit);self.set_value(value)

    def _ratio(self,value:float)->int:
        return round(1000*(value-self.spec.minimum)/max(self.spec.maximum-self.spec.minimum,1e-9))
    def _from_slider(self,position:int)->None:
        value=self.spec.minimum+(self.spec.maximum-self.spec.minimum)*position/1000
        self.spin.blockSignals(True);self.spin.setValue(value);self.spin.blockSignals(False);self.graph.set_value(self.spin.value());self.valuePreviewed.emit(self.name,self.spin.value())
    def _from_spin(self,value:float)->None:
        self.slider.blockSignals(True);self.slider.setValue(self._ratio(value));self.slider.blockSignals(False);self.graph.set_value(value);self.valuePreviewed.emit(self.name,value)
    def _commit(self)->None:self.valueCommitted.emit(self.name,self.spin.value())
    def set_value(self,value:float)->None:self.spin.setValue(value);self.graph.set_value(value)
    def value(self)->float:return self.spin.value()


class ControllerResponsePreview(QWidget):
    """Live static P/deadband/min/max transfer curve for the selected values."""
    def __init__(self,values:Mapping[str,float])->None:
        super().__init__();self.values=dict(values);self.setMinimumHeight(235);self.setToolTip("Statische regelcurve: toont P, deadband, minimum PWM en maximum PWM. I, D, filtering en motorsnelheid zijn tijdsafhankelijk en staan in de kaarten eronder.")
    def set_parameter(self,name:str,value:float)->None:self.values[name]=value;self.update()
    def paintEvent(self,event:object)->None:
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);outer=QRectF(self.rect()).adjusted(8,8,-8,-8);p.fillRect(outer,QColor("#111820"));p.setPen(QPen(QColor("#445467"),1));p.drawRect(outer)
        p.setFont(QFont("Arial",12,QFont.Weight.Bold));p.setPen(QColor("#edf5fb"));p.drawText(QPointF(outer.left()+14,outer.top()+24),"Live regelcurve: positiefout → PWM")
        p.setFont(QFont("Arial",9));p.setPen(QColor("#9fb0c1"));p.drawText(QPointF(outer.left()+14,outer.top()+43),"Statische P-invloed inclusief deadband en minimale/maximale PWM")
        plot=QRectF(outer.left()+62,outer.top()+55,outer.width()-90,outer.height()-85);kp=self.values.get("KP",0);dead=self.values.get("DEADBAND",0);minimum=self.values.get("MIN_PWM",0);maximum=max(1,self.values.get("MAX_PWM",255))
        p.setPen(QPen(QColor("#2d3a48"),1));
        for i in range(5):
            x=plot.left()+i/4*plot.width();p.drawLine(QPointF(x,plot.top()),QPointF(x,plot.bottom()));y=plot.top()+i/4*plot.height();p.drawLine(QPointF(plot.left(),y),QPointF(plot.right(),y))
        band=min(.45,dead/20);p.fillRect(QRectF(plot.center().x()-band*plot.width(),plot.top(),2*band*plot.width(),plot.height()),QColor(55,80,105,90))
        path=QPainterPath()
        for i in range(121):
            error=i/120*40-20
            if abs(error)<=dead:out=0
            else:
                out=max(-maximum,min(maximum,kp*error))
                if 0<out<minimum:out=minimum
                if -minimum<out<0:out=-minimum
            x=plot.left()+i/120*plot.width();y=plot.center().y()-out/maximum*plot.height()*.46;path.moveTo(x,y) if i==0 else path.lineTo(x,y)
        p.setPen(QPen(QColor("#4cc9f0"),3));p.drawPath(path);p.setFont(QFont("Arial",9));p.setPen(QColor("#b9c7d5"));p.drawText(QPointF(plot.left(),plot.bottom()+17),"−20% fout");p.drawText(QPointF(plot.center().x()-22,plot.bottom()+17),"doel");p.drawText(QPointF(plot.right()-52,plot.bottom()+17),"+20% fout")
        example=min(maximum,max(minimum,kp*10)) if kp else 0;p.setPen(QColor("#f2b66d"));p.drawText(QPointF(outer.right()-245,outer.top()+25),f"Bij 10% fout: circa {example:.0f} PWM")
        p.end()

