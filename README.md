# D2R Classic Mod Patcher (Locked Baseline)


---

## PatchR54 — Cow Focus Boost

**Base:** PatchR53b  
**Date:** 2026-02-12

### Changes
- Fixed `apply_cow_test_drop_injection` signature (ensures trailing `:`) and standardized signature: `(mod_root, report, enabled=True)`.
- Boosted cowtest drop probability for forge-testing bases:
  - Sacred Armor (`uar`)
  - Ancient Sword (`9wd`)
  - Shako (`uap`)
- Implemented boost using `tc_rows` alias for consistency.

### Notes
- Boost runs only during cowtest (`--cowtest`).
- Non-destructive: adjusts `Prob1` only; does not modify NoDrop or replace entries.


---

## PatchR54b — Cowtest Signature Fix

**Base:** PatchR54_CowFocusBoost_v4  
**Date:** 2026-02-12

### Changes
- Fixed `apply_cow_test_drop_injection` signature to accept 3rd param: `enabled=True`.
- Added early-out guard when cowtest flag is off.
- No `__pycache__` / `.pyc` packaged.

---

## PatchR55 — Enable Ancient Sword Base for Classic (Cowtest Atlantean Drops)

**Base:** PatchR54b (Cowtest Signature Fix)
**Date:** 2026-02-12

### Changes
- Enabled Ancient Sword base (`weapons.txt` code `9wd`) for Classic by forcing `version=0` (and `enabled=1` if present).
- This makes CowTest injection entries for `9wd` actually droppable in Classic, so you can farm Ancient Sword bases to test Atlantean forging.


---

## PatchR56 — Cow Focus Include Ancient Sword

**Base:** PatchR55  
**Date:** 2026-02-12

### Changes
- Added Ancient Sword base code (`9wd`) to cowtest focus list so it is injected/boosted alongside `uar` and `uap`.

### Notes
- Non-destructive cowtest injection preserved.


---

## PatchR71 — Atlantean stable template port (r52z proven)

**Base:** PatchR56_CowFocus_Add9wd  
'**Date:** 2026-02-14

### Change
- Replaced Atlantean logic with the proven r52z in-place template port:
  - `apply_classic_port_atlantean_vanilla_key_r29_template(mod_root, report)`
  - Forces Classic Ancient Sword (code=9wd) to vanilla key **"The Atlantian"** and restores stats including **+2 paladin**.
- Removed any other Atlantean mutators from `main()` to avoid double-patching / ambiguity.


---

## PatchR72 — Atlantean Option B: Clone-from-Vanilla Only

**Base:** PatchR71  
**Date:** 2026-02-14

### Changes
- Atlantean now uses **clone-from-vanilla only** (Option B):
  - Loads vanilla from `--vanilla\data\global\excel\uniqueitems.txt`
  - Clones verbatim into Classic `code=9wd`, `version=0`, forces engine key `The Atlantian`
  - No abbreviation (`paladin` remains `paladin`)
- Removed/disabled any secondary Atlantean template passes to prevent overwrites.
- Standardized `read_tsv()` to accept `str|Path` and return `(header, rows, nl)`.

## PatchR73_AtlanteanTokenMaxrollFix (2026-02-15)
- Atlantean: keep vanilla prop token `pal` (do not use `paladin`) to restore +2 Paladin Skills behavior.
- Atlantean: make Enhanced Damage perfect in-template (250/250) so later postpasses do not undo maxroll.
