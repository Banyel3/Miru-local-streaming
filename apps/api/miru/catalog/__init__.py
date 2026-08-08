"""Catalog — the browse wall's source of truth.

Pure functions only in this layer: classification, release-name parsing and
ranking. No I/O, no database, no indexer calls, so the rules that decide what
the wall shows are testable without a network or a PC.
"""
