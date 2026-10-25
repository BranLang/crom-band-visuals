# CROM, návod krok za krokom

CROM robí vizuály pre kapely: titulky skladieb, odhalenie loga a vlniaci sa obal albumu.
Beží iba na tvojom počítači (Windows 10 alebo 11), nič neposiela na internet. Pre macOS pozri README.md.

---

## 1. Rozbaľ balík

1. Stiahni `CROM.zip` (na GitHube **Code > Download ZIP**).
2. Klikni naň pravým tlačidlom, zvoľ **Extrahovať všetko...** a ako cieľ zadaj napríklad `C:\CROM`.
   - Nerozbaľuj ho do OneDrive ani na plochu so synchronizáciou, video súbory sú veľké.
3. V priečinku `C:\CROM` by si mal vidieť súbory `instaluj.bat`, `spusti.bat`, `app.py` a priečinky `moduly`, `projekty`, `web`.

## 2. Inštalácia (stačí raz)

1. Dvojklik na **`instaluj.bat`**.
2. Ak Windows ukáže modré okno „Systém Windows ochránil váš počítač“, klikni na **Ďalšie informácie** a potom **Spustiť aj tak**.
3. Inštalátor sám:
   - nájde Python 3.10 alebo novší, a keď chýba, nainštaluje Python 3.12 cez winget,
   - vytvorí vlastné prostredie `.venv` v priečinku CROM,
   - stiahne knižnice (numpy, Pillow, opencv, fontTools),
   - nainštaluje ffmpeg, ak ho ešte nemáš.
4. Winget sa môže opýtať na súhlas alebo na práva správcu, potvrď to.
5. Trvá to pár minút a potrebuje internet. Na konci uvidíš **Hotovo**.
6. Ak inštalácia skončí hláškou, že ffmpeg nenašla, zatvor okno a spusti `instaluj.bat` ešte raz (nový PATH sa niekedy prejaví až v novom okne).

## 3. Spustenie

1. Dvojklik na **`spusti.bat`**.
2. Otvorí sa prehliadač na adrese **http://localhost:8765**.
3. Čierne okno nechaj otvorené, kým pracuješ. Zatvorením okna (alebo Ctrl+C) aplikáciu vypneš.

## 4. Práca v aplikácii

### Projekt
- Hore vyberieš projekt (kapelu). `Demo` je pripravený s ukážkovým logom a obalom.
- Pre inú kapelu klikni na **+** vedľa výberu projektu (Nový projekt) a zadaj názov. Každý projekt má vlastné logá, obaly, recepty a výstupy.

### Moduly (efekty)
| Modul | Čo robí |
|---|---|
| **Titulky** | Názov skladby a albumu, animovaný text na koncert alebo do klipu. |
| **Odhalenie loga** | Ľubovoľné logo sa objaví so žiarou, leskom a iskrami. Vo finále je logo presne pôvodný obrázok. |
| **Obal (vlniaci sa)** | Obal albumu sa vlní ako látka vo vetre. |

### Logo a obal
- Ikonka so šípkou nahor vedľa výberu projektu (**Logo a obaly projektu**) otvorí okno, kde tlačidlom **Nahrať…** pridáš logo alebo obal.
- V module potom súbor vyberieš v poli **logo** alebo **obal**. Malá ikonka vedľa poľa nahrá nový súbor rovno odtiaľ.
- Logo najlepšie ako **PNG s priehľadným pozadím**. Aplikácia ho nikdy neprekresľuje, v pokoji je pixel po pixeli rovnaké.

### Nastavenia a náhľad
- Vpravo sú slidery (časovanie, farby, svetlo, kamera). Náhľad sa obnovuje hneď.
- Časová os dole ukazuje fázy animácie, kliknutím skočíš na daný okamih.
- **Predvoľby** nastavia celý vzhľad naraz, potom ich môžeš doladiť.

### Priehľadné pozadie
- V skupine **Výstup** zvoľ pozadie **Priehľadné (alfa kanál)** a potom formát:
  - **ProRes 4444 (.mov)** pre Premiere, DaVinci a Final Cut,
  - **HAP Alpha (.mov)** pre Resolume a VJ,
  - **WebM (.webm)** pre web,
  - **PNG sekvencia** pre hocičo.
