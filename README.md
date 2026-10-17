# sk-public-money

[Slovensky](#slovensky) · [English](#english)

## Slovensky

**sk-public-money** je [MCP](https://modelcontextprotocol.io) server, ktorý AI asistentovi (napríklad
Claude) sprístupní slovenské verejné registre o tom, kam idú verejné peniaze: zmluvy štátu, verejné
obstarávania, firmy, ich skutočných vlastníkov a účtovné závierky. Asistent v nich vie vyhľadávať,
čítať ich a prepájať údaje z viacerých registrov v jednej odpovedi.

### Prepojené registre

| Register | Čo obsahuje | Odkiaľ sú údaje | Nástroje |
| --- | --- | --- | --- |
| **CRZ**: Centrálny register zmlúv | Zmluvy ministerstiev, štátnych a verejných inštitúcií od roku 2011, s prílohami (PDF) a dodatkami | Web [crz.gov.sk](https://www.crz.gov.sk) a jeho denné exporty | `crz_*` |
| **ÚVO**: verejné obstarávanie | Kto čo obstarával a akým postupom, koľko prišlo ponúk, kto vyhral a za koľko; súhrnné správy o zákazkách s nízkymi hodnotami | Vestník verejného obstarávania (otvorené dáta na [data.slovensko.sk](https://data.slovensko.sk), od októbra 2023 v lokálnom indexe) a európsky vestník [TED](https://ted.europa.eu) (od roku 2016, zákazky nad finančnými limitmi EÚ) | `uvo_*` |
| **RPVS**: Register partnerov verejného sektora | Koneční užívatelia výhod (skutoční vlastníci) firiem, ktoré dostávajú verejné peniaze, s dátumom narodenia; oprávnené osoby a verejní funkcionári | Otvorené API [rpvs.gov.sk](https://rpvs.gov.sk/rpvs) | `rpvs_*` |
| **RPO**: Register právnických osôb | Totožnosť každej firmy, živnostníka, inštitúcie a združenia podľa IČO: názvy v čase, sídlo, štatutári, spoločníci, kapitál, vznik a zánik, právni predchodcovia a nástupcovia | API Ministerstva vnútra SR ([rpo.minv.sk](https://rpo.minv.sk)) | `rpo_*` |
| **REPLIK**: register insolvenčných konaní | Konkurzy, reštrukturalizácie, oddlženia a likvidácie súdom: dlžník, správca, súd, stav a verejné oznamy súdu a správcu (od októbra 2025 nahradil Register úpadcov aj s jeho históriou) | Verejné SOAP API Ministerstva spravodlivosti SR (replik-ws.justice.sk) | `insolvency_*` |
| **RÚZ**: Register účtovných závierok | Účtovné závierky: tržby, výsledok hospodárenia, aktíva a vlastné imanie po rokoch | Otvorené API [registeruz.sk](https://www.registeruz.sk) | `ruz_*` |

Ako sú registre prepojené:

- **IČO je spoločný kľúč.** Každý nástroj, ktorý vráti firmu alebo inštitúciu, vráti aj jej IČO, a
  s ním sa dá hneď pokračovať v ďalšom registri.
- **Obstarávania odkazujú na zmluvy.** Výsledky obstarávaní obsahujú ID zmluvy v CRZ, takže od súťaže
  sa dá prejsť priamo k podpísanej zmluve a jej textu.
- **Osoby sa dajú hľadať vo viacerých registroch:** v RPVS ako koneční užívatelia výhod a verejní
  funkcionári, v RPO ako štatutári a spoločníci.

### Čo s tým môžete robiť

Príklady otázok pre asistenta:

- „Aké najväčšie zmluvy má Slovnaft so štátom a kto sú jeho koneční užívatelia výhod?“
- „V ktorých firmách je táto osoba konečným užívateľom výhod alebo štatutárom a aké zákazky tie firmy
  vyhrali?“
- „Koľko ponúk prišlo do súťaží ministerstva vnútra na pohonné hmoty a kto ich vyhrával?“
- „Firma vyhrala zákazku za 5 miliónov eur. Kedy vznikla a aké mala tržby v posledných rokoch?“
- „Čo presne je v prílohe tejto zmluvy?“
- „Ktoré zmluvy včera zverejnilo ministerstvo zdravotníctva?“

| Nástroj | Čo robí |
| --- | --- |
| `crz_search_contracts` | Hľadá zmluvy podľa dodávateľa, objednávateľa, IČO, názvu, čísla zmluvy, rezortu, sumy a dátumu zverejnenia, alebo fulltextom |
| `crz_get_contract` | Celý záznam zmluvy alebo dodatku: strany s IČO, dátumy, sumy, prílohy, dodatky |
| `crz_read_attachment` | Text PDF prílohy zmluvy po stranách |
| `crz_daily_changes` | Všetky zmluvy zverejnené alebo zmenené v daný deň, z denného exportu |
| `crz_list_departments` | Zoznam rezortov pre filter vyhľadávania |
| `uvo_search_procurements` | Hľadá obstarávania podľa IČO (ako obstarávateľ, víťaz, uchádzač alebo subdodávateľ), slov, kódu CPV a dátumu |
| `uvo_get_notice` | Celé oznámenie: po častiach zákazky ponuky, ich sumy a poradie, víťazi a podpísané zmluvy s odkazom do CRZ |
| `rpvs_get_partner` | Partner verejného sektora podľa IČO: koneční užívatelia výhod s dátumom narodenia, oprávnená osoba, verejní funkcionári, výmaz, pokuty |
| `rpvs_search_partner` | Hľadá partnerov podľa názvu |
| `rpvs_search_person` | Kde je osoba konečným užívateľom výhod, verejným funkcionárom alebo oprávnenou osobou |
| `rpo_get_organization` | Firma alebo inštitúcia podľa IČO: názvy, sídlo, štatutári, spoločníci, kapitál, vznik a zánik |
| `rpo_search_organization` | Hľadá organizácie podľa súčasného alebo bývalého názvu |
| `rpo_search_person` | Kde je osoba štatutárom alebo spoločníkom |
| `ruz_search_entity` | Hľadá účtovné jednotky podľa názvu, IČO alebo DIČ |
| `ruz_get_entity` | Účtovná jednotka so zoznamom všetkých jej závierok |
| `ruz_get_financials` | Tržby, výnosy, výsledok hospodárenia, aktíva a vlastné imanie po rokoch, s uvedením riadku výkazu, z ktorého pochádzajú |

Na čo si dať pozor:

- **Rovnaké meno ešte neznamená tú istú osobu.** Porovnajte dátum narodenia (RPVS) alebo adresu (RPO).
- **Hodnota rámcovej dohody je maximum,** nie suma, ktorá sa naozaj zaplatila. Hodnota 0 znamená, že
  obstarávateľ ju nezverejnil.
- **TED obsahuje len zákazky nad limitmi EÚ.** Podlimitné zákazky a zákazky s nízkymi hodnotami sú
  len vo Vestníku, a teda v lokálnom indexe od októbra 2023.
- **Veľké firmy účtujúce podľa IFRS** (napríklad Slovnaft) majú v RÚZ len PDF dokumenty, z ktorých
  sa čísla automaticky nečítajú; nástroj na ne odkáže.
- **Približne pätina príloh v CRZ sú skeny bez textovej vrstvy,** ktorých text sa prečítať nedá.
- **Niektoré odpovede trvajú niekoľko sekúnd:** portály obmedzujú počet požiadaviek a niektoré API sú
  pomalé (vyhľadávanie v RPO 5–30 s, prvé načítanie veľkej firmy v RÚZ až 30 s). CRZ má večer
  pravidelnú údržbu, počas ktorej nie je dostupný.
- **Vyhľadávanie mien nerozlišuje veľké a malé písmená ani diakritiku:** „Siroky“ nájde Široký,
  Siroký aj Široky.

### Inštalácia

Potrebujete [uv](https://docs.astral.sh/uv/getting-started/installation/) (nainštaluje si aj potrebný
Python). Server sa stiahne priamo z GitHubu a spúšťa sa cez `uvx`, nič netreba klonovať.

Pripojenie ku **Claude Code** (pre všetky vaše projekty):

```sh
claude mcp add -s user sk-public-money -- uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-mcp
```

Pripojenie ku **Claude Desktop** a iným klientom: do konfigurácie MCP serverov (v Claude Desktop
*Settings → Developer → Edit Config*) pridajte:

```json
{
  "mcpServers": {
    "sk-public-money": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/hronecviktor/sk-public-money", "sk-public-money-mcp"]
    }
  }
}
```

Ak klient `uvx` nenájde, zadajte celú cestu, ktorú vypíše `which uvx` (na Windows `where uvx`).

`uvx` si stiahnutú verziu pamätá. Na najnovšiu verziu z GitHubu prejdete jednorazovým spustením
`uvx --refresh --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-mcp`
(ukončite ho cez Ctrl+C) a reštartom klienta.

**Index Vestníka verejného obstarávania** (voliteľný, ale bez neho nástroje `uvo_*` hľadajú len
v TED, teda len zákazky nad limitmi EÚ):

```sh
uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync uvo
```

Prvé spustenie stiahne všetky čísla Vestníka od októbra 2023: asi 770 súborov, spolu približne 7 GB,
čo trvá okolo 45 minút. Index potom zaberá asi 300 MB a stiahnuté súbory, uložené komprimované, ďalších
0,75 GB. Príkaz sa dá prerušiť a spustiť znova; stiahne len to, čo chýba. Ďalej sa index dopĺňa sám:
nástroje každých 6 hodín skontrolujú nové čísla a stiahnu ich.

Index a ďalšie údaje sa ukladajú do `~/.cache/sk-public-money`, alebo do priečinka v premennej
prostredia `SK_PUBLIC_MONEY_CACHE_DIR`.

### Zdroje údajov a licencie

Kód servera je pod licenciou [0BSD](LICENSE): ktokoľvek ho môže používať, kopírovať, upravovať a šíriť
na akýkoľvek účel bez akýchkoľvek podmienok. Údaje, ktoré nástroje získavajú z registrov, majú vlastné
licencie:

- CRZ: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), Úrad vlády SR
- Vestník verejného obstarávania: CC BY 4.0, Úrad pre verejné obstarávanie
- RPO: CC BY 4.0, Ministerstvo vnútra SR
- RÚZ: [CC0](https://creativecommons.org/publicdomain/zero/1.0/)
- TED: bezplatné opätovné použitie s uvedením zdroja (Úrad pre publikácie EÚ)
- RPVS: otvorené údaje Ministerstva spravodlivosti SR
- REPLIK: verejné API Ministerstva spravodlivosti SR, licencia nie je zverejnená

Server dodržiava pravidlá jednotlivých portálov pre automatizovaný prístup a požiadavky na ne posiela
s odstupom. Podrobnosti o každom zdroji sú v [docs/SOURCES.md](docs/SOURCES.md) a o vývoji
v [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (oboje v angličtine).

## English

**sk-public-money** is an [MCP](https://modelcontextprotocol.io) server that gives an AI assistant
(such as Claude) access to Slovak public registers about where public money goes: state contracts,
public procurement, companies, their beneficial owners and their financial statements. The assistant
can search and read them, and combine data from several registers in one answer.

### Connected registers

| Register | What it contains | Where the data comes from | Tools |
| --- | --- | --- | --- |
| **CRZ**: Central Register of Contracts | Contracts of ministries, state and public bodies since 2011, with attachments (PDF) and amendments | The [crz.gov.sk](https://www.crz.gov.sk) website and its daily exports | `crz_*` |
| **ÚVO**: public procurement | Who bought what by which procedure, how many bids came in, who won and for how much; reports of low-value contracts | The Public Procurement Journal (Vestník; open data on [data.slovensko.sk](https://data.slovensko.sk), in a local index since October 2023) and the EU journal [TED](https://ted.europa.eu) (since 2016, contracts above the EU thresholds) | `uvo_*` |
| **RPVS**: Register of Public Sector Partners | Beneficial owners of companies that receive public money, with birth dates; authorized persons and public officials | The open API of [rpvs.gov.sk](https://rpvs.gov.sk/rpvs) | `rpvs_*` |
| **RPO**: Register of Legal Entities | The identity of every company, sole trader, public body and association by IČO: names over time, seat, directors, partners, capital, founding and dissolution, predecessors and successors | The Ministry of Interior's API ([rpo.minv.sk](https://rpo.minv.sk)) | `rpo_*` |
| **REPLIK**: Insolvency register | Bankruptcies, restructurings, personal debt relief and court-ordered liquidations: debtor, trustee, court, status, and the public notices of court and trustee (replaced Register úpadcov in October 2025, with its history) | The Ministry of Justice's public SOAP API (replik-ws.justice.sk) | `insolvency_*` |
| **RÚZ**: Register of Financial Statements | Financial statements: revenue, profit, assets and equity by year | The open API of [registeruz.sk](https://www.registeruz.sk) | `ruz_*` |

How the registers connect:

- **IČO, the company ID, is the common key.** Every tool that returns a company or public body also
  returns its IČO, which carries straight on into the next register.
- **Procurement links to contracts.** Procurement results carry the contract's CRZ ID, so you can go
  from a tender straight to the signed contract and its text.
- **People can be searched in several registers:** in RPVS as beneficial owners and public
  officials, in RPO as directors and partners.

### What you can do with it

Example questions for the assistant:

- "What are Slovnaft's largest contracts with the state, and who are its beneficial owners?"
- "In which companies is this person a beneficial owner or director, and what tenders did those
  companies win?"
- "How many bids came into the Ministry of Interior's fuel tenders, and who won them?"
- "A company won a €5 million contract. When was it founded, and what was its revenue in recent
  years?"
- "What exactly is in this contract's attachment?"
- "Which contracts did the Ministry of Health publish yesterday?"

| Tool | What it does |
| --- | --- |
| `crz_search_contracts` | Searches contracts by supplier, buyer, IČO, title, contract number, department, amount and publication date, or by full text |
| `crz_get_contract` | A contract or amendment in full: parties with IČO, dates, amounts, attachments, amendments |
| `crz_read_attachment` | The text of a contract's PDF attachment, page by page |
| `crz_daily_changes` | Every contract published or changed on a given day, from the daily export |
| `crz_list_departments` | The departments for the search filter |
| `uvo_search_procurements` | Searches procurement by IČO (as buyer, winner, tenderer or subcontractor), words, CPV code and date |
| `uvo_get_notice` | A notice in full: per lot the bids with amounts and ranks, the winners, and the signed contracts with their CRZ link |
| `rpvs_get_partner` | A public-sector partner by IČO: beneficial owners with birth dates, authorized person, public officials, deletion, fines |
| `rpvs_search_partner` | Finds partners by name |
| `rpvs_search_person` | Where a person is a beneficial owner, public official or authorized person |
| `rpo_get_organization` | A company or public body by IČO: names, seat, directors, partners, capital, founding and dissolution |
| `rpo_search_organization` | Finds organizations by current or former name |
| `rpo_search_person` | Where a person is a director or partner |
| `ruz_search_entity` | Finds accounting entities by name, IČO or DIČ |
| `ruz_get_entity` | An accounting entity with the list of all its statements |
| `ruz_get_financials` | Revenue, income, profit, assets and equity by year, each with the report row it came from |

Things to watch out for:

- **The same name doesn't mean the same person.** Compare birth dates (RPVS) or addresses (RPO).
- **A framework agreement's value is a maximum,** not what was actually paid. A value of 0 means the
  buyer didn't disclose it.
- **TED only covers contracts above the EU thresholds.** Below-threshold and low-value contracts are
  only in the Vestník, and so only in the local index from October 2023.
- **Large companies reporting under IFRS** (such as Slovnaft) file only PDFs in RÚZ, whose figures
  aren't read automatically; the tool links to them.
- **About a fifth of CRZ attachments are scans without a text layer,** whose text can't be read.
- **Some answers take several seconds:** the portals limit request rates and some APIs are slow
  (RPO searches 5–30 s, the first look at a large company in RÚZ up to 30 s). CRZ has regular evening
  maintenance, during which it's unavailable.
- **Name searches ignore case and diacritics:** "Siroky" finds Široký, Siroký and Široky.

### Installation

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which also fetches a suitable
Python. The server is installed straight from GitHub and run with `uvx`; there's nothing to clone.

Connecting to **Claude Code** (for all your projects):

```sh
claude mcp add -s user sk-public-money -- uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-mcp
```

Connecting to **Claude Desktop** and other clients: add this to the MCP server configuration (in
Claude Desktop, *Settings → Developer → Edit Config*):

```json
{
  "mcpServers": {
    "sk-public-money": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/hronecviktor/sk-public-money", "sk-public-money-mcp"]
    }
  }
}
```

If the client can't find `uvx`, give the full path that `which uvx` prints (on Windows, `where uvx`).

`uvx` keeps the version it downloaded. To move to the latest version on GitHub, run
`uvx --refresh --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-mcp` once
(stop it with Ctrl+C) and restart the client.

**The procurement journal index** (optional, but without it the `uvo_*` tools search only TED, so
only contracts above the EU thresholds):

```sh
uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync uvo
```

The first run downloads every Vestník issue since October 2023: about 770 files, roughly 7 GB in
total, which takes around 45 minutes. The index then takes about 300 MB, and the downloaded files,
kept compressed, another 0.75 GB. The command can be stopped and run again; it fetches only what's
missing. After that the index tops itself up: every 6 hours the tools check for new issues and fetch
them.

The index and other data are stored in `~/.cache/sk-public-money`, or in the folder set in the
`SK_PUBLIC_MONEY_CACHE_DIR` environment variable.

### Data sources and licences

The server's code is licensed under [0BSD](LICENSE): anyone may use, copy, modify and distribute it
for any purpose, without any conditions. The data the tools fetch from the registers has its own
licences:

- CRZ: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), Government Office of the Slovak Republic
- Public Procurement Journal: CC BY 4.0, Public Procurement Office (ÚVO)
- RPO: CC BY 4.0, Ministry of Interior of the Slovak Republic
- RÚZ: [CC0](https://creativecommons.org/publicdomain/zero/1.0/)
- TED: free reuse with attribution (Publications Office of the EU)
- RPVS: open data of the Ministry of Justice of the Slovak Republic
- REPLIK: public API of the Ministry of Justice of the Slovak Republic; no licence is published

The server follows each portal's rules for automated access and spaces out its requests. Details on
each source are in [docs/SOURCES.md](docs/SOURCES.md), and on development in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
