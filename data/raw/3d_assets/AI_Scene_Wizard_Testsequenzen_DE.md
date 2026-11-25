# AI Scene Wizard – Testsequenzen für neun Industrieassets

Die folgenden kurzen Abläufe sind fiktive Geräteanleitungen für die erstellten Testassets. Sie dienen der Erzeugung von Hub-Sequenzen und sind keine freigegebenen Betriebs- oder Wartungsanweisungen realer Geräte. Bauteilnamen wurden mit den Node-Listen der GLBs abgeglichen.

## Verwendung im Entwicklungstest

Für einen Test der natürlichen Anweisungsverarbeitung erhält der Wizard den jeweiligen Assetnamen, die Ausgangslage und ausschließlich die nummerierten Anleitungsschritte. Die Umsetzungshinweise und Node-Zuordnungen dienen den Entwicklern als Referenz. So wird nicht bereits durch die Eingabe vorgegeben, welcher Node auszuwählen ist.

**Umsetzungsvorschläge:** „Auswählen/Highlight“, „Greifen/Platzieren“, „Drehen“, „Verschieben“, „Animation“ und „UI/Voiceover“ beschreiben beabsichtigtes Hub-Verhalten, keine verifizierten technischen Step-Type-IDs. Direkte Interaktionen auf Subnodes sind Gegenstand eurer Erweiterung; ihre Verfügbarkeit und Bedienung sind pro Runtime zu prüfen. Falls eine direkte Bewegung nicht unterstützt wird, kann eine Auswahl eine vordefinierte Animation auslösen. Eine solche Ersatzumsetzung im Testergebnis ausdrücklich benennen.

**Gemeinsame Einrichtung:** Pro Asset eine lineare Sequenz. Platzierung zu Beginn abschließen; bei Entnahme eine sichtbare Ablageposition neben dem Gerät vorsehen. Anfangstransformationen speichern und beim Wiederholen zurücksetzen. Für Serviceabläufe werden stillgesetzte, gegen Wiederanlauf gesicherte und – soweit zutreffend – spannungsfreie bzw. drucklose Geräte vorausgesetzt; diese Zustände werden in der Testszene erklärt, nicht technisch nachgewiesen. Keine zusätzlichen Werkzeuge sind erforderlich: Schraubvorgänge dürfen durch Auswahl und Animation dargestellt werden.

Fronttasten und Displays besitzen in den Assets keine hinterlegte Funktion. Die unten genannten Funktionen sind **Festlegungen für den jeweiligen Test**, keine vorhandene Gerätefunktion. Statusmeldungen erscheinen als Hub-UI am Display; eine dynamische Änderung der Displaytextur ist nicht erforderlich. Wartephasen werden für den Test auf wenige Sekunden verkürzt und entsprechend angekündigt.

Genau zwei Schritte sind als **Erweiterungstest** vorgesehen: Mikroskop, Schritt 5, und Kettenantrieb, Schritt 6. Die übrigen Schritte sollen mit einfachen Auswahl-, Transformations-, Animations- und UI-Abläufen realisiert werden.

## 01 – Laborzentrifuge: Proben einsetzen und Testlauf durchführen

**Asset:** `01_laboratory_centrifuge.glb`  
**Ausgangslage:** Deckel geschlossen, Rotor steht, alle acht Probenbehälter eingesetzt. Die linke der beiden Fronttasten startet im Test den Demonstrationslauf; die rechte Taste wird nicht benötigt.

1. Öffnen Sie den Gerätedeckel vollständig.
2. Entnehmen Sie den markierten Probenbehälter und stellen Sie ihn auf der Ablage ab.
3. Entnehmen Sie den gegenüberliegenden Probenbehälter und stellen Sie ihn daneben ab.
4. Setzen Sie beide Probenbehälter wieder in ihre ursprünglichen Rotoraufnahmen ein.
5. Prüfen Sie, ob alle Probenbehälter eingesetzt sind.
6. Schließen Sie den Gerätedeckel.
7. Drücken Sie die linke der beiden Fronttasten, um den Testlauf zu starten.
8. Warten Sie, bis die Anzeige „Testlauf abgeschlossen“ erscheint und der Rotor stillsteht.
9. Öffnen Sie den Gerätedeckel.
10. Entnehmen Sie den zuerst eingesetzten Probenbehälter und stellen Sie ihn auf der Ablage ab.

