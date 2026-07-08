# Snelstart — Arduino Nano actuator testbank

Deze handleiding is voor een **klassieke Arduino Nano met ATmega328P en 5 V**.
Een Nano Every, Nano 33 of 3,3 V-board is een ander bord. De standaard pinnen
zijn D9, D10, D7, D8, D2, A0, A1, A4 en A5 en passen op de klassieke Nano.

> **Veiligheid:** begin zonder 24–48 V motorspanning. Gebruik later altijd een
> stroombegrensde voeding en een echte, bereikbare noodstop die de motorvoeding
> kan onderbreken. Dit programma is laboratoriumsoftware en geen machinebesturing.

## Deel 1 — probeer eerst de simulatie

Hierbij heb je de Nano en motor nog niet nodig.

1. Open PowerShell in deze projectmap.
2. Voer de volgende regels één voor één uit:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_gui.py
```

3. Zet in het bovenste vak een vinkje bij **Simulation mode (no hardware)**.
4. Klik **Connect**. Bovenin moet groen `SIMULATION` verschijnen en de waarden
   moeten gaan veranderen.
5. Klik **ENABLE CONTROL**.
6. Klik bij Operating mode op **Manual direction**.
7. Houd **EXTEND** of **RETRACT** ingedrukt. Bij loslaten wordt PWM nul.
8. Druk op de grote rode **STOP** of op de spatiebalk. De actieve mode wordt
   `DISABLED`.

Als dit werkt, zijn Python en de GUI goed geïnstalleerd.

## Deel 2 — firmware op de Nano zetten

1. Installeer Arduino IDE 2.x.
2. Open Library Manager in de Arduino IDE en installeer **Adafruit INA228**.
   De extra bibliotheek Adafruit BusIO wordt daarbij normaal automatisch
   geïnstalleerd.
3. Open dit bestand in de Arduino IDE:

```text
firmware\actuator_testbench\actuator_testbench.ino
```

4. Kies **Tools → Board → Arduino AVR Boards → Arduino Nano**.
5. Kies eerst **Tools → Processor → ATmega328P**.
6. Sluit de Nano via USB aan en kies de juiste **Tools → Port → COM...**.
7. Klik Upload.
8. Mislukt alleen het uploaden? Kies dan **ATmega328P (Old Bootloader)** en
   probeer opnieuw. Dit is heel gebruikelijk bij Nano-klonen.
9. Open eventueel Serial Monitor op **115200 baud**. Na reset hoort dit te staan:

```text
VER,ACTUATOR_TESTBENCH,1.0.0,PROTOCOL,1
```

Na opstarten staat de motor altijd uitgeschakeld.

## Deel 3 — bedrading

Doe dit met USB én motorvoeding uitgeschakeld.

| Nano | Aansluiten op |
|---|---|
| A0 | loper van de actuator-feedbackpotmeter |
| A1 | loper van de commandopotmeter |
| D9 | BTS7960 RPWM |
| D10 | BTS7960 LPWM |
| D7 | BTS7960 R_EN |
| D8 | BTS7960 L_EN |
| D2 | noodstopcontact naar GND; actief laag |
| A4 | INA228 SDA |
| A5 | INA228 SCL |
| 5V | logica-voeding van sensoren en H-brug, volgens hun opschrift |
| GND | gezamenlijke logicamassa van Nano, H-brug, INA228 en potmeters |

Potmeters: één buitenste aansluiting naar 5 V, de andere naar GND en de loper
naar A0 of A1. Er mag nooit 24–48 V op een Nano-pin komen.

INA228 aan de hoge kant:

```text
voeding +  →  INA228 VIN+ / IN+
INA228 VIN- / IN-  →  BTS7960 motorvoeding +
voeding -  →  BTS7960 motorvoeding - en gezamenlijke GND
```

De actuator gaat op de twee motoruitgangen van de BTS7960. Zet de motorvoeding
nog niet aan. Controleer eerst alle aansluitingen aan de hand van de tabellen in
de volledige README.

## Deel 4 — voor het eerst verbinden

1. Start de GUI met `python run_gui.py`.
2. Haal het vinkje bij Simulation mode weg.
3. Klik **Refresh ports** en kies de COM-poort van de Nano.
4. Laat baudrate op **115200** staan en klik **Connect**.
5. Controleer het volgende voordat je motorvoeding inschakelt:

   - Firmwareversie is zichtbaar.
   - `Last telemetry` blijft verversen.
   - Feedback raw en command raw liggen niet bij 0 of 1023.
   - INA228 meldt `connected`.
   - Noodstop is vrijgegeven en er is geen fout.

Verbinden stuurt automatisch STOP. De motor gaat dus niet vanzelf bewegen.

## Deel 5 — potmeters kalibreren

Ga naar **Sensor calibration**. De software beweegt de motor hierbij niet.

1. Zet of beweeg de feedbacksensor naar de veilige mechanische minimumpositie.
2. Klik bij Actuator feedback op **Capture current as minimum**.
3. Zet de sensor naar de veilige maximumpositie en klik **Capture current as
   maximum**.
4. Controleer of het percentage van laag naar hoog loopt. Gebruik **Invert** als
   het andersom loopt.
5. Doe hetzelfde voor de losse commandopotmeter.
6. Klik **Save calibrated configuration to EEPROM**.

Kan de actuator niet met de hand bewegen, doe de kalibratie dan pas na de
richtingstest hieronder. Gebruik zeer korte bewegingen met lage PWM en stop vóór
de harde mechanische eindpunten. Laat iemand bij de fysieke noodstop staan.

## Deel 6 — eerste echte beweging

1. Stel de voeding in op een lage, veilige stroomlimiet.
2. Zet de motorvoeding aan.
3. Klik **ENABLE CONTROL**.
4. Klik **Manual direction**.
5. Zet PWM eerst laag, bijvoorbeeld **60**.
6. Houd EXTEND heel kort ingedrukt en laat los.
7. Controleer of de feedbackpositie in de verwachte richting verandert.

Gaat de actuator de verkeerde kant op, druk STOP en vink **Motor direction
inverted** aan. Loopt de positiepercentage de verkeerde kant op, gebruik dan
Feedback invert. Gaat de positie snel de verkeerde kant op in Position- of
Follow-mode, druk onmiddellijk STOP.

## Deel 7 — de belangrijkste modes

- **Manual direction:** motor beweegt alleen zolang EXTEND/RETRACT wordt
  ingedrukt als Hold-to-run aanstaat.
- **Direct PWM:** stuur een getal tussen −255 en +255. Begin klein.
- **Position target:** kies een percentage binnen de softwarelimieten en klik
  Send target.
- **Follow potentiometer:** de actuator probeert het percentage van de tweede
  potmeter te volgen. Test dit pas nadat Manual en Position goed werken.
- **Step response:** voert een meetstap uit. Gebruik eerst logging en veilige
  stroom- en tijdlimieten.

Bij elke nieuwe verbinding moet je opnieuw **ENABLE CONTROL** kiezen. Mode
wisselen zet de uitgang eerst op nul. STOP, spatiebalk, communicatieverlies,
noodstop, ongeldige feedback of te hoge stroom stoppen de motor.

## Als iets niet werkt

- **Geen COM-poort:** probeer een andere USB-datakabel en installeer bij een
  clone eventueel de CH340-driver.
- **Uploadfout:** kies ATmega328P (Old Bootloader).
- **Geen INA228:** controleer A4/A5, 5 V, GND en adres 0x40.
- **Enable wordt geweigerd:** lees de rode foutbalk; los E-stop, feedback- of
  INA228-fout eerst op en klik daarna Reset fault.
- **Motor doet niets:** controleer ENABLE, actieve mode, PWM, foutstatus,
  gezamenlijke GND en de motorvoeding.
- **Onverwachte beweging:** druk de fysieke noodstop; onderzoek pas daarna de
  motor- en feedbackrichting.

