# VAV check - podglad sterownika VAV po BACnet + reguly wyliczajace

Jednoplikowa aplikacja webowa (stdlib, zero zaleznosci w symulacji).

```bash
# symulacja logiki programu (bez sprzetu), UI na http://localhost:8342
python3 vavcheck.py --sim

# realny sterownik (Mac w tej samej sieci co sterownik)
pip install BAC0
python3 vavcheck.py --addr <ip-sterownika> --devid <device-id> [--ip <ip-maca>/24]
```

- Tabela punktow na zywo (odswiezanie 1.5 s), zapisywalne parametry edytuje sie
  wprost w tabeli.
- "Reguly" to blok JS edytowany w przegladarce (zapis w localStorage): dostaje
  obiekt `p` z punktami i zwraca wiersze [opis, oczekiwane, odczytane, czyOK].
  Domyslne reguly odtwarzaja opis programu: rampa CO2 (qmin -> qmax w pasmie
  PropBand do nastawy), wyciag = nawiew - DIFF, przeplyw z predkosci i wymiarow.
- Po pierwszym podlaczeniu realnego sterownika przepisz nazwy punktow BACnet do
  `MAPPING` w vavcheck.py (alias -> krotkie nazwy uzywane w regulach).