**Referenz für Entwickler**

- Deckel: `Lid_Assembly`; Öffnung um den vorhandenen Scharnierpivot, etwa 100°. Nicht nur die Deckeloberfläche bewegen.
- Behälterpaar: `Rotor_Assembly/Sample_Tube_1` und `Sample_Tube_5`. Gegenüberliegende Positionen markieren; immer die Behältergruppe inklusive Kappe bewegen. Schritt 4 erfordert zwei abgeschlossene Platzierungen.
- Schritt 5: visuelle Prüfung mit Highlight der eingesetzten Behälter und UI-Bestätigung; keine zusätzliche technische Prüfung behaupten.
- Start: `Control_Panel/Control_Button_0`. Die Taste befindet sich links von `Control_Button_1`, aber rechts vom Display.
- Lauf: optional Rotation der gesamten `Rotor_Assembly` inklusive eingesetzter Behälter als Animation; danach stoppen. Status über UI an `LCD_Display`. Öffnen erst nach dem Warte-/Animationsschritt freigeben.

## 02 – Labor-Inkubator: Einschub entnehmen und einsetzen

**Asset:** `02_laboratory_incubator.glb`  
**Ausgangslage:** Beide Türen geschlossen; Gerät für die Demonstration außer Betrieb. Alle drei Einschübe eingesetzt.

1. Öffnen Sie die äußere Gerätetür vollständig.
2. Öffnen Sie die innere Glastür vollständig.
3. Ziehen Sie den mittleren Einschub aus dem Innenraum und legen Sie ihn auf der Ablage ab.
4. Kontrollieren Sie die freiliegenden Auflagebereiche auf sichtbare Rückstände.
5. Identifizieren Sie den Umluftventilator an der Rückwand.
6. Setzen Sie den Einschub wieder auf seiner ursprünglichen Höhe ein und schieben Sie ihn vollständig zurück.
7. Schließen Sie zuerst die innere Glastür.
8. Schließen Sie anschließend die äußere Gerätetür.

**Referenz für Entwickler**

- Türen: `Outer_Door_Assembly`, anschließend `Inner_GlassDoor_Assembly`; vorhandene Drehpunkte verwenden.
- Mittlerer Einschub: `Tray_2_Assembly`, als vollständige Gruppe entlang der Einschubrichtung bewegen.
- Schritt 4: Auflagebereich markieren und Prüfung per UI bestätigen lassen. Das Asset enthält keine Verschmutzung. Die modellierten `Tray_2_Rail_*` gehören zum beweglichen Einschub und dürfen nicht als fest im Schrank verbleibende Schienen behandelt werden.
- Ventilator: `Circulation_Fan_Assembly`; Auswahl/Highlight, keine Rotation erforderlich.
- Schritt 6: definierte Zielposition; anschließend Türen in der angegebenen Reihenfolge schließen.

## 03 – Mikroskop: Probe betrachten und Vergrößerung wechseln

**Asset:** `03_laboratory_microscope.glb`  
**Ausgangslage:** Objektträger liegt bereits auf dem Objekttisch. Die Testszene beginnt mit dem 10×-Objektiv in Beobachtungsposition; diese Stellung muss vorab anhand des Modells eingerichtet werden.

1. Identifizieren Sie den Objektträger auf dem Objekttisch.
2. Identifizieren Sie das aktuell verwendete 10×-Objektiv.
3. Identifizieren Sie den Grobtrieb an der Seite des Mikroskops.
4. Drehen Sie den Objektivrevolver, bis das 40×-Objektiv die Beobachtungsposition erreicht.
5. Drehen Sie den Feintrieb langsam, bis das angezeigte Probenbild scharf erscheint.
6. Lesen Sie die angezeigte Objektivvergrößerung ab und bestätigen Sie „40×“.
7. Drehen Sie den Objektivrevolver zurück auf das 10×-Objektiv.

