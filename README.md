
# D2R Classic Deterministic Forge Mod

## Base Game Version
Built and validated against:
Diablo II Resurrected Classic Offline — v1.4.71776

---

## Core Features

### Deterministic Unique Forging
- Uses usetype,uni cube outputs
- Supports low / normal / superior bases
- No RNG fallback
- Stable across save files

### Deterministic Set Forging
- Uses usetype,set outputs
- Perfect roll enforcement

### Perfect Affix Normalization
- All uniques forced to max rolls
- All sets forced to max rolls
- Magic prefixes/suffixes normalized
- Automagic stats normalized

### Classic Compatibility Ports
- Shako enabled for Classic
- Sacred Armor enabled for Classic
- Atlantean restored correctly
- Tyrael’s Might mapped safely to Sacred Armor

### Atlantean Fixes
- Uses correct token "pal" for +2 Paladin Skills
- Enhanced Damage forced to perfect roll
- Vanilla row used as authoritative source

### Level Requirement Adjustments
- Sacred Armor base lvlreq set to 1
- Tyrael’s Might lvlreq removed
- Unique lvlreq normalized

### Cow Level Test Mode
Enabled via:
--cowtest OR COWTEST=1

Adds high‑quality drop boosts for:
- Sacred Armor
- Ancient Sword
- Shako bases

Does NOT overwrite vanilla treasure classes.

### Structural Safeguards
- TSV integrity checks
- Unique ID stability protection
- No row reordering
- No legacy remap logic

---

## Patcher Design Principles

1. Vanilla is the source of truth
2. Never reorder TSV rows
3. All mapping is in‑place
4. Maxroll normalization runs last

---

## Stability Guarantee

This architecture prevents:
- Unique scrambling
- Save corruption
- Incorrect forging outputs

