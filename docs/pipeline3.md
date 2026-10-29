# Pipeline 3: Teile mit Vision-Modell beschreiben – Vorversuche

Stand: 27.10.2026 · Branch `pipeline-3-caption` · Nico

Ziel von Pipeline 3 (Notion-Aufgabe): Ein Vision-Modell bekommt die 8 Renders eines Teils aus Pipeline 2 und
liefert strukturiert Name, Typ, Funktion, Beschreibung und Confidence. Zuerst wurde von Hand getestet, ob ein
**lokales Modell** dafür reicht (Abschnitte bis Befund 9), danach mit `caption.py` Gemini und Qwen verglichen.

## Testaufbau

| | |
|---|---|
| Hardware | MacBook, Apple M4, 16 GB RAM |
| Laufzeit | Ollama 0.40.11 (`brew install ollama`, läuft als Hintergrunddienst) |
| Modell | `qwen2.5vl:7b` (6,0 GB, Apache-2.0) – größtes Qwen-VL-Modell, das in 16 GB passt |
| Daten | Pipeline 1 über alle Assets (1.114 Teile), Pipeline 2 `part` + `highlight` für `04_torque_wrench` (5 Teile) und `04_magnetic_stirrer` (36 Teile) |
| Prompt | Bilder + „What part is it?“ + Asset-Typ als Kontext (z. B. „torque wrench“), **ohne Knotenname** |

## Messungen

| Eingabe | Tokens | Dauer pro Teil | Teil (Soll) | Antwort Qwen |
|---|---|---|---|---|
| 1 Einzelbild 512 px | – | 64 s (inkl. Modell laden) | Handle | ✅ „handle“ |
| 8 Einzelbilder 512 px | 8.250 | 179–200 s | Ratchet_Head | ❌ „handle“ |
| 8 Einzelbilder auf 256 px verkleinert | 8.250 | 183 s | Ratchet_Head | ❌ „handle“ |
| 1 Raster (4×2 Views à 256 px) + 1 Highlight-Raster | 2.177 | 45 s | Ratchet_Head | ❌ „handle“ |
| 1 Raster + 1 Highlight-Raster | 2.178 | 44 s | Temperature_Knob | ❌ „stir bar“ |

Trefferquote bei Teilen, die nicht trivial sind: **0 von 3** (streng nach Name; abgestufte Bewertung mit mehr Teilen
siehe „Vergleich Gemini vs. Qwen“ weiter unten).

## Befunde

1. **Kontextlimit:** Ollama erlaubt standardmäßig 4.096 Tokens. 8 Bilder brauchen ~8.250 → Fehler
   `request exceeds the available context size`. Lösung: `options.num_ctx` pro Anfrage hochsetzen (getestet: 12.288).
2. **Verkleinern bringt nichts:** Ollama skaliert die Bilder für Qwen intern selbst, 256 px kosten genauso viele
   Tokens wie 512 px.
3. **Rasterbild statt Einzelbilder:** Die 8 Views als ein Bild (4×2) senken die Tokens um ~75 % und die Dauer
   von ~180 s auf ~45 s pro Teil. Hochgerechnet auf alle 1.114 Teile: ~14 h statt ~55 h.
4. **Qualität reicht nicht:** Qwen 7B benennt Teile fast immer nach dem auffälligsten Bauteil des Assets
   („handle“, „stir bar“), auch wenn das Highlight klar etwas anderes zeigt.
5. **Die Bilder selbst reichen aus:** Zum Vergleich wurden dieselben Raster von Claude (in Claude Code) angesehen:
   - Ratchet_Head: runder Kopf am Ende des Schlüssels mit Umschalthebel → als Ratschenkopf erkennbar.
   - Temperature_Knob: isoliert nur ein schwarzer Zylinder, im Highlight vorne am Gehäuse → als Bedienknopf
     erkennbar, aber **nicht**, ob Temperatur oder Drehzahl (zwei gleich aussehende Knöpfe).
   Das Problem ist also das Modell, nicht die Renders.
