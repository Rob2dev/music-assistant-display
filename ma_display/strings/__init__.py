"""All user-visible texts, one file per language (English is the fallback).

Every file defines NAME (what the language is called in itself, shown in the language list) and TEXTS (the texts).
English is the fallback for a missing key or language, so a half-finished translation never leaves a blank on
screen. Keep texts short: the layout is sized for the shortest language, and long ones are cut off with an
ellipsis (`python -m unittest discover -s tests -t tests` checks that every text fits).

To add a language: copy en.py to <code>.py, translate it, and add the code to CODES below.
"""
from importlib import import_module

CODES = ("en", "nl", "fr", "de", "es", "it", "pt", "pl", "sv", "cs")  # the order of the language list

_modules = {code: import_module("." + code, __name__) for code in CODES}
STRINGS = {code: mod.TEXTS for code, mod in _modules.items()}
NAMES = {code: mod.NAME for code, mod in _modules.items()}

__all__ = ["STRINGS"]
