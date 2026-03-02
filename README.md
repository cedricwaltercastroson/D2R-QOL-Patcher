# D2R Classic++ Forge & Port Patcher (Canonical R127)

## Target Game Version (Hard-Pinned)

Built and validated against:
**Diablo II: Resurrected (PC) — Classic Offline**
**Build: v1.6.77312**

Scope: Classic only. Expansion content may be safely ported into Classic via this patcher.

---

# Canonical Baseline

This build is locked to:

PatcherR127_RETHINK_CUBESETLVLREQ

All prior patch numbers and legacy changelog sections are deprecated.
Future modifications must increment revision >= R128.

---

# Stage0 — Always On (QoL Baseline)

Stage0 is unconditional and independent of drop stages.

Includes:

- Deterministic cube forge injection (schema-robust, header-normalized)
- Andariel quest-drop fix
- TOA version=0 safeguard
- Stack adjustments (Classic):
  - Keys: 50
  - Tome of Town Portal: 80
  - Tome of Identify: 80
  - Arrows/Bolts unchanged (500)
- ShowLevel=1 for armor/weapons
- InTown skill overrides
- Relaxed base requirements (armor/weapons)
- Unique level requirement = 0
- Set level requirement = 0

Stage0 is frozen and considered stable.

---

# Deterministic Forge System

## Unique Forge
- Cube output: usetype,uni
- Reagent: isc
- Inputs: normal + superior
- Jewelry: magic-only inputs (ring,mag / amul,mag)

## Set Forge
- Cube output: usetype,set
- Reagent: key

Engine enforces one-copy-per-unique-per-session rule.

---

# Port Layer (LoD → Classic)

Governed by a single canonical allowlist.

Rules:

- Stable itemtype families only
- Assassin/Druid class-locked items excluded
- Class-skill tokens (ass, dru) filtered
- No TSV row reordering
- No row cloning
- Ports occur in-place (version=0, enabled=1)
- Corresponding base white items enabled
- Base enablement does NOT require unique/set mapping (gated variety)

Eligibility is independent of harness state.

---

# Maxroll Normalization

Runs after port layer:

- Unique items forced to perfect rolls
- Set items forced to perfect rolls
- Magic prefix/suffix maxroll enforcement
- Automagic normalization

---

# Anti-Corruption Guarantees

- Vanilla TSV header order preserved
- No row reordering
- No unstable ID remapping
- Cubemain injection immune to schema drift
- Build fails if forge recipes are missing

---

# How To Run

Standard build:

python patcher.py --vanilla "C:\vanilla" --out "C:\output"

Enable full drop stages:

python patcher.py --vanilla "C:\vanilla" --out "C:\output" --enable-expansion-drops-in-classic --exp-drops-stage 4

---

# Output Layout

<out>\mods\qol\qol.mpq\data\global\excel\

---

End of canonical documentation.
