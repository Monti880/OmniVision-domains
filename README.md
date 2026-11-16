# OmniVision – domini

Elenco aggiornato degli indirizzi dei siti usati da OmniVision. L'app lo legge da:

- `https://raw.githubusercontent.com/Monti880/OmniVision-domains/main/domains.json`
- `https://cdn.jsdelivr.net/gh/Monti880/OmniVision-domains@main/domains.json` (copia di riserva)

## Aggiornamento

La lista si aggiorna **a mano**: quando un sito cambia indirizzo, apri `domains.json` su GitHub, premi la
matita, sostituisci l'indirizzo e salva. Tutte le installazioni di OmniVision lo usano dal controllo
successivo (all'avvio, ogni 30 minuti o alla prima ricerca che fallisce).

Il workflow **Update Domains** non parte più da solo. I server di GitHub sono fuori dall'Italia: vedono
come funzionanti domini che i provider italiani bloccano, e non raggiungono siti che rifiutano le
connessioni dai datacenter, quindi metterebbe nella lista indirizzi sbagliati. Per un controllo
affidabile esegui `python update_domains.py` da un PC in Italia (serve `pip install curl_cffi`).

`domain_resolver.py` è una copia di `OmniVision/utils/domain_resolver.py`: se cambi uno dei due, aggiorna l'altro.
