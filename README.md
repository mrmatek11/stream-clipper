# stream-clipper

Najlepsze momenty z VOD-ów (Twitch/YouTube) zamienione w pionowe klipy 1080x1920 z napisami.
Wszystko działa lokalnie na Twoim GPU, bez API i bez tokenów.

1. **yt-dlp** pobiera listę VOD-ów z kanałów w `config.toml`.
2. **chat-downloader** pobiera zapis czatu. Skrypt szuka miejsc, gdzie czat wybucha (KEKW, LUL, W, ???...).
3. Pobierane są tylko te fragmenty, nie całe 8-godzinne VOD-y.
4. **faster-whisper** (CUDA) robi napisy po 3 słowa.
5. **ffmpeg** (NVENC) tworzy pion: rozmyte tło, przybliżony gameplay i wypalone napisy.

Klipy lądują w `out/<gra>/`. Nazwa zaczyna się od siły wybuchu czatu (`012.4x_...`), więc najlepsze są na górze listy.

> Klipuj tylko streamerów, którzy prowadzą kampanię klipowania albo dali Ci zgodę. Bez tego grozi Ci zgłoszenie o naruszenie praw autorskich i ban konta.

## Instalacja

Potrzebujesz Pythona 3.11+, ffmpeg i sterownika NVIDIA. Toolkit CUDA nie jest potrzebny, biblioteki instalują się z pip.

**Windows 11** (PowerShell):
```powershell
winget install Python.Python.3.12 Gyan.FFmpeg
git clone https://github.com/mrmatek11/stream-clipper; cd stream-clipper
py -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
```

**Kubuntu**:
```bash
sudo apt install python3-venv ffmpeg
git clone https://github.com/mrmatek11/stream-clipper && cd stream-clipper
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Użycie

Wpisz kanały do `config.toml`, potem:

```bash
python clipper.py                        # wszystkie gry i kanały
python clipper.py --url <link do VOD>    # jeden VOD, dobre na start do strojenia
```

Przetworzone VOD-y zapisują się w `out/.done`, więc kolejne uruchomienie bierze tylko nowe.
Pierwsze uruchomienie pobiera model Whispera (ok. 1,5 GB).

## Strojenie (`config.toml`)

| Problem | Zmień |
|---|---|
| Za mało klipów | obniż `min_ratio` (np. 2.0) |
| Klip zaczyna się za późno | zwiększ `before` |
| Gameplay za mały w pionie | zwiększ `zoom` (1.5) |
| Napisy zasłaniają HUD | zmień `margin_v` |
| Brak karty NVIDIA | `encoder = "libx264"`, `device = "cpu"`, `whisper_model = "small"` |
| Błąd CUDA / brak VRAM | `whisper_model = "medium"` albo `"small"` |

`python test_clipper.py` sprawdza wykrywanie momentów i napisy.
