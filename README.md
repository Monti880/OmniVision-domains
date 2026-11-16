# OmniVision – domini

Elenco aggiornato degli indirizzi dei siti usati da OmniVision. L'app lo legge da:

- `https://raw.githubusercontent.com/Monti880/OmniVision-domains/main/domains.json`
- `https://cdn.jsdelivr.net/gh/Monti880/OmniVision-domains@main/domains.json` (copia di riserva)

## Aggiornamento automatico

Il workflow **Update Domains** gira ogni 3 ore: controlla ogni dominio, prova lo stesso nome su altre
estensioni e le altre liste pubbliche, e salva in `domains.json` gli indirizzi nuovi che funzionano.
Se un servizio non risponde più apre una issue "Domini non raggiungibili".
Si può avviare anche a mano da **Actions → Update Domains → Run workflow**.

## Correzione a mano

Quando un sito cambia nome (non solo estensione) il controllo automatico non può trovarlo: apri
`domains.json` su GitHub, premi la matita, sostituisci l'indirizzo e salva. Tutte le installazioni di
OmniVision lo usano dal controllo successivo (all'avvio, ogni 30 minuti o alla prima ricerca che fallisce).

`domain_resolver.py` è una copia di `OmniVision/utils/domain_resolver.py`: se cambi uno dei due, aggiorna l'altro.
