# D2R Classic Deterministic Forge Mod (QoL Patcher)

## Base Game Version
Built and validated against **Diablo II: Resurrected — Classic Offline** (v1.6.x series vanilla dumps).

> Scope: **Classic only** (no Expansion features).

---

## What This Mod Does

### Deterministic Forging System
- Deterministic **Unique Forge** via cube output `usetype,uni` (Classic-safe patterns).
- Deterministic **Set Forge** via cube output `usetype,set` (Classic-safe patterns).
- Quality coverage for forging inputs:
  - Low quality (cracked/damaged)
  - Normal
  - Superior
- Jewelry consistency (Classic rule): rings/amulets forged from **magic** inputs only (no “normal” jewelry in Classic).

### Perfect Rolls / Maxroll Normalization
- Uniques: forced to **perfect rolls** (min → max) via post-pass normalization (runs *after* LoD→Classic ports so newly-enabled uniques are included).
- Sets: forced to **perfect rolls** (min/amin → max/amax) via post-pass normalization.
- Magic affixes:
  - `magicprefix.txt` maxroll enforcement
  - `magicsuffix.txt` maxroll enforcement
- `automagic.txt` normalization where applicable.

### Classic Ports / Canonical Bases

**Phase 1 (forge-only):** This patcher ports **all non-Assassin/Druid uniques** into Classic by:
- Enabling the unique row (`version=0`, `enabled=1`) **in place** (no clones, no row reordering).
- Enabling the corresponding **canonical base white item** for Classic in `armor.txt` / `weapons.txt` / `misc.txt` (`version=0`, `spawnable=1` where present).
- Skipping Assassin/Druid content via **two filters** (Classic original characters only):
  - Base-type class restriction (katars/pelts/etc.)
  - Unique property filter for `ass` / `dru` class-skill tokens (catches cases like **Earthshaker** on shared weapon bases)

**Why in-place?** Keeping `uniqueitems.txt` structurally identical to vanilla prevents the “jumbled uniques” corruption mode.

Notes:
- The game engine enforces the vanilla **one-copy-per-unique-per-game-session** rule (forge obeys this).
- Existing saves created under older remap schemes may show “invalid item” if the stored base differs; newly forged items are correct.

### Atlantean (Ancient Sword)
- The Atlantean is enabled for Classic **in place** on its original base: Ancient Sword (`9wd`).
- No string injection is performed; the build relies on vanilla string keys.
### Level Requirement Adjustments
- Removes/normalizes level requirements where required for testing (including Tyrael’s Might).
- Sacred Armor base requirement is forced to a low, Classic-safe value for practical testing.

### Cow Level Drops (Current)
- Uses the Cow Level base sampler toggles (`--cow-all-bases` / `--cow-all-bases-full`) and Phase 2 safe integration.
- Legacy CowTest injection has been removed as redundant.

### Misc QoL
- Stack sizes (Classic):
  - Keys: maxstack 50
  - Tome of Town Portal: maxstack 80
  - Tome of Identify: maxstack 80
  - Arrows/Bolts unchanged (500)
- `ShowLevel=1` enabled in armor/weapons tables.

---

## Patcher Architecture (Summary)

### Pipeline Order
1. Seed vanilla TSVs into output (preserve row order/headers).
2. TSV integrity checks (column counts, header stability).
3. Classic ports/mapping (Shako/Sacred Armor; Classic-safe remaps).
4. Forge recipe injection (unique + set + quality variants).
5. Cow Level drop integration (all-bases sampler / Phase 2 safe TC fill).
6. Maxroll post-pass (runs late so ports/remaps are included).
7. Final sync of static assets into output.

### Anti-Corruption Guarantees
- No TSV row reordering.
- No unstable unique key rewriting that scrambles saves.
- Maxroll normalization runs after mapping/ports.

### Design Rules
1. Vanilla is source of truth.
2. Don’t reorder TSV rows.
3. Mapping must be deterministic and in-place.
4. Normalization runs last.

---

## How To Run


### Phase 2 Drops (SAFE integration)
Phase 2 is **optional** and designed to be conservative:
- Integrates ported (non-Assassin/Druid) **base items** into natural `treasureclassex.txt` drops.
- **SAFE MODE:** fills **empty ItemN slots only** on high-level TCs (level >= 70); does not modify NoDrop/Picks or replace existing drops.

Enable with:
```bat
python patcher.py --vanilla "C:\vanilla" --out "C:\output" --enable-expansion-drops-in-classic
```

### Standard
```bat
python patcher.py --vanilla "C:\vanilla" --out "C:\output"
```


---

## Output Layout
```
<out>\mods\qol\qol.mpq\data\global\excel\
```

---

## Locked Baseline
**PatchR73_AtlanteanTokenMaxrollFix**:
- Atlantean `pal` token fix (+2 Paladin Skills works)
- Atlantean perfect ED enforced
- README is authoritative (no separate changelog)


### Cow Level XP Boost (monstats-only)
- Cow monsters (Hell Bovine, Cow King) grant **10× XP** by patching `monstats.txt` only.
- No changes to drop tables, area level, or global experience curves.


---

## PatchR76 — Legacy Tyrael/Chaos cleanup (no behavior change)

**Date:** 2026-02-15

### Cleanup
- Removed legacy Tyrael/Chaos Armor remap stubs and misleading report lines:
  - Removed `[tyrael] legacy remap assignment removed...`
  - Removed `[tyrael] Legacy Goldskin->Tyrael repurpose assertion skipped...`
- Removed unused Chaos Armor host logic (`[chaos]` tagged functions).
- Kept active Tyrael hosting on **Sacred Armor (uar)** unchanged.


---

## PatchR78 — Dead code pruning pass (safe)

**Date:** 2026-02-16

### What changed
- Removed unreferenced legacy/disabled helper functions (name/docstring flagged) to reduce maintenance surface.
- No intended gameplay/logic changes.
- No TSV row ordering changes.


---

## PatchR79 — UI overrides toggle (disabled by default)

**Date:** 2026-02-16

### What changed
- Added `--enable-ui` flag.
- Default behavior: UI layout overrides are copied then renamed to:
  - `disable_profilehd.json`
  - `disable_profilelv.json`
  - `disable_profilesd.json`
  - `disableglobaldata.json`
  - `disableglobaldatahd.json`
  This prevents the game from loading them unless explicitly enabled.


---

## PatchR80 — patch.bat toggle parity

**Date:** 2026-02-17

### What changed
- Updated `patch.bat` to provide gameplay/UI toggles via environment switches:
  - `set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1` enables Phase 2 safe drop integration
  - `set COWALLBASES=1` enables the Cow Level base sampler (`--cow-all-bases`)
  - `set COWCHAOS=1` enables full chaos mode (`--cow-all-bases-full`)
  - `set UITOGGLE=1` enables UI layout overrides (`--enable-ui`)
- Default behavior remains unchanged.

## Cow base sampler (optional)

Flags:
- `--cow-all-bases` : Cow Level only. Adds difficulty-scaled Treasure Classes so cows can drop a wide spread of base items on Normal/Nightmare/Hell.
- `--cow-all-bases-full` : Full chaos mode. All base tiers equally likely regardless of difficulty. Implies `--cow-all-bases`.

