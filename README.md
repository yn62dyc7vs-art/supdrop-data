# supdrop-data

Denní katalog cen pro šup (supdrop.cz).

- `config/shops.json` – partnerské obchody (formát feedu, ID odkazu v eHUBu, důvěryhodnost)
- `scripts/build_catalog.py` – stáhne produktové feedy, spojí varianty, zařadí do kategorií, spočítá slevy a vytvoří partnerské odkazy
- GitHub Action „Denní data šupu“ to spouští každé ráno a výsledek uloží do větve `live`:
  - `latest.json` – katalog, který si web a appka načtou při otevření
  - `state.json` – historie cen za 30 dní (z ní se počítá sleva u feedů bez původní ceny)
- adresy feedů jsou v tajné proměnné `SHOP_FEEDS` (Settings → Secrets and variables → Actions)

Nový obchod: přidat řádek do `config/shops.json` a jeho feed do `SHOP_FEEDS`.
Změny tady nespouští nasazení na Netlify, takže nespotřebují žádné kredity.
`scripts/update_data.py` je stará verze (čtení stránek obchodů), už se nepoužívá.