**Referenz für Entwickler**

- Schritt 1: `Stage_Assembly/Specimen_Slide_Assembly`; Auswahl/Highlight, kein Verschieben unter den feststehenden Klammern.
- Objektive: `Objective_10x_Assembly`, `Objective_40x_Assembly`; zum Wechsel den gemeinsamen Parent `Objective_Turret_Assembly` drehen. Zielstellungen visuell bestimmen, nicht blind von einem Standardwinkel ausgehen.
- Grob-/Feintrieb: z. B. `Focus_Controls/Coarse_Focus_1` und `Fine_Focus_1`; Seite durch Highlight eindeutig machen.
- **Schritt 5 – Erweiterungstest:** Kontinuierliche Kopplung der Drehbewegung an die Schärfe eines zusätzlichen Probenbilds. Das GLB enthält weder dieses Bild noch eine optische Simulation. Bild und Schärfeverarbeitung müssten ergänzt werden; technische Machbarkeit offen.
- **Einfache Ersatzumsetzung für Schritt 5:** Auswahl des Feintriebs löst eine kurze Drehanimation aus; UI wechselt von einer vorbereiteten unscharfen zu einer scharfen Darstellung. Dies ist keine kontinuierliche Fokussierung.
- Schritt 6: UI zeigt ausdrücklich die Objektivvergrößerung, nicht die Gesamtvergrößerung des Mikroskops.

## 04 – Magnetrührer: Bedienelemente und Rührfunktion kennenlernen

**Asset:** `04_magnetic_stirrer.glb`  
**Ausgangslage:** Becherglas mit Flüssigkeit und Rührstäbchen steht auf der Heizplatte. Temperaturfühler ist eingetaucht. Beide Regler stehen in der für den Test festgelegten Aus-Stellung; keine Heizfunktion aktiv.

1. Identifizieren Sie die Heizplatte unter dem Becherglas.
2. Identifizieren Sie das Rührstäbchen im Becherglas.
3. Identifizieren Sie den Temperaturfühler und seine Halterung am Stativ.
4. Drehen Sie den rechten Regler auf die markierte Rührstellung.
5. Beobachten Sie die Drehbewegung des Rührstäbchens.
6. Drehen Sie den rechten Regler zurück in die Aus-Stellung.
7. Warten Sie, bis das Rührstäbchen stillsteht.
8. Identifizieren Sie den linken Temperaturregler. Belassen Sie ihn in der Aus-Stellung.

**Referenz für Entwickler**

- Auswahl/Highlight: `Ceramic_HeatingPlate`, `Beaker_Assembly/Magnetic_Stir_Bar`, `Temperature_Probe_Assembly` und `Probe_Clamp_Assembly`.
- Rechter Regler: `StirringSpeed_Knob`; linker Regler: `Temperature_Knob`. Aus- und Rührstellung für die Sequenz definieren und mit UI markieren; keine erfundenen physikalischen Sollwerte nötig.
- Regler um ihre Mittelachse drehen. Die `Knob_Indicator_*` sind separate Geschwistermeshes und müssen der jeweiligen Reglerbewegung zugeordnet werden.
- Schritt 5: Rührstäbchen als starres Mesh um seine Mitte rotieren lassen; Originalpivot prüfen bzw. Animationspivot setzen. Keine Flüssigkeitsverformung oder Wirbelsimulation erforderlich.
- Temperaturfühler und Kabel bleiben ortsfest. Dadurch wird keine nicht vorhandene Kabelverformung vorausgesetzt.

## 07 – Säulenbohrmaschine: Tisch positionieren und Vorschub demonstrieren

**Asset:** `07_pillar_drill_press.glb`  
**Ausgangslage:** Maschine stillgesetzt, Spindel steht. Kein Werkstück eingespannt. Demonstration ohne Motorlauf und ohne Materialbearbeitung.

