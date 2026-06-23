# D2R Classic++ Forge & Port Patcher

## Current Branch Canon

**R200 Stage4 Zero-Flag Canon**  
**+ Direct Flat Mixed Cow Pool**  
**+ TC Enrichment Flat No-Focus**  
**+ Quest/Restricted Filter**  
**+ LoD Azurewrath Preference**  
**+ UITOGGLE Restored**  
**+ Full Max-Roll Suite Restored**

This README describes the currently locked branch canon.

---

## Target Game Version

Built and validated against:

**Diablo II: Resurrected (PC) — Classic Offline**  
**Build: v1.6.81914**

Scope: **Classic only**. Expansion content may be selectively ported into Classic through the patcher.

---

## Expected Folder Layout

Place the patcher files and source folders together:

```text
C:\D2Rmod\
  patcher.py
  patch.bat
  vanilla\
  static_mod\
  patch_sources\
```

The `vanilla\` folder must contain the extracted D2R TXT database for **v1.6.81914**, including:

```text
vanilla\data\global\excel\
```

The patcher never reads or writes `.bin` files.

---

## How To Run

### Standard build

Run:

```bat
patch.bat
```

or:

```bash
python patcher.py
```

This is a **zero-flag canon runner**. Gameplay settings are baked into the patcher/batch profile. Do not pass the old `--vanilla`, `--out`, or stage flags.

### Output folder

The generated mod is written to:

```text
C:\D2Rmod\output\
```

Expected generated layout:

```text
output\mods\qol\qol.mpq\data\global\excel\
```

---

## Canon Profile

The locked branch profile is:

```text
ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
COWALLBASES=1
COWCHAOS=1
EXP_DROPS_STAGE=4
UITOGGLE=0
LODSTASH=1
COWALWAYSDROP=1
NOLOWQUALITY=1
COW_ALLBASES_SEED=1782137524
COW_ALLBASES_POOL_SIZE=45
COW_ALLBASES_WRAP_PROB=8192
COW_DIRECT_POOL=1
COW_FLAT_MIXED_POOL=1
TC_ENRICH_FLAT_NO_FOCUS=1
QUEST_FILTER=1
PREFER_LOD_AZUREWRATH=1
```

`UITOGGLE` remains configurable through `patch.bat` or the environment while keeping the patcher zero-flag.

Default:

```bat
set "UITOGGLE=0"
```

Enable UI layout overrides:

```bat
set "UITOGGLE=1"
```

---

## Current Canon Scope

This branch includes:

- Stage0 QoL baseline
- deterministic cube forge injection
- LoD → Classic port layer
- quest/restricted item filtering
- LoD Azurewrath preference
- direct flat mixed cow pool
- TC enrichment flat no-focus mode
- Stage4 cow drop forcing
- Stage5 stash / controller support
- full max-roll normalization
- Holy Fire / Holy Freeze / Holy Shock flat-damage mechanics
- Holy aura tooltip cleanup
- uniqueitems anti-corruption guard
- Stage1 cow harness retained for testing only

JAVE-specific Stage1 harness work remains **parked indefinitely** due to likely Classic engine-side behavior.

---

## Stage0 — Always On QoL Baseline

Stage0 is unconditional and independent of drop stages.

Includes:

- deterministic cube forge injection with schema-robust header normalization
- Andariel always quest-drop fix while preserving Classic quest-drop behavior
- Token of Absolution `version=0` safeguard
- stack adjustments:
  - Keys: 100
  - Tome of Town Portal: 100
  - Tome of Identify: 100
  - Arrows/Bolts: 999
- `ShowLevel=1` for armor and weapons
- InTown skill overrides
- relaxed base requirements for armor and weapons
- unique level requirement = 0
- set level requirement = 0

Stage0 is stable and always enabled.

---

## Deterministic Forge System

### Unique Forge

- cube output: `usetype,uni`
- reagent: `isc`
- inputs: normal + superior
- jewelry: magic-only inputs, such as `ring,mag` / `amul,mag`

### Set Forge

- cube output: `usetype,set`
- reagent: `key`

The engine may still enforce one-copy-per-unique-per-session behavior.

### Sacred Armor Forge Note

Sacred Armor uses base code:

```text
uar
```

Both **Templar's Might** and **Tyrael's Might** use Sacred Armor. The canon branch does **not** suppress either one. A generic unique Sacred Armor forge may resolve as either valid Sacred Armor unique.

---

## LoD → Classic Port Layer

The port layer selectively enables stable LoD content in Classic.

Rules:

- stable itemtype families only
- Assassin / Druid class-locked items excluded
- class-skill tokens such as `ass` and `dru` filtered
- quest/restricted bases excluded
- no TSV row reordering
- no row cloning
- ports occur in-place using `version=0` and `enabled=1`
- corresponding base white items are enabled
- base enablement does not require a unique / set mapping

Eligibility is independent of the Stage1 harness.

### Quest / Restricted Filter

Quest-only and restricted bases are blocked from:

- LoD → Classic unique porting
- cow-all-bases mixed pool
- direct cow pool route
- TC enrichment

Examples intentionally filtered:

```text
Amulet of the Viper
Hell Forge Hammer
Horadric Staff
KhalimFlail
Staff of Kings
SuperKhalimFlail
```

---

## Azurewrath Canon Rule

Azurewrath has a Classic-era duplicate and a LoD-era version.

Current canon prefers:

```text
Azurewrath → Phase Blade → 7cr
```

The older Classic Azurewrath row is suppressed:

```text
Azurewrath → Crystal Sword → crs
```

This is unique-row filtering only. Phase Blade itself remains valid in the base pool.

---

## Max-Roll Normalization

The full item roll suite runs after the port layer and requirement relax passes.

Affected tables:

- `uniqueitems.txt`
- `setitems.txt`
- `automagic.txt`
- `magicprefix.txt`
- `magicsuffix.txt`

Behavior:

- unique items forced to perfect stat rolls
- set items forced to perfect stat rolls
- set bonus ranges forced to max where schema-safe
- magic prefixes forced to max where schema-safe
- magic suffixes forced to max where schema-safe
- automagic ranges normalized where applicable
- chance-to-cast / charged-skill style properties are skipped where `min` and `max` are separate parameters rather than a roll range

Expected log summary:

```text
[item-rolls] COMPLETE: unique/set/automagic/magicprefix/magicsuffix rolls forced to max where schema-safe
```

---

## Stage4 Cow Drops / Direct Flat Mixed Pool

The current branch restores the old R200-style mixed cow pool while keeping it deterministic and safer.

Active behavior:

- cows always drop at least one item through `NoDrop=0`
- cow-related TreasureClass rows are patched
- HellBovine and Cow King monstats TC columns are patched
- direct cow pool route is enabled
- Normal, Nightmare, and Hell cows use the same flat mixed wrapper
- every eligible base code has equal per-code weight
- quest/restricted bases are filtered
- TC enrichment uses flat no-focus mode

Important constants:

```text
COW_ALLBASES_SEED=1782137524
COW_ALLBASES_POOL_SIZE=45
COW_ALLBASES_WRAP_PROB=8192
COW_DIRECT_POOL=1
COW_FLAT_MIXED_POOL=1
TC_ENRICH_FLAT_NO_FOCUS=1
```

Expected log examples:

```text
[cow-all-bases] FLAT MIXED POOL: every eligible base code has equal weight; Normal/Nightmare/Hell cows use the same wrapper
[cow-all-bases] Wrapper: ALL_DIFFICULTIES=zz_cow_allbases_wrap_FLAT
[tc-enrichment] FLAT NO-FOCUS: no hand-picked focus bases; all injected base entries use equal prob=1
```

### Ancient Armor / Sacred Armor Parity

Ancient Armor and Sacred Armor are both expected to be present in the flat mixed cow pool:

```text
Ancient Armor = aar
Sacred Armor  = uar
```

They are treated with equal per-code weight in the custom cow pool.

---

## Stage1 Cow Harness / TC Testing

The Stage1 cow harness function is retained for diagnostics and non-canon testing.

Normal canon generation keeps it disabled:

```text
[stage1-cow] Disabled; skipped
```

The harness remains useful for isolated TC/category testing, but it is not part of normal Stage4 canon generation.

### JAVE Status

JAVE is not part of the current canon harness path.

Reason:

- repeated testing showed JAVE behavior that does not match other itemtype families
- evidence suggests Classic engine-specific handling rather than a remaining patcher-structure issue

JAVE remains **parked / non-canon research**.

---

## Holy Aura Flat-Damage Canon Change

The patcher enforces flat damage for:

- Holy Fire
- Holy Freeze
- Holy Shock

### Mechanics

The patcher copies max-side elemental progression into the min-side elemental progression, so aura pulse damage becomes flat-max.

It also copies the passive max-damage stat / calc into the passive min-damage stat / calc, so aura-added weapon damage becomes flat-max as well.

### Tooltip / UI

The patcher rewrites the relevant `skilldesc.txt` rows so the visible damage line is:

- a single numeric line
- no duplicate damage line
- sourced from the true max-side display expression

Result:

- no ranged-looking holy aura tooltip
- no duplicate holy aura damage line
- visible value tracks the intended former max side

---

## Stage5 Stash / Controller Support

Stage5 stash support is active by default through:

```text
LODSTASH=1
```

Includes:

- bank layout generation from vanilla expansion deltas
- Classic personal stash grid expanded to match Big Bank Page 1
- controller bank layout generation

---

## UI Toggle

`UITOGGLE` controls whether D2R layout overrides are active.

Default canon:

```text
UITOGGLE=0
```

When disabled, UI JSON overrides are copied but renamed to `disable*` filenames so the game does not load them.

When enabled, the active JSON layout files are copied from `patch_sources`.

---

## Anti-Corruption Guarantees

The patcher preserves stability by enforcing:

- vanilla TSV header order preserved
- no row reordering
- no unique row cloning
- no unstable ID remapping
- static payload cannot override generated gameplay `.txt`
- stale static gameplay `.txt` files are purged / guarded
- cubemain injection is schema-normalized
- forge recipe validation after injection
- uniqueitems header, row count, `*ID` uniqueness, and `*ID` order guard

If the uniqueitems guard fails, do not use the generated output.

---

## Expected Clean Log Markers

A healthy run should include:

```text
[stage1-allowlist] stable_types=22 excluded_types=9
[canon-profile] ...
[ui] UITOGGLE=0 ...
[azurewrath] preferred LoD Azurewrath base=7cr ...
[qol-stage0] APPLIED ...
[skills-flat-holy] APPLIED ...
[skilldesc-holy-direct] APPLIED ...
[stage0-itemratio] NOLOWQUALITY_V4 ...
[stage4-cow] Patched 11 cow TreasureClassEx row(s) ...
[classic-port] ...
[cow-all-bases] FLAT MIXED POOL ...
[tc-enrichment] FLAT NO-FOCUS ...
[item-rolls] COMPLETE ...
[uniq-lvlreq] ...
[set-lvlreq] ...
[uniqueitems-guard] OK ...
[stage5-stash] APPLIED ...
```

---

## Notes

- This branch is Classic-only.
- The current canon priority is stability, reproducibility, clean Classic behavior, and in-game practical testing.
- JAVE remains parked until an explicit engine-focused research pass is resumed.
- Tyrael's Might / Templar's Might are intentionally left neutral because both share the Sacred Armor base.

---

End of documentation.
