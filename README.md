# bacnet-check

Uniwersalny webowy podglad urzadzen BACnet/IP z **profilami regul** liczacych
"oczekiwane vs odczytane". Jeden plik, biblioteka standardowa Pythona; BAC0
potrzebne dopiero przy realnym sprzecie.

```bash
# symulowane urzadzenie (VAV), bez sprzetu - dziala od reki
python3 bacnet_check.py --sim

# realny BACnet; urzadzenie wybierasz juz w przegladarce
pip install BAC0
python3 bacnet_check.py [--ip <ip-twojego-komputera>/24]
```

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

## Profile w zestawie

- **vav** - sterownik VAV wg opisu programu: rampa CO2 (od nastawa-PropBand do
  nastawy przestawia wydatek z minimalnego na maksymalny), wyciag nadazajacy za
  nawiewem z uwzglednieniem DIFF, oraz kontrola przeplywu policzonego z predkosci
  i wymiarow kanalu wzgledem nastawy.
- **przyklad-ogolny** - szablon startowy dla dowolnego innego urzadzenia.

Nowy profil: wybierz szablon, popraw mapowanie i reguly, wpisz nazwe, Zapisz.

## Uwaga do profilu VAV

Reguly **usredniaja** dwa czujniki predkosci w tym samym kanale. Jesli w danym
ukladzie kazdy czujnik siedzi w osobnym kanale, przeplywy trzeba liczyc osobno
(kazdy ze swoim przekrojem) i dopiero sumowac - popraw regule dla takiego ukladu.
