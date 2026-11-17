# Terra G4UT — Android

Android-versie van de Terra G4UT A-scan inspector (UT-SPI). Zelfde meetlogica als de Windows-app, in Python
(Chaquopy) binnen een Android-app; de bediening is een web-UI in een WebView.

```
core/g4ut_core/   Python-kern: UDP (Teensy), verwerking, auto-gate/gain, projecten (CSV), upload 3D Inspect
web/              UI (HTML/JS/CSS) — identiek op Android en desktop
android/          Android-app (Kotlin + Chaquopy), bouwt de kern en de web-map mee
desktop/          dev_server.py (zelfde UI op laptop) + sim_teensy.py (nep-Teensy voor testen)
```

## APK
Elke push naar `main` bouwt via GitHub Actions een APK (Actions → artifact *TerraG4UT-apk*).
Een tag `v1.0.0` maakt een Release met de APK erbij. Installeren: APK naar de tablet, "onbekende bronnen" toestaan.

Netwerk: de tablet moet via de Herelink-airunit (USB-C) een `192.168.144.x`-adres krijgen; de Teensy staat op
`192.168.144.50` (UDP 5005 data, 5006 commando's, 5007 telemetrie).

Data op de tablet: `Android/data/com.terra.g4ut/files/g4ut/` (settings.json, data/<project>.csv, screenshots).

## Desktop / testen
```
pip install numpy requests aiohttp
python desktop/sim_teensy.py --thickness 9.8        # nep-Teensy (optioneel)
python desktop/dev_server.py --teensy 127.0.0.1     # of --teensy 192.168.144.50
# open http://localhost:8080
```
