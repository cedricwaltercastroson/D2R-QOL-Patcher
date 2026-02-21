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
- Skipping Assassin/Druid class-locked bases/uniques (Classic original characters only).

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

### Cow Level Test Mode (Toggleable)
Enable via either:
- CLI: `--cowtest`
- Or environment toggle used by the batch wrapper: `set COWTEST=1`

Behavior:
- Injects **non-destructive** Cow TC boosts (does not permanently replace vanilla tables).
- Focus boosts to speed up forging validation runs:
  - Sacred Armor (`uar`) bases
  - Ancient Sword (`9wd`) bases
  - Shako bases (`uap` / War Hat family tests)

Disable:
- `set COWTEST=0` (or omit `--cowtest`) returns to standard drop behavior.

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
5. Cowtest injection (only if enabled; non-destructive TC edits).
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
python patcher.py --vanilla "C:\vanilla" --out "C:\output" --phase2drops
```

### Standard
```bat
python patcher.py --vanilla "C:\vanilla" --out "C:\output"
```

### With Cow Test
```bat
set COWTEST=1
python patcher.py --vanilla "C:\vanilla" --out "C:\output" --cowtest
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

## PatchR80 — patch.bat UI toggle parity

**Date:** 2026-02-17

### What changed
- Updated `patch.bat` to provide CowTest + UI toggle parity using environment switches:
  - `set COWTEST=1` enables Cow Level test drop injection (`--cowtest`)
  - `set UITEST=1` enables UI layout overrides (`--enable-ui`)
- Default (`0/0`) behavior remains unchanged.