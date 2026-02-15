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
- Uniques: forced to **perfect rolls** (min → max) via post-pass normalization.
- Sets: forced to **perfect rolls** (min/amin → max/amax) via post-pass normalization.
- Magic affixes:
  - `magicprefix.txt` maxroll enforcement
  - `magicsuffix.txt` maxroll enforcement
- `automagic.txt` normalization where applicable.

### Classic Ports / Canonical Bases
- **Shako base enabled** for Classic (code `uap`).
- **Sacred Armor base enabled** for Classic (code `uar`).
- **Harlequin Crest** remapped deterministically onto Shako base (Classic-safe mapping).
- **Tyrael’s Might** mapped to Classic-compatible Sacred Armor (uar) and made forgeable.

### Atlantean (Ancient Sword) — Correctness Fix (PatchR73)
- Atlantean base: Ancient Sword (code `9wd`).
- Restores **+2 Paladin Skills** by using correct property token: `pal` (NOT `paladin`).
- Enforces **perfect Enhanced Damage** in the Atlantean template to prevent non-perfect rolls.
- Keeps naming/color stability via the Classic engine key handling (Classic “Atlantian” key behavior).

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
