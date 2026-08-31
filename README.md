# bacnet-check

Uniwersalny webowy podglad urzadzen BACnet/IP z **profilami regul** liczacych
"oczekiwane vs odczytane". Jeden plik, biblioteka standardowa Pythona; BAC0
potrzebne dopiero przy realnym sprzecie.

Zaleznosci sa zapisane w naglowku `bacnet_check.py` (PEP 723), wiec **uv sam
tworzy srodowisko** - nie ma czego instalowac ani zadnego venv do pilnowania.

```bash
# przykladowe urzadzenie, bez sprzetu
uv run bacnet_check.py --sim example-device

# realny BACnet; urzadzenie wybierasz juz w przegladarce
uv run bacnet_check.py [--ip <ip-twojego-komputera>/24]

# wlasne definicje urzadzen trzymane poza narzedziem
uv run bacnet_check.py --sim moj-sterownik --sims ~/moje/sims --profiles ~/moje/profiles
```

Nie masz uv? `curl -LsSf https://astral.sh/uv/install.sh | sh`

UI: http://localhost:8342

## Jak to dziala

Narzedzie nic nie wie o urzadzeniach. Zna tylko trzy rzeczy:

1. **Punkty** - odczytywane cyklicznie i pokazywane w tabeli; zapisywalne
   edytujesz wprost w tabeli (zapis idzie do urzadzenia od razu).
2. **Mapowanie** - nazwa punktu w sterowniku na krotki alias uzywany w regulach,
   dzieki czemu ta sama regula dziala na sterownikach roznych producentow.
   Mapowanie jest opcjonalne: bez niego regula siega wprost po nazwe z urzadzenia.
3. **Reguly** - blok JavaScript, ktory dostaje `p` (biezacy odczyt) i `prev`
   (poprzedni) i zwraca wiersze `[opis, oczekiwane, odczytane, czyOK]`.
   Wynik to tabela OK / ROZJAZD / brak danych.

Mapowanie i reguly razem tworza **profil** (`profiles/*.json`), edytowany w
przegladarce i zapisywany na dysku, wiec profile trzymasz w gicie.

Edytor regul ma kolorowanie skladni, numery linii, wciecia pod Tab/Enter oraz
przycisk **Formatuj** (rownanie wciec wg zagniezdzenia).

## Definicje urzadzen sa poza narzedziem

Repozytorium zawiera wylacznie narzedzie i **jedno generyczne urzadzenie
przykladowe** (`example-device`), ktore dokumentuje kontrakt. Wlasne sterowniki
trzymasz u siebie i wskazujesz przez `--profiles` / `--sims`.

Przyklad takiego zestawu: [bacnet-vav-nefryt](https://github.com/RyKaT07/bacnet-vav-nefryt).

## Symulatory (`sims/*.py`)

Symulowane urzadzenie to zwykly plik Pythona - kopiujesz `sims/example-device.py`
i przerabiasz pod swoje. Kontrakt jest krotki:

- `PARAMS` - punkty zapisywalne i ich wartosci poczatkowe
- `UNITS` - jednostki (opcjonalnie)
- `step(t, p, s)` - zwraca punkty tylko do odczytu; `t` to sekundy od startu,
  `p` to biezace parametry, `s` to wlasny stan miedzy krokami

Symulator z tymi samymi nazwami punktow co prawdziwe urzadzenie pozwala
dopracowac reguly zanim sprzet w ogole dojedzie.

## Skad sie biora nazwy punktow

Punkt to obiekt w sterowniku i **nazwe nadaje mu programista sterownika**, nie ta
aplikacja. Po polaczeniu zobaczysz dokladnie to, co siedzi w urzadzeniu - czasem
czytelne `SupplyFlowSetpoint`, czasem `AV12`.

Kolejnosc pracy: polacz sie, kliknij **Wypelnij z punktow** (wciaga wszystkie
nazwy z urzadzenia), popraw prawa strone na swoje aliasy, napisz reguly, zapisz
profil pod nazwa urzadzenia.
