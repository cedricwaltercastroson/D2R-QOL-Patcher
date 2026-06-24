# D2R Classic++ R200 — Locked Stable Canon

This is the current stable R200 release package for **Diablo II: Resurrected Classic Offline**.

Target build: **v1.6.81914**

## What this mod does

- Brings the selected Expansion item pool into Classic.
- Improves cow-level farming with a broader flat mixed item base pool.
- Keeps Cow King on his normal boss-loot behavior.
- Keeps Ancient Armor and Sacred Armor available.
- Uses the LoD Azurewrath Phase Blade version and suppresses the old duplicate Classic Azurewrath.
- Enables the stable Javelin and Amazon-specific item families that were tested successfully.
- Forces supported unique, set, and magic affix rolls to their maximum safe values.
- Applies the existing quality-of-life changes, stash support, and Holy aura flat-damage cleanup.

## Included files

```text
patcher.py
patch.bat
README.md
MANIFEST.md
```

## Folder layout

Place the package files in your normal mod working folder:

```text
C:\D2Rmod\patcher.py
C:\D2Rmod\patch.bat
C:\D2Rmod\vanilla\
C:\D2Rmod\static_mod\
C:\D2Rmod\patch_sources\
```

## How to run

Run:

```bat
patch.bat
```

Expected output:

```text
C:\D2Rmod\output\mods\qol\qol.mpq\
```

## Expected gameplay behavior

Regular cows use the expanded mixed item pool.

Cow King keeps his normal boss-loot route and is not converted into a regular cow-drop sampler.

Javelin and Amazon-specific item bases are included using the stable Classic-safe setup. These items are part of the normal pool; they are not forced drops.

## Notes

This package is the current locked stable canon. Future experiments should branch from this package and should not replace it unless explicitly promoted to canon.