1. Identifizieren Sie die Schutzhaube vor dem Bohrfutter.
2. Lösen Sie den Klemmhebel am Bohrtisch.
3. Verschieben Sie den Bohrtisch nach unten bis zur markierten Position.
4. Stellen Sie den Klemmhebel wieder in die Verriegelungsstellung.
5. Betätigen Sie den Vorschubhebel bis zur markierten Stellung und beobachten Sie die Abwärtsbewegung der Spindel.
6. Führen Sie den Vorschubhebel in seine Ausgangsstellung zurück.
7. Öffnen Sie die obere Riemenabdeckung.
8. Identifizieren Sie die beiden Riemenscheiben und den Antriebsriemen.
9. Schließen Sie die Riemenabdeckung.

**Referenz für Entwickler**

- Schutz: `Chuck_Guard_Assembly`; nur auswählen/markieren.
- Klemmung: `Work_Table_Assembly/Table_LockLever`; „gelöst“ und „verriegelt“ durch kurze Drehanimationen darstellen. Pivot muss gegebenenfalls gesetzt werden; keine Gewindesimulation.
- Tisch: `Work_Table_Assembly` inklusive Klemme verschieben. Zielhöhe so festlegen, dass für den folgenden Vorschub ausreichend Abstand bleibt.
- Vorschub: `Feed_Lever_Assembly` und `Spindle_Assembly` mit zwei abgestimmten, vordefinierten Animationen bewegen. Kein kontinuierliches mechanisches Constraint nötig; keine Bohrerspin-Animation, da Maschine stillsteht. Vorwärts- und Rückwärtsbewegung jeweils als eigener Schritt.
- Obere Abdeckung: `Belt_Cover_Assembly`. Sichtbare Zielstellung vorab prüfen; das vereinfachte Modell enthält keinen ausgearbeiteten Scharniermechanismus.
- Riemenantrieb: `Drive_Pulley_0`, `Drive_Pulley_1` und beide `Drive_Belt_Straight_*` gemeinsam markieren. Der Riemen ist vereinfacht modelliert und muss nicht umlaufen.

## 13 – Elektromotor mit Kupplung: Bauteile und Lüfter kennenlernen

**Asset:** `13_electric_motor_coupling.glb`  
**Ausgangslage:** Motor spannungsfrei und gegen Wiederanlauf gesichert. Welle und Lüfter stehen.

1. Identifizieren Sie den Anschlusskasten auf der Oberseite des Motors.
2. Identifizieren Sie die Kühlrippen des Motorgehäuses.
3. Nehmen Sie die rückseitige Lüfterabdeckung ab und legen Sie sie auf der Ablage ab.
4. Kontrollieren Sie die freigelegten Lüfterflügel auf sichtbare Beschädigungen.
5. Setzen Sie die Lüfterabdeckung wieder in ihre ursprüngliche Position ein.
6. Identifizieren Sie die Antriebswelle und die darauf angeordnete Passfeder.
7. Identifizieren Sie das elastische Verbindungselement zwischen den beiden Kupplungshälften.
8. Bestätigen Sie den Abschluss der Bauteilkontrolle.

**Referenz für Entwickler**

- Anschlusskasten: `Terminal_Box_Assembly`; geschlossen lassen. Sein Innenraum enthält keine ausgearbeiteten elektrischen Anschlüsse.
- Kühlrippen: `Cooling_Fin_*` unter `Motor_Assembly` gemeinsam markieren.
- Abdeckung: `Motor_Assembly/Fan_Cover_Assembly` axial vom Motor wegbewegen; komplette Gruppe inklusive Schutzgitter. Das Modell besitzt dafür keine separat modellierten Befestigungsschrauben; keine Schraubschritte hinzufügen.
- Lüfter: `Cooling_Fan_Assembly`; Flügel markieren und Prüfung per UI bestätigen. Es ist kein Schaden modelliert.
- Welle/Passfeder: `Drive_Shaft_Assembly` und dessen `Shaft_Key`; nacheinander markieren.
- Elastisches Element: `Flexible_Coupling_Assembly/Coupling_Elastomer`; keine Kupplungsdemontage nötig.

