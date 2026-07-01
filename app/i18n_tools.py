"""Utilidades del sistema de traducción comunitaria (Rosetta).

Compila una plantilla de patrón (origen español con tokens {n}/{s} y su traducción con los
mismos tokens) a una regla [regex, sub] que consume el motor i18n del cliente. Espejo de
scrape/build_patterns.py: {n}=número (\\d+), {s}=texto (.*?); el regex se ancla ^…$ y casa
contra el texto ESPAÑOL renderizado; la sustitución usa $1,$2… en el orden de aparición.
"""
import re

_TOKEN = re.compile(r"(\{[ns]\})")
_WORD = re.compile(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")


def _seq(s: str):
    out = []
    for p in _TOKEN.split(s or ""):
        if p == "{n}":
            out.append(("ph", "n"))
        elif p == "{s}":
            out.append(("ph", "s"))
        elif p != "":
            out.append(("lit", p))
    return out


def compile_pattern(es_tpl: str, target_tpl: str):
    """(es_tpl, target_tpl) -> [regex, sub] o None si no casa (nº de tokens distinto o
    literal demasiado corto para ser específico)."""
    es_seq, tg_seq = _seq(es_tpl), _seq(target_tpl)
    es_ph = [v for k, v in es_seq if k == "ph"]
    tg_ph = [v for k, v in tg_seq if k == "ph"]
    if not es_ph or len(es_ph) != len(tg_ph):
        return None
    lits = "".join(v for k, v in es_seq if k == "lit")
    if len(_WORD.sub("", lits)) < 3:
        return None
    rx = "^"
    for k, v in es_seq:
        rx += re.escape(v) if k == "lit" else (r"(\d+)" if v == "n" else r"(.*?)")
    rx += "$"
    sub, i = "", 0
    for k, v in tg_seq:
        if k == "ph":
            i += 1
            sub += "$" + str(i)
        else:
            sub += v
    return [rx, sub]
