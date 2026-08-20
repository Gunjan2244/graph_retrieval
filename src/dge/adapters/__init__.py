"""Vendor-specific adapters.

CLAUDE.md code convention: "no vendor SDK imported outside its adapter
module." Every file in this package imports exactly one vendor thing (fastembed,
a hosted HTTP API) and implements a Protocol from `dge.interfaces`. Core logic
never imports from here directly — it depends on the Protocol and the caller
chooses which adapter to inject.
"""