6. **Mehrdeutigkeit ist real:** Gleich aussehende Teile (zwei Drehknöpfe, vier Gummifüße, zwei Gehäuseschrauben)
   lassen sich aus Bildern allein nicht unterscheiden. Das betrifft Forschungsfrage Q4 und spricht dafür, dass die
   Confidence im Output wichtig ist.
7. **Isolierte Teile sind oft nicht eindeutig:** Ohne Highlight ist der Ratschenkopf nur ein grauer Zylinder.
   `input_views: part+highlight` sollte der Standard sein.
8. **Kleine Teile:** Pipeline 2 warnt bei 10 von 36 Teilen des Magnetrührers (< 5 % der Asset-Größe). Im Highlight
   sind sie nur wenige Pixel groß (z. B. Rührfisch). Deckt sich mit der offenen Frage aus dem Weekly vom 22.10.
9. **Vorsicht Datenleck:** Die Render-Ordner heißen nach dem Knotennamen (`04_magnetic_stirrer__Magnetic_Stir_Bar`).
   Diese IDs dürfen nie in den Prompt, sonst liest das Modell die Lösung ab.

## Vergleich Gemini vs. Qwen mit `caption.py` (27.10.2026)

Gleicher Prompt und gleiche Eingabe (Rasterbild Teil + Rasterbild Highlight, `input_views: part+highlight`) für beide
Modelle. Gemini über die API im Gratis-Tarif (`gemini-3.5-flash`, wird von Google mit `gemini-3.6-flash` bedient,
weil 3.8 Flash überlastet war), Qwen lokal über Ollama. Ergebnisse lokal unter
`data/processed/cap3d/captions/<modell>/`.

**Bewertung (von Hand, abgestuft):** Es zählt, ob das Teil sinnvoll erkannt wurde, nicht der exakte Knotenname.
- **2 = richtig:** das richtige Teil, Name darf allgemeiner sein (z. B. „handle grip“ für `Handle`)
- **1 = teilweise:** richtige Region oder Funktion, aber zu grob oder ein Nachbarteil
  (z. B. „base“ für einen Gummifuß unter dem Sockel)
- **0 = falsch:** ein anderes Teil

| Teil (Knotenname) | Gemini 3.6 Flash | | Qwen2.5-VL 7B | |
|---|---|---|---|---|
| torque_wrench / Handle | handle grip | 2 | Handle | 2 |
| torque_wrench / Ratchet_Head | ratchet head housing | 2 | Torque Wrench Handle | 0 |
| torque_wrench / Reversing_Lever | direction lever | 2 | Torque Wrench Handle | 0 |
| torque_wrench / Shaft | internal lever shaft | 2 | Torque Wrench Handle (Schaft steckt im Griff → Nachbarteil) | 1 |
| torque_wrench / Square_Drive | square drive lug | 2 | Torque Wrench Handle | 0 |
| magnetic_stirrer / Beaker_Assembly | beaker with liquid and stir bar | 2 | Magnetic Stirrer Blade | 0 |
| magnetic_stirrer / Magnetic_Stir_Bar | – (Tageslimit) | | Magnetic Stirrer Arm („holds the stir bar … to stir the liquid“) | 1 |
| magnetic_stirrer / Rubber_Foot_1 | – (Tageslimit) | | Magnetic Stirrer Base („stable platform“) | 1 |
| magnetic_stirrer / Stirrer_Housing | – (Tageslimit) | | Magnetic Stirrer Blade | 0 |
| magnetic_stirrer / Temperature_Knob | – (Tageslimit) | | Magnetic Stirrer Base | 0 |
| magnetic_stirrer / Beaker_GlassWall | glass beaker | 2 | – | |
| magnetic_stirrer / Beaker_Rim | beaker rim | 2 | – | |
| magnetic_stirrer / Ceramic_HeatingPlate | heating plate | 2 | – | |
| magnetic_stirrer / Beaker_Bottom | liquid sample (Region stimmt) | 1 | – | |
| magnetic_stirrer / Beaker_Graduation_0 … _4 (5 Teile) | magnetic stir bar | 0 (×5) | – | |

