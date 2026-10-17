# sk-public-money

[Slovensky](#slovensky) · [English](#english)

## Slovensky

**sk-public-money** je [MCP](https://modelcontextprotocol.io) server, ktorý AI asistentovi (napríklad
Claude) sprístupní slovenské verejné registre o tom, kam idú verejné peniaze: zmluvy štátu, verejné
obstarávania, firmy, ich skutočných vlastníkov a účtovné závierky. Asistent v nich vie vyhľadávať,
čítať ich a prepájať údaje z viacerých registrov v jednej odpovedi.

### Ukážka

Na otázku *„Čo médiá nenapísali o zákazkách Ústrednej vojenskej nemocnice v Ružomberku a o novej
nemocnici v Prešove?“* asistent s týmto serverom zostavil **[tento prehľad](https://hronecviktor.github.io/sk-public-money/examples/uvn-presov.html)**. Prepojil
Vestník verejného obstarávania, CRZ, RPO, RPVS, RÚZ, Finančnú správu a súdne rozhodnutia a pri každom
zistení overil, či o ňom už písali médiá.

### Prepojené registre

| Register | Údaje | Čo obsahuje | Odkiaľ sú údaje | Nástroje |
| --- | --- | --- | --- | --- |
| **CRZ**: Centrálny register zmlúv | 🌐 live | Zmluvy ministerstiev, štátnych a verejných inštitúcií od roku 2011, s prílohami (PDF) a dodatkami | Web [crz.gov.sk](https://www.crz.gov.sk) a jeho denné exporty | `crz_*` |
| **ÚVO**: verejné obstarávanie | 🌐 TED live<br>💾 Vestník: `uvo` | Kto čo obstarával a akým postupom, koľko prišlo ponúk, kto vyhral a za koľko; súhrnné správy o zákazkách s nízkymi hodnotami | Vestník verejného obstarávania (otvorené dáta na [data.slovensko.sk](https://data.slovensko.sk), od októbra 2023 v lokálnom indexe) a európsky vestník [TED](https://ted.europa.eu) (od roku 2016, zákazky nad finančnými limitmi EÚ) | `uvo_*` |
| **RPVS**: Register partnerov verejného sektora | 🌐 live | Koneční užívatelia výhod (skutoční vlastníci) firiem, ktoré dostávajú verejné peniaze, s dátumom narodenia; oprávnené osoby a verejní funkcionári | Otvorené API [rpvs.gov.sk](https://rpvs.gov.sk/rpvs) | `rpvs_*` |
| **RPO**: Register právnických osôb | 🌐 live | Totožnosť každej firmy, živnostníka, inštitúcie a združenia podľa IČO: názvy v čase, sídlo, štatutári, spoločníci, kapitál, vznik a zánik, právni predchodcovia a nástupcovia | API Ministerstva vnútra SR ([rpo.minv.sk](https://rpo.minv.sk)) | `rpo_*` |
| **REPLIK**: register insolvenčných konaní | 🌐 live | Konkurzy, reštrukturalizácie, oddlženia a likvidácie súdom: dlžník, správca, súd, stav a verejné oznamy súdu a správcu (od októbra 2025 nahradil Register úpadcov aj s jeho históriou) | Verejné SOAP API Ministerstva spravodlivosti SR (replik-ws.justice.sk) | `insolvency_*` |
| **Obchodný vestník** | 💾 `ov` | Oznámenia o firmách od júla 2011: likvidácie a výzvy veriteľom, zlúčenia, rozdelenia a zmeny právnej formy, zníženia základného imania, podania do obchodného registra, konkurzy (do septembra 2025) | Datahub [Slovensko.Digital](https://ekosystem.slovensko.digital) (týždenný dump a sync API), v lokálnom indexe | `ov_*` |
| **RÚZ**: Register účtovných závierok | 🌐 live | Účtovné závierky: tržby, výsledok hospodárenia, aktíva a vlastné imanie po rokoch | Otvorené API [registeruz.sk](https://www.registeruz.sk) | `ruz_*` |
| **Finančná správa SR** | 💾 `fs` | Registrácia pre DPH, index daňovej spoľahlivosti, platitelia DPH s dôvodom na zrušenie registrácie a vymazaní, vyrubená daň z príjmov právnických osôb a daňová strata po rokoch, nadmerné odpočty DPH, daňoví dlžníci | Otvorené dáta [opendata.financnasprava.sk](https://opendata.financnasprava.sk) (zoznamy v lokálnom indexe) | `fs_*` |
| **Súdy**: rozhodnutia a sudcovia | 🌐 live<br>💾 register diskvalifikácií: `court` (postaví sa aj sám) | Rozhodnutia všeobecných súdov (asi 4,7 milióna, s textom z PDF), v ktorých vystupuje firma s daným IČO; sudcovia a ich majetkové priznania | Pilotné API Ministerstva spravodlivosti SR ([obcan.justice.sk](https://obcan.justice.sk)) a [Súdna rada SR](https://www.sudnarada.gov.sk) | `court_*` |
| **PPA**: poľnohospodárske dotácie | 💾 `ppa` | Kto dostal peniaze zo spoločnej poľnohospodárskej politiky (EPZF a EPFRV) a z národného spolufinancovania, koľko, na aké opatrenie a v ktorej obci; za posledné dva finančné roky | Zoznamy prijímateľov, ktoré zverejňuje [Pôdohospodárska platobná agentúra](https://www.apa.sk/o-nas/zverejnovane-udaje/prehlad-prijimatelia-pomoci-z-epzf-epfrv) (XLSX, v lokálnom indexe) | `ppa_*` |
| **Financovanie politických strán** | 🌐 účty a správy live<br>💾 zoznam správ: `party` (postaví sa aj sám) | Pohyby na transparentných účtoch strán, kandidátov a kampaní v Slovenskej sporiteľni; výročné správy strán od roku 2015 s osobitnými evidenciami darov, pôžičiek a členských príspevkov | API [transparentných účtov Slovenskej sporiteľne](https://www.slsp.sk/sk/ludia/transparentne-ucty); výročné správy na [minv.sk](https://www.minv.sk/?vyrocnespravyps) (PDF, väčšinou skeny, ktoré asistent číta ako obrázky) | `party_*` |
| **EÚ fondy 2021–2027**: ITMS21+ | 💾 `eu` | Projekty spolufinancované z EÚ, žiadosti o NFP, žiadosti o platbu, obstarávania prijímateľov so zmluvami a dodávateľmi, faktúry k projektom | Otvorené API [ITMS21+](https://www.itms21.sk) (v lokálnom indexe) | `eu_*` |

🌐 live: nástroje sa pýtajú portálu pri každom dopyte, netreba nič sťahovať. 💾 lokálny index: treba ho raz postaviť cez `sk-public-money-sync <zdroj>` alebo všetky naraz cez `sk-public-money-sync all` (pozri [Prvé spustenie](#prvé-spustenie)).

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
- „S kým sa táto firma súdila a ako súd rozhodol?“
- „Koľko poľnohospodárskych dotácií dostali firmy zo skupiny Agrofert a na aké opatrenia?“

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
| `fs_get_taxpayer` | Daňový subjekt podľa IČO: registrácia pre DPH, index daňovej spoľahlivosti, zoznamy na zrušenie registrácie a vymazaných platiteľov, daň z príjmov a daňová strata po rokoch, posledné nadmerné odpočty DPH |
| `fs_search_debtors` | Hľadá v zozname daňových dlžníkov podľa mena (zoznam nemá IČO) |
| `fs_list_flagged` | Vymazaní platitelia DPH, platitelia s dôvodom na zrušenie registrácie alebo menej spoľahliví podľa dátumu a miesta |
| `court_search_decisions` | Hľadá súdne rozhodnutia podľa IČO alebo slov, súdu, sudcu, právnej oblasti a dátumu |
| `court_get_decision` | Rozhodnutie podľa ID alebo ECLI: súd, sudca, spisová značka, povaha, citované predpisy a text z PDF |
| `court_judge` | Sudca: súd, funkcia, stav a jeho majetkové priznania od roku 2013, ktoré vie aj prečítať |
| `court_disqualified` | Register diskvalifikácií: osoby, ktoré nesmú byť štatutármi firiem, s adresou, spisovou značkou a obdobím zákazu |
| `ppa_search_beneficiary` | Poľnohospodárske dotácie prijímateľa podľa mena alebo IČO (cez názov v RPO): sumy po fondoch a opatreniach, obec, materská spoločnosť |
| `ppa_top` | Najväčší prijímatelia za finančný rok, podľa opatrenia, obce alebo druhu prijímateľa |
| `party_account_transactions` | Pohyby na transparentnom účte v Slovenskej sporiteľni podľa IBAN alebo názvu účtu, s filtrom protistrany, dátumu, sumy a smeru |
| `party_annual_reports` | Výročné správy strany alebo roka: odkaz na PDF, opravená verzia, nedostatky, ktoré strany sú naskenované |
| `party_read_report` | Strany výročnej správy: textové strany ako text, naskenované ako obrázky, bez zadaných strán náhľady na nájdenie evidencií |
| `eu_search_projects` | Hľadá projekty a žiadosti o NFP z EÚ fondov 2021–2027 podľa IČO prijímateľa alebo partnera, slov a programu; so zazmluvnenou a nárokovanou sumou |
| `eu_get_project` | Celý projekt: prijímateľ, partneri, sumy (aj vyplatená), zmluva v CRZ, ukazovatele, dodávatelia, faktúry, obstarávania so zmluvami a žiadosti o platbu |
| `eu_search_procurements` | Hľadá obstarávania prijímateľov EÚ fondov podľa IČO obstarávateľa alebo hlavného dodávateľa a slov; pri dodávateľovi aj faktúry, ktoré fakturoval do projektov |

Na čo si dať pozor:

- **Rovnaké meno ešte neznamená tú istú osobu.** Porovnajte dátum narodenia (RPVS) alebo adresu (RPO).
- **Súdne rozhodnutia sa hľadajú podľa IČO fulltextom:** IČO môže patriť inej strane sporu alebo byť
  náhodné číslo, preto si pozrite úryvok. Fyzické osoby sú v rozhodnutiach anonymizované.
- **Štatutári v RPO sa automaticky porovnávajú s registrom diskvalifikácií** podľa mena (a adresy, ak
  sa zhoduje). Zhoda mena nie je dôkaz; register sa ukladá lokálne a obnovuje raz denne
  (`sk-public-money-sync court` stiahne aj obdobia zákazov vopred).
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
- **Zoznam daňových dlžníkov nemá IČO,** dlžníci sa hľadajú len podľa mena; porovnajte adresu.
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

### Prvé spustenie

Väčšina nástrojov funguje hneď po pripojení: CRZ, RPVS, RPO, RÚZ, TED, súdne rozhodnutia
a transparentné účty, aj insolvenčný register REPLIK, sa pýtajú priamo portálov a výročné správy strán sa sťahujú, až keď ich asistent
číta. Niektoré registre sa však nedajú prehľadávať cez internet, dajú sa len stiahnuť celé,
preto si ich server ukladá do lokálneho indexu. Kým index nie je postavený, nástroj to povie aj
s príkazom, ktorý ho postaví.

Všetky indexy postavíte, a neskôr aktualizujete, jedným príkazom:

```sh
uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync all
```

Príkaz synchronizuje štyri zdroje naraz, najpomalšie ako prvé. Každý zdroj sa pýta iného portálu,
takže žiadny portál nedostane viac požiadaviek, než povoľuje jeho vlastný limit. V termináli ukazuje
jeden riadok priebehu na zdroj; celý výstup každého zdroja je v `~/.cache/sk-public-money/logs/`.
Prvé spustenie tak trvá zhruba tak dlho ako najpomalší zdroj, asi 70 minút. `--jobs 1` ich spustí
jeden po druhom. Príkaz sa dá prerušiť (Ctrl+C) a spustiť znova (stiahne len to, čo chýba) a jeden
zlyhaný zdroj nezastaví ostatné. Jeden index postavíte cez `sk-public-money-sync <zdroj>`, niektoré
vynecháte cez `sk-public-money-sync all --skip ov`. Obchodný vestník (`ov`) je najpomalší, asi 70
minút, a nemá hotový index; ak ho nepotrebujete, vynechajte ho a postavte neskôr samostatne cez
`sk-public-money-sync ov`.

| Zdroj | Pre nástroje | Prvé spustenie | Miesto na disku | Ako sa aktualizuje |
| --- | --- | --- | --- | --- |
| `party` | `party_annual_reports` a `party_read_report` (zoznam si pri prvom použití postavia samy) | asi 20 sekúnd | menej ako 1 MB | zoznam najnovších rokov si nástroje obnovia samy raz týždenne |
| `ppa` | `ppa_*` (bez indexu nefungujú) | menej ako minúta | asi 20 MB | znova spustiť, keď PPA zverejní nový rok |
| `fs` | `fs_*` (bez indexu nefungujú) | asi 15 minút, sťahuje asi 0,7 GB (história dane z príjmov a DPH od júna 2018, odkedy ju Finančná správa archivuje) | asi 0,75 GB | spúšťať denne; malé denné zoznamy si nástroje doplnia samy |
| `court` | `court_disqualified` a kontrola štatutárov v RPO (fungujú aj bez neho) | asi 20 minút | menej ako 1 MB | zoznam si nástroje obnovia samy raz denne |
| `uvo` | `uvo_*` pod limitmi EÚ (bez indexu len TED) | asi minúta (hotový index, 155 MB); zo zdroja asi 45 minút a 7 GB | asi 330 MB | nové čísla si nástroje doplnia samy každých 6 hodín |
| `ov` | `ov_search_notices` (bez indexu nefunguje) | asi 70 minút; streamuje 4,2 GB dump z Datahubu (neukladá ho) a z jeho 4,2 milióna oznámení si nechá 2,8 milióna | asi 0,9 GB | zmeny si nástroje doplnia samy každých 6 hodín (najviac 5 strán), väčší odstup dobehne `sk-public-money-sync ov` |
| `eu` | `eu_*` (bez indexu funguje len `eu_get_project`, bez obstarávaní a žiadostí o platbu) | niekoľko minút (hotový index); zo zdroja okolo 5 hodín, dá sa prerušiť a pokračovať | asi 60 MB | spúšťať raz týždenne: znova načíta zoznamy a stiahne len nové a zmenené detaily (asi 10 minút) |

Pomalé zdroje sa pri prvom spustení nestavajú od nuly: príkaz stiahne hotový index z
[GitHub release `indexes`](https://github.com/hronecviktor/sk-public-money/releases/tag/indexes),
skontroluje ho a doplní len to, čo pribudlo odvtedy. Bez neho, len zo zdrojov, ich postaví
`sk-public-money-sync all --no-bootstrap`. Vtedy je prvé spustenie pomalé zámerne: server dodržiava
pravidlá portálov pre roboty a posiela požiadavky s odstupom, a index Vestníka si navyše nechá
stiahnuté čísla (0,75 GB) na prípadné prestavanie, ak nepridáte `--no-keep-raw`.

Aby boli údaje čerstvé, spúšťajte `all` pravidelne, napríklad každú noc cez cron (s celou cestou
k `uvx`, ktorú vypíše `which uvx`):

```sh
0 4 * * * /home/<vy>/.local/bin/uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync all
```

Indexy a ďalšie údaje sa ukladajú do `~/.cache/sk-public-money`, alebo do priečinka v premennej
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
- Finančná správa SR: CC BY 4.0 (index daňovej spoľahlivosti CC0), „Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/“
- Súdne rozhodnutia a sudcovia (Ministerstvo spravodlivosti SR) a majetkové priznania sudcov (Súdna rada SR): licencia nie je zverejnená
- PPA: licencia nie je uvedená; zoznamy zverejňuje Pôdohospodárska platobná agentúra podľa čl. 98
  nariadenia (EÚ) 2021/2116
- Transparentné účty Slovenskej sporiteľne a výročné správy strán (minv.sk): licencia ani podmienky použitia nie sú zverejnené
- ITMS21+: licencia nie je zverejnená, Ministerstvo investícií, regionálneho rozvoja a informatizácie SR
- REPLIK: verejné API Ministerstva spravodlivosti SR, licencia nie je zverejnená
- Obchodný vestník: Datahub Slovensko.Digital podľa [podmienok používania datasetov](https://ekosystem.slovensko.digital/podmienky); údaje majú len informatívny charakter, server ich skracuje a upravuje

Server dodržiava pravidlá jednotlivých portálov pre automatizovaný prístup a požiadavky na ne posiela
s odstupom. Podrobnosti o každom zdroji sú v [docs/SOURCES.md](docs/SOURCES.md) a o vývoji
v [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (oboje v angličtine).

## English

**sk-public-money** is an [MCP](https://modelcontextprotocol.io) server that gives an AI assistant
(such as Claude) access to Slovak public registers about where public money goes: state contracts,
public procurement, companies, their beneficial owners and their financial statements. The assistant
can search and read them, and combine data from several registers in one answer.

### Example

Asked *"What haven't the media reported about the contracts of the Central Military Hospital in
Ružomberok and the new hospital in Prešov?"*, an assistant using this server put together
**[this report](https://hronecviktor.github.io/sk-public-money/examples/uvn-presov.html#en)**. It combined the procurement journal, CRZ, RPO, RPVS, RÚZ, the tax
authority and court decisions, and checked each finding against what the media had already
published.

### Connected registers

| Register | Data | What it contains | Where the data comes from | Tools |
| --- | --- | --- | --- | --- |
| **CRZ**: Central Register of Contracts | 🌐 live | Contracts of ministries, state and public bodies since 2011, with attachments (PDF) and amendments | The [crz.gov.sk](https://www.crz.gov.sk) website and its daily exports | `crz_*` |
| **ÚVO**: public procurement | 🌐 TED live<br>💾 Vestník: `uvo` | Who bought what by which procedure, how many bids came in, who won and for how much; reports of low-value contracts | The Public Procurement Journal (Vestník; open data on [data.slovensko.sk](https://data.slovensko.sk), in a local index since October 2023) and the EU journal [TED](https://ted.europa.eu) (since 2016, contracts above the EU thresholds) | `uvo_*` |
| **RPVS**: Register of Public Sector Partners | 🌐 live | Beneficial owners of companies that receive public money, with birth dates; authorized persons and public officials | The open API of [rpvs.gov.sk](https://rpvs.gov.sk/rpvs) | `rpvs_*` |
| **RPO**: Register of Legal Entities | 🌐 live | The identity of every company, sole trader, public body and association by IČO: names over time, seat, directors, partners, capital, founding and dissolution, predecessors and successors | The Ministry of Interior's API ([rpo.minv.sk](https://rpo.minv.sk)) | `rpo_*` |
| **REPLIK**: Insolvency register | 🌐 live | Bankruptcies, restructurings, personal debt relief and court-ordered liquidations: debtor, trustee, court, status, and the public notices of court and trustee (replaced Register úpadcov in October 2025, with its history) | The Ministry of Justice's public SOAP API (replik-ws.justice.sk) | `insolvency_*` |
| **Obchodný vestník**: Commercial Bulletin | 💾 `ov` | Notices about companies since July 2011: liquidations and calls to creditors, mergers, splits and conversions, capital reductions, commercial register entries, bankruptcies (until September 2025) | [Slovensko.Digital](https://ekosystem.slovensko.digital)'s Datahub (weekly dump and sync API), in a local index | `ov_*` |
| **RÚZ**: Register of Financial Statements | 🌐 live | Financial statements: revenue, profit, assets and equity by year | The open API of [registeruz.sk](https://www.registeruz.sk) | `ruz_*` |
| **Finančná správa SR**: the tax authority | 💾 `fs` | VAT registration, tax reliability index, VAT payers facing cancellation and struck off, corporate income tax assessed and tax loss by year, VAT excess deductions, tax debtors | Open data at [opendata.financnasprava.sk](https://opendata.financnasprava.sk) (lists kept in a local index) | `fs_*` |
| **Courts**: decisions and judges | 🌐 live<br>💾 disqualified persons: `court` (also builds itself) | Decisions of the general courts (about 4.7 million, with text from the PDF) that mention a company's IČO; judges and their asset declarations | The Ministry of Justice's pilot API ([obcan.justice.sk](https://obcan.justice.sk)) and the [Judicial Council](https://www.sudnarada.gov.sk) | `court_*` |
| **PPA**: farm subsidies | 💾 `ppa` | Who got money from the Common Agricultural Policy (EAGF and EAFRD) and national co-financing, how much, under which measure and in which municipality; the last two financial years | The beneficiary lists published by the [Agricultural Paying Agency](https://www.apa.sk/o-nas/zverejnovane-udaje/prehlad-prijimatelia-pomoci-z-epzf-epfrv) (XLSX, in a local index) | `ppa_*` |
| **Political-party financing** | 🌐 accounts and reports live<br>💾 report listing: `party` (also builds itself) | Movements on the transparent accounts of parties, candidates and campaigns at Slovenská sporiteľňa; parties' annual reports since 2015 with their registers of gifts, loans and membership fees | The [Slovenská sporiteľňa transparent-account](https://www.slsp.sk/sk/ludia/transparentne-ucty) API; annual reports on [minv.sk](https://www.minv.sk/?vyrocnespravyps) (PDFs, mostly scans, which the assistant reads as images) | `party_*` |
| **EU funds 2021–2027**: ITMS21+ | 💾 `eu` | EU co-financed projects, grant applications, payment claims, recipients' procurements with contracts and suppliers, invoices billed to projects | The open API of [ITMS21+](https://www.itms21.sk) (in a local index) | `eu_*` |

🌐 live: the tools ask the portal on every query, nothing to download. 💾 local index: build it once with `sk-public-money-sync <source>`, or all of them with `sk-public-money-sync all` (see [First run](#first-run)).

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
- "Who has this company been in court with, and how did the court decide?"
- "How much farm subsidy did the Agrofert group's companies get, and under which measures?"

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
| `fs_get_taxpayer` | A taxpayer by IČO: VAT registration, tax reliability index, VAT cancellation and struck-off lists, corporate income tax and tax loss by year, latest VAT excess deductions |
| `fs_search_debtors` | Searches the tax debtor list by name (the list has no IČO) |
| `fs_list_flagged` | VAT payers struck off or facing cancellation, or rated less reliable, by date and place |
| `court_search_decisions` | Searches court decisions by IČO or words, court, judge, legal area and date |
| `court_get_decision` | A decision by ID or ECLI: court, judge, case number, outcome, cited laws and the text of its PDF |
| `court_judge` | A judge: court, function, status, and their asset declarations since 2013, which it can also read |
| `court_disqualified` | The register of disqualified persons: people banned from company boards, with address, case number and ban period |
| `ppa_search_beneficiary` | A beneficiary's farm subsidies by name or IČO (via its name in RPO): amounts by fund and measure, municipality, parent company |
| `ppa_top` | The largest beneficiaries in a financial year, by measure, municipality or kind of beneficiary |
| `party_account_transactions` | Movements on a Slovenská sporiteľňa transparent account by IBAN or account name, filtered by counterparty, date, amount and direction |
| `party_annual_reports` | A party's or a year's annual reports: PDF link, corrected version, deficiencies, which pages are scans |
| `party_read_report` | Pages of an annual report: text pages as text, scanned pages as images; without pages, thumbnails to find the registers |
| `eu_search_projects` | Searches EU-funded projects and grant applications of 2021–2027 by IČO of recipient or partner, words and programme, with contracted and claimed amounts |
| `eu_get_project` | A project in full: recipient, partners, amounts (including paid out), CRZ contract, indicators, suppliers, invoices, procurements with contracts, and payment claims |
| `eu_search_procurements` | Searches procurements of EU fund recipients by IČO of buyer or main supplier and words; for a supplier also the invoices it billed to projects |

Things to watch out for:

- **The same name doesn't mean the same person.** Compare birth dates (RPVS) or addresses (RPO).
- **Court decisions are found by IČO in their full text:** the number may belong to another party or
  be an unrelated figure, so check the snippet. People are anonymised in decisions.
- **Directors in RPO are checked against the register of disqualified persons automatically,** by
  name (and address when it matches). A matching name is not proof; the register is kept locally and
  refreshed daily (`sk-public-money-sync court` also fetches the ban periods in advance).
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
- **The tax debtor list has no IČO,** so debtors are matched by name only; compare the address.
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

### First run

Most tools work as soon as the server is connected: CRZ, RPVS, RPO, RÚZ, TED, court decisions,
transparent accounts and the REPLIK insolvency register ask the portals directly, and parties' annual reports are downloaded only when
the assistant reads one. Some registers can't be searched online, only downloaded whole, so the server
keeps them in a local index. Until an index is built, its tools say so, with the command that builds
it.

One command builds every index, and later keeps them up to date:

```sh
uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync all
```

It syncs four sources at once, the slowest first. Each source talks to a different portal, so no
portal gets more requests than its own limit allows. In a terminal it shows one progress line per
source; each source's full output is in `~/.cache/sk-public-money/logs/`. The first run therefore
takes about as long as the slowest source, around 70 minutes. `--jobs 1` runs them one after another.
It can be stopped (Ctrl+C) and run again (it fetches only what's missing), and one failing source
doesn't stop the others. `sk-public-money-sync <source>` builds a single index, and
`sk-public-money-sync all --skip ov` leaves some out. The Obchodný vestník (`ov`) is the slowest first
build, about 70 minutes, with no prebuilt index; if you don't need it, skip it and build it later on
its own with `sk-public-money-sync ov`.

| Source | For the tools | First run | Disk space | How it stays current |
| --- | --- | --- | --- | --- |
| `party` | `party_annual_reports` and `party_read_report` (they build the list themselves on first use) | about 20 seconds | under 1 MB | the tools refresh the newest years themselves once a week |
| `ppa` | `ppa_*` (they don't work without it) | under a minute | about 20 MB | run again when PPA publishes a new year |
| `fs` | `fs_*` (they don't work without it) | about 15 minutes, downloads about 0.7 GB (corporate tax and VAT history since June 2018, where the tax authority's archive starts) | about 0.75 GB | run daily; the tools update the small daily lists themselves |
| `court` | `court_disqualified` and the director check in RPO (both work without it too) | about 20 minutes | under 1 MB | the tools refresh the list themselves once a day |
| `uvo` | `uvo_*` below the EU thresholds (without it, TED only) | about a minute (prebuilt index, 155 MB); from the source about 45 minutes and 7 GB | about 330 MB | the tools fetch new issues themselves every 6 hours |
| `ov` | `ov_search_notices` (it doesn't work without it) | about 70 minutes; streams a 4.2 GB dump from Datahub (without storing it) and keeps 2.8 million of its 4.2 million notices | about 0.9 GB | the tools fetch changes themselves every 6 hours (at most 5 pages); `sk-public-money-sync ov` catches up a longer gap |
| `eu` | `eu_*` (without it only `eu_get_project` works, without procurements and payment claims) | a few minutes (prebuilt index); from the source around 5 hours, can be stopped and resumed | about 60 MB | run weekly: reloads the lists and fetches only new and changed details (about 10 minutes) |

The slow sources aren't built from scratch on the first run: the command downloads a prebuilt index
from the [GitHub release `indexes`](https://github.com/hronecviktor/sk-public-money/releases/tag/indexes),
checks it, and fetches only what's been added since. `sk-public-money-sync all --no-bootstrap` builds
them from the sources alone instead. That first run is slow on purpose: the server follows each
portal's rules for robots and spaces out its requests, and the Vestník index also keeps the downloaded
issues (0.75 GB) for a rebuild unless you add `--no-keep-raw`.

To keep the data fresh, run `all` regularly, for example nightly from cron (with the full path to
`uvx` that `which uvx` prints):

```sh
0 4 * * * /home/<you>/.local/bin/uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync all
```

Indexes and other data are stored in `~/.cache/sk-public-money`, or in the folder set in the
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
- Finančná správa SR: CC BY 4.0 (the tax reliability index CC0), "Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/"
- Court decisions and judges (Ministry of Justice) and judges' asset declarations (Judicial Council of the Slovak Republic): no licence published
- PPA: no licence stated; the lists are published by the Agricultural Paying Agency under Article 98
  of Regulation (EU) 2021/2116
- Slovenská sporiteľňa transparent accounts and the parties' annual reports (minv.sk): no licence or terms of use published
- ITMS21+: no licence published, Ministry of Investments, Regional Development and Informatisation
- REPLIK: public API of the Ministry of Justice of the Slovak Republic; no licence is published
- Obchodný vestník: Slovensko.Digital Datahub under its [dataset terms](https://ekosystem.slovensko.digital/podmienky); the data are informative only, and the server shortens and restructures them

The server follows each portal's rules for automated access and spaces out its requests. Details on
each source are in [docs/SOURCES.md](docs/SOURCES.md), and on development in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