## 14 – Kettenantrieb: Schutzabdeckung und Kraftübertragung

**Asset:** `14_chain_drive_module.glb`  
**Ausgangslage:** Antrieb steht und ist gegen Wiederanlauf gesichert. Schutzabdeckung montiert. Ein späterer Lauf wird ausschließlich als virtuelle Funktionsdarstellung gezeigt.

1. Lösen Sie die vier Befestigungsschrauben der Schutzabdeckung.
2. Ziehen Sie die Schutzabdeckung nach vorn ab und legen Sie sie auf der Ablage ab.
3. Identifizieren Sie das Antriebskettenrad und anschließend das Umlenkkettenrad.
4. Kontrollieren Sie das markierte Kettenglied sowie dessen Bolzen und Rolle.
5. Setzen Sie die Schutzabdeckung wieder ein und befestigen Sie sie mit den vier Schrauben.
6. Starten Sie die virtuelle Funktionsdarstellung und beobachten Sie die umlaufende Kette sowie beide Kettenräder.
7. Beenden Sie die Funktionsdarstellung und warten Sie bis zum vollständigen Stillstand.

**Referenz für Entwickler**

- Schrauben: `Safety_Guard_Assembly/Guard_Screw_1` bis `Guard_Screw_4`. Für diesen Test nur lösen, nicht vollständig entnehmen; sie bleiben mit der Abdeckung verbunden.
- Abdeckung: gesamte `Safety_Guard_Assembly` bewegen. Zum Wiederanbringen gespeicherte Ausgangstransformation verwenden.
- Kettenräder: `Drive_Sprocket_Assembly`, `Idler_Sprocket_Assembly`.
- Kettenglied: `Roller_Chain_Assembly/Chain_Link_01_Assembly`; anschließend dessen `Link_Pin` und `Link_Roller`. Im Modell sind 64 einzelne Gliedgruppen vorhanden.
- **Schritt 6 – Erweiterungstest:** Eine geschlossen umlaufende Kette mit plausibel gekoppelten Kettenrädern. Das Asset enthält weder diese Animation noch ein Kettenrig. Die gemeinsame Rotation aller Kettenglieder als ein starrer Körper erfüllt diesen Schritt nicht.
- **Einfache Ersatzumsetzung:** Kette statisch lassen, Bewegungsrichtung mit Guidance/Highlight erläutern und die Kraftübertragung per UI beschreiben. Als Ersatz kennzeichnen, nicht als erfolgreiche Kettensimulation werten.
- Start/Stopp über Hub-UI; am Asset ist kein Schalter modelliert.

## 17 – Rohrleitungsmodul: Filtereinsatz entnehmen und einsetzen

**Asset:** `17_pipe_valve_filter_module.glb`  
**Ausgangslage:** Modul abgesperrt, drucklos und entleert. Die Drucklosigkeit ist ein vorgegebener Zustand der Simulation; der Manometerzeiger wird für den Test auf die markierte Nullstellung gesetzt. Ventile werden während dieses Serviceablaufs nicht betätigt.

1. Identifizieren Sie das Handradventil und den Kugelhahn.
2. Lesen Sie den Druck am Manometer ab und bestätigen Sie die angezeigte Nullstellung.
3. Lösen Sie die sechs Schrauben des Filterdeckels.
4. Heben Sie den Filterdeckel ab und legen Sie ihn auf der Ablage ab.
5. Ziehen Sie den Filtereinsatz nach oben aus dem Filtergehäuse.
6. Kontrollieren Sie den Filtereinsatz auf sichtbare Beschädigungen.
7. Setzen Sie den Filtereinsatz wieder in das Filtergehäuse ein.
8. Setzen Sie den Filterdeckel auf und befestigen Sie ihn wieder mit den sechs Schrauben.

**Referenz für Entwickler**