| | Gemini 3.6 Flash | Qwen2.5-VL 7B |
|---|---|---|
| Teile bewertet | 15 | 10 |
| richtig / teilweise / falsch | 9 / 1 / 5 | 1 / 3 / 6 |
| Punkte | 19 von 30 (63 %) | 5 von 20 (25 %) |
| nur die 6 Teile, die beide haben | 12 von 12 | 3 von 12 |
| Dauer pro Teil | 5–30 s (inkl. Wartezeit bei Überlastung) | 45–80 s |
| Tokens pro Teil | ~2.270 Input, ~60 Output, 400–1.450 Denk-Tokens | ~2.200 Input |

Weitere Befunde:

10. **Qwen erkennt die Richtung, aber nicht das Teil:** Ein Griff wird als Griff erkannt, und bei Rührfisch und
    Gummifuß stimmen Region bzw. Funktion grob. Meist nennt Qwen aber das größte Bauteil des Assets
    („Torque Wrench Handle“ für fast alles). Die Confidence ist immer 0,80 und sagt damit nichts aus.
11. **Gemini ist bei normal großen Teilen sehr gut,** auch bei kleinen Details wie dem Umschalthebel (0,85) oder dem
    Vierkant (Square Drive).
12. **Winzige Teile scheitern auch bei Gemini:** Die 5 Skalenstriche des Becherglases (2,7 % der Asset-Größe) werden
    alle als „magnetic stir bar“ erkannt – im Highlight sind sie nur ein kleiner orangefarbener Strich im Glas,
    genau wie der Rührfisch. Bestätigt Befund 8 (kleine Teile heranzoomen).
13. **Die Confidence ist nicht verlässlich:** Gemini gibt auch bei den falschen Skalenstrichen 0,95 an. Für
    Forschungsfrage Q4 (Unsicherheit erkennen) reicht die selbst angegebene Confidence also nicht.
