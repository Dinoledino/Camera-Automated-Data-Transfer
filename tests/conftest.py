import gc

from cadt.indexer import on_android

# Sur Android (Termux, Python 3.14.10), le ramasse-miettes provoque un plantage intermittent du
# code compilé pendant le traitement d'image (constaté le 2026-11-25 ; défaut hors de ce projet,
# jamais reproduit sur PC). On le désactive pendant les tests, comme dans le processus des aperçus.
if on_android():
    gc.disable()
