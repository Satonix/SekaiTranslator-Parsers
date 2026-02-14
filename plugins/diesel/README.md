Diesel engine - NUT strings parser

Folder: plugins/diesel

Implements the same interface as plugins/artemis/plugin.py:
- plugin object with plugin_id, name, extensions
- detect(ctx, text) -> float
- parse(ctx, text) -> list[dict]
- rebuild(ctx, entries) -> str

Binary handling:
- Assumes loader provides text as latin1 1:1 bytes mapping.