14. **Gratis-Tarif von Gemini taugt nur für Tests:** maximal **20 Anfragen pro Tag und Modell**
    (Fehler `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). Für alle 1.114 Teile wären das ~56 Tage.
    Dazu kommen häufige Überlastungen (HTTP 503, Timeouts); Gemini 3.8 Flash war am Testtag gar nicht erreichbar.
    `caption.py` wiederholt bei 503 automatisch und bricht beim Tageslimit sofort ab; fertige Teile bleiben erhalten.

## Was das für Pipeline 3 heißt

- Lokal (Ollama) ist technisch machbar, kostenlos und datenschutzfreundlich, mit 16 GB RAM aber zu ungenau.
- Eingabe als Rasterbild (Teil + Highlight) übernehmen – spart bei jedem Modell Tokens und Zeit.
- Ein stärkeres Modell ist nötig. Optionen: ein Cloud-Modell (Anthropic, OpenAI, Google – direkt oder über die
  Cloud von VUSE) oder ein Rechner mit deutlich mehr Speicher für größere lokale Modelle.

## Was man für ein Cloud-Modell bräuchte

1. **Freigabe von VUSE**, dass Renders der CAD-Modelle an einen externen Anbieter gehen dürfen
   (oder Zugang über die Cloud von VUSE, z. B. Claude über AWS Bedrock).
2. **Entwickler-Konto beim Anbieter** mit Guthaben bzw. Zahlungsmittel. Chat-Abos (claude.ai Pro, ChatGPT Plus,
   Gemini Advanced) enthalten **keinen** API-Zugang.
3. **API-Key** – nur lokal als Umgebungsvariable bzw. in `.env` (steht in `.gitignore`), nie im Code oder in der Config.
4. **Python-Paket des Anbieters** über `uv add …` (landet in `pyproject.toml` / `uv.lock`).

## Kostenvergleich Cloud-Modelle

Annahmen pro Teil: 2 Rasterbilder (Teil + Highlight, je 1024×512 px) + Prompt → ~1.800 Tokens Input,
JSON-Antwort inkl. Denkschritte → ~1.000 Tokens Output. Jeder Anbieter zählt Bild-Tokens etwas anders,
die Werte können daher um etwa ±50 % abweichen. Batch = Verarbeitung asynchron (meist < 1 h) mit 50 % Rabatt.
Preise in USD pro 1 Mio. Tokens, abgerufen am 27.10.2026 von den offiziellen Preisseiten.

Verglichen wird jeweils das **stärkste Modell** des Anbieters, weil die Qualität der Beschreibung entscheidet.

| Anbieter | Bestes Modell | Input / Output | 5 Teile | 100 Teile | Alle 1.114 | Alle, Batch | Gratis-Kontingent |
|---|---|---|---|---|---|---|---|
| Anthropic | Claude Fable 5.1 | $10 / $50 | $0,34 | $6,80 | $76 | $38 | nein |
| OpenAI | gpt-6-astra | $10 / $50 | $0,34 | $6,80 | $76 | $38 | nein |
| Google | Gemini 4 Argon | $2 / $10, ab 01.12.26 $4 / $20 | $0,07 | $1,40 | $15, ab 01.12. $30 | $7,60 / $15 | nein |
| Google | Gemini 3.8 Flash (bestes Gratis-Modell) | $0,75 / $3,75 | 0 im Gratis-Tarif | 0, aber 5 Tage (20/Tag) | 0, aber ~56 Tage (20/Tag) | – | **ja** |
| lokal | Qwen2.5-VL 7B (Ollama) | – | 0 | 0 | 0 (~14 h Rechenzeit) | – | – |

Hinweise:
- Bei OpenAI nennt die Preisseite nicht, welche Modelle Bilder annehmen – vor Nutzung in der Modell-Doku prüfen.
- Das Gratis-Kontingent von Google hat Tageslimits pro Projekt (aktuelle Werte nur in AI Studio sichtbar).
  Eingaben im Gratis-Tarif dürfen zur Verbesserung der Google-Produkte verwendet werden.
- Gesamtbudget für Tests und einen kompletten Lauf mit einem Spitzenmodell: **~$40–80** (mit Batch eher die
  untere Hälfte), mit Gemini 4 Argon deutlich weniger. Vor dem großen Lauf die Token-Zahlen nachmessen.

## Vorab testen ohne API-Kosten

| Weg | Kosten | Automatisierbar | Daten gehen an |
|---|---|---|---|
| Rasterbilder von Hand im Chat hochladen (claude.ai Pro, ChatGPT, Gemini-App) | 0 (vorhandene Abos / Gratis-Versionen) | nein | jeweiligen Anbieter |
| Google Gemini API, Gratis-Kontingent | 0 | ja | Google, darf für Training genutzt werden |
| Claude in Claude Code ansehen lassen (wie oben bei Befund 5) | 0 (Pro-Abo) | nein | Anthropic |
| Lokales Modell (Ollama) | 0 | ja | bleibt auf dem Rechner |

Ein Handtest mit ~10 Teilen in 2–3 Chat-Oberflächen zeigt schnell, ob sich ein API-Konto überhaupt lohnt und welcher
Anbieter in Frage kommt.

**Datenfreigabe (27.10.2026):** Die 14 Test-Assets und ihre Renders sind nicht vertraulich und dürfen an externe
Anbieter gehen, auch in Gratis-Tarifen (Aussage Nico). Für echte Kundenmodelle von VUSE gilt das nicht automatisch.

## Offen fürs Weekly

- Test-Assets sind freigegeben. Für spätere echte Kundenmodelle: Dürfen die an externe Anbieter, oder hat VUSE
  eine eigene Cloud (AWS Bedrock, Azure, Google Cloud), über die wir gehen sollten?
- Wer stellt API-Key bzw. Budget (VUSE, DHBW, privat)?
- Kleine Teile beim Highlight-Rendern heranzoomen (Pipeline 2)?
