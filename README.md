# supdrop-data

Denní ceny pro šup (supdrop.cz). Web a appka si při otevření načtou `data/latest.json` odsud.

- `data/watchlist.json` – co šup hlídá
- `data/latest.json` – aktuální ceny a slevy
- `scripts/update_data.py` – stáhne ceny, spočítá slevy a skóre
- GitHub Action „Denní data šupu“ to spouští každé ráno

Změny tady nespouští nasazení na Netlify, takže nespotřebují žádné kredity.