- Takéto video v strižni položíš na čokoľvek. Rýchly náhľad s alfou je vždy WebM.

### Render
- **Rýchly náhľad** urobí video v 960×540 do `projekty/<projekt>/vystupy/_nahlady`.
- **Render** urobí finálne video v plnom rozlíšení do `projekty/<projekt>/vystupy/<názov>/vNN`.
- Ikonka fotoaparátu pod náhľadom uloží aktuálnu snímku ako PNG.
- Záložka **Výstupy** ukáže hotové videá, ikonkou priečinka ich otvoríš.
- **Uložiť recept** (Ctrl+S) zapamätá všetky nastavenia, v záložke **Recepty** ich znova načítaš.

## 5. Voliteľne: ovládanie cez Claude (MCP)

Ak používaš Claude Desktop alebo Claude Code, CROM sa dá ovládať aj slovami.

1. Najprv musí prebehnúť `instaluj.bat`.
2. Dvojklik na **`pripoj_claude.bat`**. Opýta sa, či má CROM pridať do Claude Code a do Claude Desktop. Odpovedz `A` (áno) alebo `n` (nie).
   - Pôvodné nastavenia Claude Desktop sa zálohujú do `claude_desktop_config.json.zaloha`.
3. Claude Desktop úplne zatvor (aj z ikonky pri hodinách) a znova otvor.
4. CROM sa spustí sám, keď ho Claude potrebuje, `spusti.bat` netreba.

Príklady, čo môžeš Claudovi napísať:
- „V CROM-e urob titulky pre skladbu *Zlatý drak*, album *Svetlo*, tmavé s ohňom, a ukáž mi náhľad v 3. sekunde.“
- „Vytvor projekt pre kapelu XY, pridaj logo `C:\Users\ja\Desktop\logo.png` a urob odhalenie loga s priehľadným pozadím.“
- „Vyrenderuj obal vlniaci sa 10 sekúnd vo Full HD.“

## 6. Voliteľne: AI asistent v aplikácii (platené)

Aplikácia má asistenta, ktorý z vety poskladá nastavenia. Potrebuje Anthropic API kľúč, **každé použitie sa platí** z tvojho účtu na console.anthropic.com.

1. V priečinku CROM otvor príkazový riadok (do adresného riadka priečinka napíš `cmd` a Enter) a spusti:
   ```
   .venv\Scripts\python -m pip install anthropic
   ```
2. Kľúč vlož v aplikácii v záložke **Asistent** pod ikonkou kľúča (Nastavenia) a daj **Uložiť kľúč**. Alebo ho nastav ako premennú prostredia `ANTHROPIC_API_KEY`.
   - **Overiť kľúč** nič nestojí, platí sa až za skutočné použitie asistenta.
3. Kľúč nikomu neposielaj. Uloží sa iba do súboru `_nastavenia.json` v priečinku CROM.

## 7. Keď niečo nejde

| Problém | Riešenie |
|---|---|
| `spusti.bat` píše „Python pre CROM nie je pripravený“ | Spusti najprv `instaluj.bat`. |
| „Chýba ffmpeg“ | Spusti `instaluj.bat` znova v novom okne, alebo nainštaluj ffmpeg z https://www.gyan.dev/ffmpeg/builds/ |
| Prehliadač sa neotvoril | Otvor ručne http://localhost:8765 |
| „Port 8765 je obsadený“ | CROM už beží v inom okne. Zatvor ho, alebo použi to otvorené. |
| Modul hlási chybu | Pri zozname modulov sa objaví ikonka výstrahy s počtom chýb. Klikni na ňu a text chyby pošli autorovi (GitHub Issues). |
| Render je pomalý | Full HD video sa počíta na procesore, minúta videa môže trvať niekoľko minút. Na skúšanie používaj Náhľad. |
| Claude CROM nevidí | Spusti `pripoj_claude.bat` znova a Claude Desktop úplne reštartuj. Záznam behu je v `_mcp_app.log`. |
