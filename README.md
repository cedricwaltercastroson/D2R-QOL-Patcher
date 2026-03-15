# D2R Classic++ Forge & Port Patcher (R200 Canon)

## Target Game Version

Built and validated against:

**Diablo II: Resurrected (PC) — Classic Offline**  
**Build: v1.6.81914**

Scope: **Classic only**. Expansion content may be selectively ported into Classic through the patcher.

---

## Current Canon Scope

This canon build includes:

- Stage0 QoL baseline
- deterministic forge injection
- LoD → Classic port layer
- maxroll normalization
- Stage4 cow drop forcing
- Stage5 stash / controller support
- Stage1 TC hard-materialize + cow alias verification logic
- Holy Fire / Holy Freeze / Holy Shock flat-damage mechanics
- Holy aura tooltip cleanup (single visible true-max damage line)

---

## Stage0 — Always On (QoL Baseline)

Stage0 is unconditional and independent of drop stages.

Includes:

- deterministic cube forge injection (schema-robust, header-normalized)
- Andariel always quest-drop fix while preserving Classic quest-drop behavior
- TOA `version=0` safeguard
- stack adjustments (Classic):
  - Keys: 100
  - Tome of Town Portal: 100
  - Tome of Identify: 100
  - Arrows/Bolts: 999
- `ShowLevel=1` for armor / weapons
- InTown skill overrides
- relaxed base requirements (armor / weapons)
- unique level requirement = 0
- set level requirement = 0

Stage0 is frozen and considered stable.

---

## Deterministic Forge System

### Unique Forge

- cube output: `usetype,uni`
- reagent: `isc`
- inputs: normal + superior
- jewelry: magic-only inputs (`ring,mag` / `amul,mag`)

### Set Forge

- cube output: `usetype,set`
- reagent: `key`

Engine still enforces the one-copy-per-unique-per-session rule.

---

## Port Layer (LoD → Classic)

Governed by a single canonical allowlist.

Rules:

- stable itemtype families only
- Assassin / Druid class-locked items excluded
- class-skill tokens (`ass`, `dru`) filtered
- no TSV row reordering
- no row cloning
- ports occur in-place (`version=0`, `enabled=1`)
- corresponding base white items enabled
- base enablement does **not** require unique / set mapping

Eligibility is independent of harness state.

---

## Maxroll Normalization

Runs after the port layer:

- unique items forced to perfect rolls
- set items forced to perfect rolls
- magic prefix / suffix maxroll enforcement
- automagic normalization

---

## Stage1 Cow Harness / TC Fixes

The canon patcher keeps the generic Stage1 TC safety work:

- hard-materializes the synthetic Stage1 root + chunk rows into `treasureclassex.txt`
- forces known cow-related alias TCs to the Stage1 root
- verifies those rows exist after save
- patches HellBovine / Cow King monstats TC fields when Stage1 harness is enabled

---

## Holy Aura Flat-Damage Canon Change

The canon patcher now enforces flat damage for:

- Holy Fire
- Holy Freeze
- Holy Shock

### Mechanics

The patcher copies the max-side elemental progression into the min-side elemental progression, so the aura pulse damage becomes flat-max.

It also copies the passive max-damage stat / calc into the passive min-damage stat / calc, so the aura-added weapon damage becomes flat-max as well.

### Tooltip / UI

The patcher rewrites the Holy Fire / Holy Freeze / Holy Shock `skilldesc.txt` rows so the visible damage line is:

- a single numeric line
- no duplicate damage line
- sourced from the true max-side display expression

Result:
- no more ranged-looking holy aura tooltip
- no duplicate holy aura damage line
- visible value tracks the intended former max side

---

## Anti-Corruption Guarantees

- vanilla TSV header order preserved
- no row reordering
- no unstable ID remapping
- cubemain injection immune to schema drift
- build fails if forge recipes are missing

---

## How To Run

Prerequisite:

A vanilla TXT database dump from the exact D2R build you are targeting.

### Quick build

Run:

```bat
patch.bat
```

### Direct build

```bash
python patcher.py --vanilla "C:\vanilla" --out "C:\output"
```

### Enable full drop stages

```bash
python patcher.py --vanilla "C:\vanilla" --out "C:\output" --enable-expansion-drops-in-classic --exp-drops-stage 4
```

---

## Output Layout

```text
<out>\mods\qol\qol.mpq\data\global\excel\
```

---

## Notes

- The current canon priority is stability, reproducibility, and clean Classic behavior.

---

End of documentation.
