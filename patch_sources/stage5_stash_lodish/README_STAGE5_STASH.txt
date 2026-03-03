Stage 5 (Large Stash / LoD Stash Port) Payload Folder

Drop the LoD-style stash UI/layout override files here using their real in-mod relative paths.

Everything under:
  patch_sources/stage5_stash_lodish/
is copied verbatim into:
  <output>/mods/<modname>/<modname>.mpq/

when you run:
  python patcher.py ... --stash-lodish
or:
  patch.bat stage5

Rules:
- Stage 0–4 remain immutable.
- Stage 5 must be additive: ONLY files placed in this folder are applied.
- No JSON parsing/resave: files are copied raw.

Suggested contents (examples; verify against your vanilla dump):
  data/global/ui/layouts/...
  data/global/ui/panels/...
  data/hd/global/ui/...

