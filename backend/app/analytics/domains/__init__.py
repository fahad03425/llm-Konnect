"""
Domain-specific KPI packs for the Module 6.6 KPI engine.

Everything here is registered onto `KPIEngine` through a `DomainPack.register_kpis`
hook and is invisible to other domains. The engine core (`models`, `filters`,
`engine`, `kpi`, `seam`) stays free of domain vocabulary — a grep over those files
must find no domain nouns, and a test enforces it.

Modules in this package are NOT imported eagerly: the engine loads a domain the
first time it is asked to do work for that domain.
"""