- Ventile: `Handwheel_Valve_Assembly`, `Ball_Valve_Assembly`; Auswahl/Highlight genügt.
- Manometer: `Pressure_Gauge_Assembly`. Der vorhandene Zeiger steht nicht automatisch auf null; dessen Transformation vorab setzen und „0 bar – Simulationszustand“ als Hub-UI anzeigen. Die Auswahl allein ist kein realer Drucknachweis.
- Deckel: `Filter_Lid_Assembly`; Schrauben `Filter_Lid_Screw_1` bis `Filter_Lid_Screw_6`. Nur lösen, in diesem vereinfachten Test am Deckel belassen und gemeinsam mitführen.
- **Wichtige Gruppierung für Schritte 5–7:** `Filter_Cartridge` und `Filter_Pleat_0` bis `Filter_Pleat_19` liegen als Geschwister unter `Filter_Assembly`. Gemeinsam bewegen bzw. in der Szene zu einer Interaktionsgruppe zusammenfassen. Nur `Filter_Cartridge` zu bewegen würde die Filterfalten im Gehäuse zurücklassen.
- Sichtprüfung per Highlight und UI-Bestätigung; keine Schäden oder Verschmutzung modelliert. Keine Reinigungssimulation erforderlich.

## 18 – Schaltschrank: Komponenten identifizieren und Kabelkanal öffnen

**Asset:** `18_electrical_control_cabinet.glb`  
**Ausgangslage:** Schaltschrank freigeschaltet, gegen Wiedereinschalten gesichert und Spannungsfreiheit bereits festgestellt. Tür geschlossen und entriegelt. Kein Einschalten innerhalb der Sequenz.

1. Öffnen Sie die Schaltschranktür vollständig.
2. Identifizieren Sie den linken Leitungsschutzschalter in der oberen Reihe.
3. Identifizieren Sie das mittlere Schütz in der darunterliegenden Reihe.
4. Identifizieren Sie die linke Reihenklemme in der unteren Reihe.
5. Nehmen Sie die Abdeckung des linken Kabelkanals ab und legen Sie sie auf der Ablage ab.
6. Identifizieren Sie die sichtbaren Leitungen im Schaltschrank.
7. Setzen Sie die Kabelkanalabdeckung wieder in ihre ursprüngliche Position ein.
8. Schließen Sie die Schaltschranktür.

**Referenz für Entwickler**

- Tür: `Cabinet_Door_Assembly`, vorhandenen Scharnierpivot verwenden. Das Schloss wird nicht betätigt; Schlüssel ist keiner vorhanden.
- Linker Leitungsschutzschalter: `DIN_Rail_3_Assembly/Circuit_Breaker_1_Assembly`.
- Mittleres Schütz: `DIN_Rail_2_Assembly/Contactor_2_Assembly`.
- Linke Reihenklemme: `DIN_Rail_1_Assembly/Terminal_Block_01`.
- Linker Kanaldeckel: `Cable_Duct_1_Assembly/Cable_Duct_Cover_Assembly_1`; nur den Deckel, nicht den gesamten Kabelkanal bewegen.
- Leitungen: `Internal_Wiring`; Auswahl/Highlight. Kabelverlauf ist vereinfacht und statisch, der Kanalinnenraum nicht detailliert ausgearbeitet. Keine Kabel herausziehen, Anschlüsse lösen oder Leiterverformung erwarten.

## Kurze Auswertung pro erzeugter Sequenz

- Wurden aus der manuellen Beschreibung die richtigen Bauteile und Hierarchieebenen ausgewählt?
- Bewegen sich zugehörige Teile gemeinsam, während feststehende Teile an Ort und Stelle bleiben?
- Stimmen Reihenfolge, Anfangszustand, Bewegungsrichtung und Rückkehrpositionen?
- Werden Anzeige- und Wartezustände explizit dargestellt, statt als bereits vorhandene Gerätefunktion angenommen?
- Erkennt und kennzeichnet der Wizard fehlende Voraussetzungen bzw. die beiden Erweiterungstests, ohne eine nicht vorhandene Simulation zu behaupten?
