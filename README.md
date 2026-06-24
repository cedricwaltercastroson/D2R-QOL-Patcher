# D2R Classic++ R200 — LOCKED STABLE CANON

## Canon status

This is the currently locked stable R200 canon branch.

Use this branch as the baseline for future work unless a later branch is explicitly promoted to canon.

## Target game version

Diablo II: Resurrected (PC) — Classic Offline  
Build: **v1.6.81914**

## Canon identity

```text
R200 locked stable canon
Zero-flag runner
Stage4 direct flat mixed cow pool
JAVE stackless support
Amazon-specific support
Cow King native boss loot preserved
No harnesses
```

## Required canon flags

A correct run should show these flags in the log:

```text
ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
COWALLBASES=1
COWCHAOS=1
EXP_DROPS_STAGE=4
UITOGGLE=0
LODSTASH=1
COWALWAYSDROP=1
NOLOWQUALITY=1
COW_DIRECT_POOL=1
COWKING_NATIVE=1
COW_FLAT_MIXED_POOL=1
TC_ENRICH_FLAT_NO_FOCUS=1
QUEST_FILTER=1
PREFER_LOD_AZUREWRATH=1
JAVE_STACKLESS_SPEAR=1
AMAZON_SPECIFIC=1
AMAZON_SPECIFIC_HARNESS=0
```

Important: this stable canon does **not** use `COWKING_FORCE=1`. The forced Cow King route branch was experimental and is not the preferred canon.

## Canon systems retained

- Zero-flag R200 runner.
- Stage0 QoL baseline.
- LoD to Classic unique/set/base port layer.
- Stage4 direct flat mixed cow pool for regular cow farming.
- Cow King native boss TreasureClass preservation.
- TC enrichment flat/no-focus.
- Quest/restricted item filter.
- LoD Azurewrath preference: Phase Blade `7cr` preferred; old Classic Crystal Sword Azurewrath suppressed.
- Ancient Armor and Sacred Armor parity retained.
- Full max-roll suite:
  - uniqueitems
  - setitems
  - set bonuses
  - automagic
  - magicprefix
  - magicsuffix
- Stage5 stash/controller support.
- Holy Fire, Holy Freeze, and Holy Shock flat-damage mechanics with single true-max tooltip cleanup.

## Cow / Cow King behavior

Regular cows use the flat mixed R200 all-base pool.

Cow King is preserved on the native boss TreasureClass chain and is not flattened into the regular cow base sampler.

Expected log markers:

```text
COWKING_NATIVE=1
[cow-king] preserved native Cow King boss treasure classes
```

Expected behavior:

```text
Regular cows = flat mixed R200 all-base pool
Cow King     = native boss-style Cow King TreasureClass chain
```

The patcher must preserve native Cow King rows such as:

```text
Cow King
Cow King (N)
Cow King (H)
Cow King (H) Desecrated A/B/C/D
Cow King Item (H) Desecrated A/B/C/D
```

## JAVE canon rule

Classic monster TC drops were unstable when regular JAVE bases used the original stackable/throwable `jave` path. The stable fix is to keep original item codes/names/graphics while converting the rows to a stackless spear-like `spea` profile.

Canon regular JAVE codes:

```text
jav, pil, ssp, glv, tsp
9ja, 9pi, 9s9, 9gl, 9ts
7ja, 7pi, 7s7, 7gl, 7ts
```

Quantity-only unique properties are removed where unsafe:

```text
Gargoyle's Bite
Demon's Arch
Wraithflight
```

Removed unsafe properties:

```text
rep-quant
stack
```

## Amazon-specific canon rule

Amazon-specific bows, spears, and javelins are enabled through Classic-safe profiles.

```text
Amazon bows:     am1, am2, am6, am7, amb, amc  -> bow
Amazon spears:   am3, am4, am8, am9, amd, ame  -> spea
Amazon javelins: am5, ama, amf                 -> stackless spea
```

Titan's Revenge uses base `ama`. Because `ama` is canonically stackless in this branch, Titan's Revenge has quantity-only properties removed:

```text
rep-quant
stack
```

This is intentional. Stack/quantity behavior is not part of the stable Classic-safe thrown weapon path.

## Cube recipes

The normal custom cube recipe set is retained.

This canon branch does not include any diagnostic cube recipe such as:

```text
Identify Scroll -> Javelin
```

## What is intentionally not included

```text
No JAVE family harness
No Amazon-specific harness
No forced-only JAVE/Amazon cow testing
No nuclear TreasureClassEx proof
No Long Sword trap
No Identify Scroll -> Javelin diagnostic cube recipe
No Cow King forced boss route branch
No __pycache__
No .pyc
```

## Expected success markers

A correct canon log should contain:

```text
[jave-forward] STACKLESS SPEAR PROFILE
[jave-forward] unique quantity cleanup
[amazon-forward] CLASS-SPECIFIC STABLE PROFILE
[amazon-forward] unique quantity cleanup
[amazon-harness] disabled; normal Amazon forward pool remains active
[cow-all-bases] JAVE forward: injected 12 stackless JAVE base code(s) into cow sampler eligibility
[cow-all-bases] Amazon forward: injected 8 Amazon-specific base code(s) into cow sampler eligibility
[cow-all-bases] Parity check: AncientArmor(aar)=present; SacredArmor(uar)=present; per-code-weight=equal
[cow-king] preserved native Cow King boss treasure classes
[stage1-cow] Disabled; skipped
```

## How to run

Place these files beside your normal folders:

```text
patcher.py
patch.bat
vanilla/
static_mod/
patch_sources/
```

Run:

```bat
patch.bat
```

Expected output:

```text
output/mods/qol/qol.mpq/
```

## Stability note

This build is locked because the regular JAVE stability harness passed, the Amazon-specific stability harness passed, and the non-harness canon log confirms the safe profiles are active while harnesses are disabled.

Future experiments should branch from this canon, not replace it, unless explicitly promoted.
