# bacnet-check

Uniwersalny webowy podglad urzadzen BACnet/IP z **profilami regul** liczacych
"oczekiwane vs odczytane". Jeden plik, biblioteka standardowa Pythona; BAC0
potrzebne dopiero przy realnym sprzecie.

Zaleznosci sa zapisane w naglowku `bacnet_check.py` (PEP 723), wiec **uv sam
tworzy srodowisko** - nie ma czego instalowac ani zadnego venv do pilnowania.

```bash
# symulowane urzadzenie z sims/vav-nefryt.py, bez sprzetu
uv run bacnet_check.py --sim vav-nefryt

# realny BACnet; urzadzenie wybierasz juz w przegladarce
uv run bacnet_check.py [--ip <ip-twojego-komputera>/24]

# skrypt jest wykonywalny, wiec dziala tez tak:
./bacnet_check.py --sim
```

Samo `--sim` (bez nazwy) bierze pierwszy plik z `sims/`.

Nie masz uv? `curl -LsSf https://astral.sh/uv/install.sh | sh`

UI: http://localhost:8342

## Jak to dziala

Sama aplikacja nic nie wie o urzadzeniach. Zna tylko trzy rzeczy:

1. **Punkty** - odczytywane cyklicznie i pokazywane w tabeli; zapisywalne
   edytujesz wprost w tabeli.
2. **Mapowanie** - nazwa punktu w sterowniku na krotki alias uzywany w regulach
   (dzieki temu ta sama regula dziala na sterownikach roznych producentow).
3. **Reguly** - blok JavaScript, ktory dostaje obiekt `p` z aliasami i zwraca
   wiersze `[opis, oczekiwane, odczytane, czyOK]`. Wynik to tabela OK / ROZJAZD.

Mapowanie i reguly razem tworza **profil** (`profiles/*.json`). Profile
edytujesz w przegladarce i zapisujesz na serwerze, wiec da sie je trzymac
w gicie i przenosic miedzy stanowiskami.

## Symulatory (`sims/*.py`)

Symulowane urzadzenie to zwykly plik Pythona, nie czesc aplikacji - kopiujesz
`sims/vav-nefryt.py` i przerabiasz pod swoje urzadzenie. Kontrakt jest krotki:

- `PARAMS` - punkty zapisywalne i ich wartosci poczatkowe
- `UNITS` - jednostki (opcjonalnie)
- `step(t, p, s)` - zwraca punkty tylko do odczytu; `t` to sekundy od startu,
  `p` to biezace parametry, `s` to wlasny stan miedzy krokami

W zestawie jest `vav-nefryt` - odwzorowuje layout sterownika (rampa CO2, tryby,
wyciag nadazny z DIFF), z tymi samymi nazwami punktow co prawdziwe urzadzenie,
wiec profil regul dziala bez zmian na symulacji i na sprzecie.

## Profile w zestawie

- **VAV-nefryt** - sterownik VAV wg opisu programu: rampa CO2 (od nastawa-PropBand
  do nastawy przestawia wydatek z minimalnego na maksymalny), wyciag nadazajacy za
  nawiewem z uwzglednieniem DIFF, oraz kontrola przeplywu policzonego z predkosci
  i wymiarow kanalu wzgledem nastawy.

Nowy profil: **Nowy**, potem wpisz nazwe i **Zapisz profil**.

## Skad sie biora nazwy punktow

Punkt to obiekt w sterowniku (analogowy, binarny, wieloznaczny) i **nazwe nadaje
mu programista sterownika**, nie ta aplikacja. Po polaczeniu zobaczysz w tabeli
dokladnie to, co siedzi w urzadzeniu - czasem czytelne `SupplyFlowSetpoint`,
czasem `AV12`. Dlatego jest mapowanie: przepisuje nazwy z konkretnego sterownika
na krotkie aliasy uzywane w regulach, wiec ta sama regula chodzi na sterownikach
roznych producentow.

Kolejnosc pracy: polacz sie, kliknij **Wypelnij z punktow** (wciaga wszystkie
nazwy z urzadzenia), popraw prawa strone na swoje aliasy, napisz reguly, zapisz
profil pod nazwa urzadzenia.

## Uwaga do profilu VAV

Reguly **usredniaja** dwa czujniki predkosci w tym samym kanale. Jesli w danym
ukladzie kazdy czujnik siedzi w osobnym kanale, przeplywy trzeba liczyc osobno
(kazdy ze swoim przekrojem) i dopiero sumowac - popraw regule dla takiego ukladu.
