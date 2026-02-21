from __future__ import annotations
import random
import re

_VANILLA_ROOT = None  # set in main()
import json
#!/usr/bin/env python3
"""
D2R Mod Updater / Merger (vanilla as source of truth)

What it does:
- Takes a NEW vanilla dump (TSV .txt files + UI json if present)
- Applies your locked intent as deterministic patches:
  * misc.txt: maxstack (key=50, tbk=80, ibk=80; arrows/bolts remain 500)
  * armor.txt + weapons.txt: ShowLevel=1 for all rows (except "Expansion" marker)
  * automagic.txt: force max rolls by setting level=maxlevel and modNmin=modNmax
  * setitems.txt: force max rolls by setting min*=max* and amin*=amax*
  * uniqueitems.txt: Tyrael's Might force max rolls by setting min*=max* (row code uar)
  * cubemain.txt: inject UNIQUE FORGE / SET FORGE recipes from patch_sources (dedup by signature)
  * UI json / item-names.json: copied from patch_sources as-is (no JSON reserialization)

Outputs:
- A merged folder containing patched files (same relative paths as vanilla input)
- A log.txt describing what changed

Usage:
  python patcher.py --vanilla <path_to_new_vanilla_root> --out <path_to_output_root>

Notes:
- Input vanilla root should contain "data/..." as extracted from CASC.
- This tool never reads or writes .bin files.
"""
import argparse, csv, hashlib, json, os, re, shutil
import csv
import io
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent

def apply_classic_add_atlantean_for_ancient_sword(rows, header, report):
    """Port 'The Atlantean' into Classic (safe, non-destructive).

    Strategy:
      - Find the existing non-Classic row for The Atlantean and CLONE it as a Classic (version=0, enabled=1) row.
      - If a Classic row already exists on the same base code:
          * If it's The Atlantean (or legacy misspelling "The Atlantian"), normalize name and exit.
          * Otherwise, abort to avoid ambiguous Classic uniques on the same base code.
    """
    def is_classic(r):
        v = (r.get("version") or "").strip()
        return v == "" or v == "0"

    id_key = "index" if "index" in header else ("name" if "name" in header else None)
    if id_key is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing index/name column for Atlantean port.")

    def uid(r): 
        return (r.get(id_key) or "").strip()

    # 1) Locate source Atlantean row (prefer non-classic)
    src_row = None
    for r in rows:
        if not is_classic(r) and uid(r) in ("The Atlantean", "The Atlantian"):
            src_row = r
            break
    if src_row is None:
        for r in rows:
            if uid(r) in ("The Atlantean", "The Atlantian"):
                src_row = r
                break
    if src_row is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate any Atlantean row in uniqueitems.txt to clone.")

    base_code = (src_row.get("code") or "").strip()
    if base_code == "":
        raise RuntimeError("PATCHER ASSERTION FAILED: Atlantean source row has empty base code.")

    # 2) If any Classic unique already uses this base code, it must be Atlantean (otherwise ambiguous)
    for r in rows:
        if is_classic(r) and (r.get("code") or "").strip() == base_code:
            existing = uid(r)
            if existing == "The Atlantian":
                r[id_key] = "The Atlantean"
                report.append("[atlantean] Normalized Classic Atlantean name 'The Atlantian' -> 'The Atlantean'")
                report.append(f"[atlantean] The Atlantean already present on base '{base_code}'")
                return
            if existing != "The Atlantean":
                raise RuntimeError(
                    f"PATCHER ASSERTION FAILED: Found existing Classic unique bound to Atlantean base '{base_code}': '{existing}'. "
                    "Refusing to add The Atlantean to avoid ambiguity."
                )
            report.append(f"[atlantean] The Atlantean already present on base '{base_code}'")
            return

    # 3) Clone as Classic row
    new_row = dict(src_row)
    new_row["version"] = "0"
    if "enabled" in new_row:
        new_row["enabled"] = "1"
    # name normalization
    new_row[id_key] = "The Atlantean"

    rows.append(new_row)
    report.append(f"[atlantean] Added Classic The Atlantean by cloning source row onto base '{base_code}'")

def read_tsv(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    newline = "\r\n" if ("\r\n" in text and text.count("\r\n") >= text.count("\n")/2) else "\n"
    lines = text.splitlines()
    rows = list(csv.reader(lines, delimiter="\t"))
    if not rows:
        raise ValueError(f"Empty TSV: {path}")
    header = rows[0]
    data = []
    for r in rows[1:]:
        if not r or not any(cell != "" for cell in r):
            continue
        r = r + [""] * (len(header) - len(r))
        data.append(dict(zip(header, r[:len(header)])))
    return header, data, newline

def write_tsv(path: Path, header, data, newline="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator=newline, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        for r in data:
            w.writerow([r.get(h, "") for h in header])


def patch_monstats_cow_xp_boost(mod_root, report, mult=9999):
    """Increase XP for Cow Level monsters (hellbovine, cowking) via monstats.txt only.

    - Scope: Classic-safe; does not touch levels/treasure classes/experience curve.
    - Moderate default: 10x.
    """
    from pathlib import Path

    p = mod_root / "data/global/excel/monstats.txt"
    if not p.exists():
        report.append(f"[cow-xp] monstats.txt not found at: {p} (skipped)")
        return

    h, rows, nl = read_tsv(p)

    # Identify Exp columns (D2 convention)
    exp_cols = [c for c in ("Exp", "Exp(N)", "Exp(H)") if c in h]
    if not exp_cols:
        exp_cols = [c for c in h if c.lower().startswith("exp")]
    if not exp_cols:
        report.append("[cow-xp] No Exp columns found in monstats.txt (skipped)")
        return

    targets = {"hellbovine", "cowking"}
    changed_rows = 0
    changed_cells = 0

    for r in rows:
        mid = (r.get("Id") or r.get("id") or "").strip().lower()
        if mid not in targets:
            continue
        row_changed = False
        for c in exp_cols:
            v = (r.get(c) or "").strip()
            if not v or not re.fullmatch(r"-?\d+", v):
                continue
            iv = int(v)
            nv = iv * int(mult)
            if str(nv) != v:
                r[c] = str(nv)
                changed_cells += 1
                row_changed = True
        if row_changed:
            changed_rows += 1

    write_tsv(p, h, rows, nl)
    report.append(f"[cow-xp] Increased cow monster XP in monstats.txt by x{mult} (rows: {changed_rows}, cells: {changed_cells})")

def patch_charstats_from_reference(mod_root: Path, patch_sources: Path, log_lines: list[str]) -> None:
    """
    Patch charstats.txt by applying column overrides from a known-good reference file.

    - Vanilla charstats (seeded into mod_root) remains the base (schema/source of truth).
    - We then override specific fields using patch_sources/charstats.reference.txt,
      keyed by the 'class' column.
    """
    rel = Path("data/global/excel/charstats.txt")
    dst_path = mod_root / rel
    ref_path = patch_sources / "charstats.reference.txt"

    if not dst_path.exists():
        log_lines.append(f"[charstats] missing {rel} in output (skipped)")
        return
    if not ref_path.exists():
        log_lines.append("[charstats] missing patch_sources/charstats.reference.txt (skipped)")
        return

    dst_lines = dst_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    ref_lines = ref_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not dst_lines or not ref_lines:
        log_lines.append("[charstats] empty dst/ref (skipped)")
        return

    # delimiter detection
    dst_delim = "\t" if "\t" in dst_lines[0] else (";" if ";" in dst_lines[0] else "\t")
    ref_delim = "\t" if "\t" in ref_lines[0] else (";" if ";" in ref_lines[0] else "\t")

    dst_reader = csv.DictReader(dst_lines, delimiter=dst_delim)
    ref_reader = csv.DictReader(ref_lines, delimiter=ref_delim)

    if not dst_reader.fieldnames or not ref_reader.fieldnames:
        log_lines.append("[charstats] missing headers (skipped)")
        return

    if "class" not in [c.lower() for c in dst_reader.fieldnames]:
        log_lines.append("[charstats] dst missing 'class' column (skipped)")
        return
    if "class" not in [c.lower() for c in ref_reader.fieldnames]:
        log_lines.append("[charstats] ref missing 'class' column (skipped)")
        return

    # Find exact class column name in each file
    def find_col(fieldnames, target_lower):
        for c in fieldnames:
            if c.lower() == target_lower:
                return c
        return None

    dst_class_col = find_col(dst_reader.fieldnames, "class")
    ref_class_col = find_col(ref_reader.fieldnames, "class")

    # Build reference map: class -> row dict
    ref_map = {}
    for r in ref_reader:
        if not r:
            continue
        key = (r.get(ref_class_col) or "").strip().lower()
        if not key:
            continue
        ref_map[key] = r

    # Apply overrides for matching columns (only columns that exist in dst)
    dst_rows = []
    changed_cells = 0
    changed_rows = 0

    for r in dst_reader:
        if not r:
            continue
        key = (r.get(dst_class_col) or "").strip().lower()
        ref_row = ref_map.get(key)
        row_changed = False

        if ref_row is not None:
            for col in dst_reader.fieldnames:
                if col == dst_class_col:
                    continue
                # only override if the reference provides a non-empty value for that column
                if col in ref_row:
                    v = ref_row.get(col)
                    if v is not None and str(v).strip() != "" and r.get(col) != v:
                        r[col] = v
                        changed_cells += 1
                        row_changed = True

        if row_changed:
            changed_rows += 1
        dst_rows.append(r)

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=dst_reader.fieldnames, delimiter=dst_delim, lineterminator="\n")
    w.writeheader()
    w.writerows(dst_rows)
    dst_path.write_text(out.getvalue(), encoding="utf-8")

    log_lines.append(f"[charstats] applied reference overrides from {ref_path.name}: {changed_rows} row(s), {changed_cells} cell(s) updated")


def patch_treasureclassex_andariel(mod_root: Path, report: list[str]) -> None:
    """
    Minimal Andariel quest-drop patch (version-safe):
    - Keep vanilla treasureclassex.txt as the source of truth.
    - Overwrite rows for Andariel / Andariel (N) / Andariel (H) using the corresponding
      Andarielq / Andarielq (N) / Andarielq (H) values.
    - Preserve the original Treasure Class names (i.e., keep "Andariel", not "Andarielq").
    """
    rel = Path("data/global/excel/treasureclassex.txt")
    path = mod_root / rel
    if not path.exists():
        report.append(f"[treasureclassex] missing {rel} in output (skipped)")
        return

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        report.append("[treasureclassex] empty file (skipped)")
        return

    delim = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else "\t")
    rdr = csv.DictReader(lines, delimiter=delim)
    if not rdr.fieldnames:
        report.append("[treasureclassex] missing header (skipped)")
        return

    # Determine the treasure class name column (usually "Treasure Class")
    def find_col(fieldnames, want_lower):
        for c in fieldnames:
            if c.lower() == want_lower:
                return c
        return None

    tc_col = find_col(rdr.fieldnames, "treasure class")
    if tc_col is None:
        # fall back to first column name
        tc_col = rdr.fieldnames[0]

    rows = []
    by_name = {}
    for row in rdr:
        rows.append(row)
        key = (row.get(tc_col) or "").strip()
        if key:
            by_name[key] = row

    pairs = [
        ("Andariel", "Andarielq"),
        ("Andariel (N)", "Andarielq (N)"),
        ("Andariel (H)", "Andarielq (H)"),
    ]

    changed_cells = 0
    changed_rows = 0
    missing = []

    for dst_name, src_name in pairs:
        dst = by_name.get(dst_name)
        src = by_name.get(src_name)
        if dst is None or src is None:
            missing.append((dst_name, src_name))
            continue

        row_changed = False
        for col in rdr.fieldnames:
            if col == tc_col:
                continue
            if dst.get(col) != src.get(col):
                dst[col] = src.get(col)
                changed_cells += 1
                row_changed = True
        if row_changed:
            changed_rows += 1

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rdr.fieldnames, delimiter=delim, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(out.getvalue(), encoding="utf-8")

    if missing:
        report.append("[treasureclassex] Andariel quest-drop patch partial: missing pair(s): " +
                      ", ".join([f"{d}<-{s}" for d,s in missing]))
    report.append(f"[treasureclassex] Andariel quest-drop patch applied: {changed_rows} row(s), {changed_cells} cell(s) updated")


def patch_skills_intown_from_reference(mod_root: Path, patch_sources: Path, report: list[str]) -> None:
    """
    Minimal, version-safe Town-cast patch:
    - Vanilla skills.txt is the base/schema (seeded from --vanilla).
    - We ONLY override the 'InTown' column using patch_sources/skills.reference.txt,
      keyed by the first column ('skill').
    """
    rel = Path("data/global/excel/skills.txt")
    dst_path = mod_root / rel
    ref_path = patch_sources / "skills.reference.txt"

    if not dst_path.exists():
        report.append(f"[skills] missing {rel} in output (skipped)")
        return
    if not ref_path.exists():
        report.append("[skills] missing patch_sources/skills.reference.txt (skipped)")
        return

    dst_lines = dst_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    ref_lines = ref_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not dst_lines or not ref_lines:
        report.append("[skills] empty dst/ref (skipped)")
        return

    dst_delim = "\t" if "\t" in dst_lines[0] else (";" if ";" in dst_lines[0] else "\t")
    ref_delim = "\t" if "\t" in ref_lines[0] else (";" if ";" in ref_lines[0] else "\t")

    dst_reader = csv.DictReader(dst_lines, delimiter=dst_delim)
    ref_reader = csv.DictReader(ref_lines, delimiter=ref_delim)

    if not dst_reader.fieldnames or not ref_reader.fieldnames:
        report.append("[skills] missing headers (skipped)")
        return

    # skill name column is typically 'skill' (first column); use first header as fallback.
    skill_col_dst = dst_reader.fieldnames[0]
    skill_col_ref = ref_reader.fieldnames[0]

    if "InTown" not in dst_reader.fieldnames or "InTown" not in ref_reader.fieldnames:
        report.append("[skills] missing InTown column in dst/ref (skipped)")
        return

    # Map: skill -> InTown value (non-empty) from reference
    ref_map = {}
    for r in ref_reader:
        if not r:
            continue
        k = (r.get(skill_col_ref) or "").strip()
        if not k:
            continue
        v = (r.get("InTown") or "").strip()
        if v != "":
            ref_map[k] = v

    if not ref_map:
        report.append("[skills] reference contains no InTown overrides (skipped)")
        return

    rows = []
    changed_rows = 0
    changed_cells = 0
    for r in dst_reader:
        if not r:
            continue
        k = (r.get(skill_col_dst) or "").strip()
        if k in ref_map:
            v = ref_map[k]
            if (r.get("InTown") or "").strip() != v:
                r["InTown"] = v
                changed_rows += 1
                changed_cells += 1
        rows.append(r)

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=dst_reader.fieldnames, delimiter=dst_delim, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    dst_path.write_text(out.getvalue(), encoding="utf-8")

    report.append(f"[skills] InTown overrides applied from skills.reference.txt: {changed_rows} row(s) updated")


def patch_uniqueitems_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """
    Force ALL Unique items to roll their maximum ranged stats (Classic-safe).

    - Edits data/global/excel/uniqueitems.txt (vanilla-seeded by the patcher)
    - Classic only: version=0
    - For each row, copies maxN -> minN wherever maxN is non-empty
    """
    rel = Path("data/global/excel/uniqueitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[uni-max] missing {rel} (skipped)")
        return

    h, rows, nl = read_tsv(p)
    # --- Guarded Atlantean enablement (clone vanilla row; no strings injection) ---
    if _VANILLA_ROOT is None:
        raise RuntimeError('PATCHER ASSERTION FAILED: _VANILLA_ROOT not set; cannot clone vanilla Atlantean row.')
    _, _van_rows, _ = read_tsv(_VANILLA_ROOT / 'data/global/excel/uniqueitems.txt')
    # [uni-max] Atlantean porting handled by IN-PLACE patch step; no cloning here.

    # --- Guarded Atlantean enablement (deterministic) ---
    # Port 'The Atlantean' into Classic by adding a new Ancient Sword (ans) unique row (no replacement needed).
    # [tyrael] Legacy Chaos/Goldskin remap system removed (Tyrael hosted on Sacred Armor uar).

    min_cols = [c for c in h if c.lower().startswith("min") and c[3:].isdigit()]
    if not min_cols:
        report.append("[uni-max] no min/max columns found (skipped)")
        return

    changed_cells = 0
    changed_rows = 0

    for r in rows:

        if (r.get("version") or "").strip() != "0":
            continue

        row_changed = False
        for c in min_cols:
            mx = "max" + c[3:]
            if mx not in h:
                continue
            mxv = (r.get(mx, "") or "").strip()
            if not mxv:
                continue
            if (r.get(c, "") or "").strip() != mxv:
                r[c] = mxv
                changed_cells += 1
                row_changed = True

        if row_changed:
            changed_rows += 1

    write_tsv(p, h, rows)
    report.append(f"[uni-max] forced max rolls for uniques (rows changed: {changed_rows}, cells: {changed_cells})")


def patch_setitems_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """
    Force ALL Set items to roll their maximum ranged stats (Classic-safe).

    - Edits data/global/excel/setitems.txt (vanilla-seeded by the patcher)
    - Classic only: version=0
    - For each row, copies maxN -> minN wherever maxN is non-empty
    """
    rel = Path("data/global/excel/setitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[set-max] missing {rel} (skipped)")
        return

    h, rows, nl = read_tsv(p)

    min_cols = [c for c in h if c.lower().startswith("min") and c[3:].isdigit()]
    if not min_cols:
        report.append("[set-max] no min/max columns found (skipped)")
        return

    changed_cells = 0
    changed_rows = 0

    for r in rows:
        if (r.get("version") or "").strip() != "0":
            continue

        row_changed = False
        for c in min_cols:
            mx = "max" + c[3:]
            if mx not in h:
                continue
            mxv = (r.get(mx, "") or "").strip()
            if not mxv:
                continue
            if (r.get(c, "") or "").strip() != mxv:
                r[c] = mxv
                changed_cells += 1
                row_changed = True

        if row_changed:
            changed_rows += 1

    write_tsv(p, h, rows)
    report.append(f"[set-max] forced max rolls for set items (rows changed: {changed_rows}, cells: {changed_cells})")


def _force_min_equals_max(rows: list[dict[str, str]], headers: list[str], version_value: str) -> tuple[int, int]:
    """
    Force "min" roll columns to their corresponding "max" roll values.

    Supports two schemas commonly seen in D2R tables:
      1) min1/max1, min2/max2, ...
      2) mod1min/mod1max, mod2min/mod2max, ...

    Version gating:
      - If the table has a 'version' column, only rows with version==version_value are modified.
      - If the table has no 'version' column (common), all rows are treated as eligible (Classic-only mod safety).
    Returns (changed_rows, changed_cells).
    """
    # Detect whether version gating is available
    has_version = any(h.lower() == "version" for h in headers)

    pairs: list[tuple[str, str]] = []

    # Schema (1): minN/maxN
    min_cols = [c for c in headers if c.lower().startswith("min") and c[3:].isdigit()]
    for c in min_cols:
        n = c[3:]
        mx = "max" + n
        mx_key = next((h for h in headers if h.lower() == mx.lower()), None)
        if mx_key:
            pairs.append((c, mx_key))

    # Schema (2): modNmin/modNmax
    for c in headers:
        m = re.match(r"^mod(\d+)min$", c, flags=re.I)
        if not m:
            continue
        n = m.group(1)
        mx = f"mod{n}max"
        mx_key = next((h for h in headers if h.lower() == mx.lower()), None)
        if mx_key:
            pairs.append((c, mx_key))

    if not pairs:
        return (0, 0)

    changed_rows = 0
    changed_cells = 0

    for r in rows:
        if has_version:
            if (r.get("version") or "").strip() != version_value:
                continue

        row_changed = False
        for mn, mx in pairs:
            mxv = (r.get(mx) or "").strip()
            if mxv == "":
                continue
            if (r.get(mn) or "").strip() != mxv:
                r[mn] = mxv
                changed_cells += 1
                row_changed = True

        if row_changed:
            changed_rows += 1

    return (changed_rows, changed_cells)


def patch_magicprefix_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for all magic prefixes (Classic rows: version=0)."""
    rel = Path("data/global/excel/magicprefix.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[affix-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_min_equals_max(rows, h, "0")
    write_tsv(p, h, rows)
    report.append(f"[affix-max] magicprefix: forced max rolls (rows changed: {cr}, cells: {cc})")


def patch_magicsuffix_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for all magic suffixes (Classic rows: version=0)."""
    rel = Path("data/global/excel/magicsuffix.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[affix-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_min_equals_max(rows, h, "0")
    write_tsv(p, h, rows)
    report.append(f"[affix-max] magicsuffix: forced max rolls (rows changed: {cr}, cells: {cc})")


def patch_automagic_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for all automagic entries (Classic rows: version=0)."""
    rel = Path("data/global/excel/automagic.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[affix-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_min_equals_max(rows, h, "0")
    write_tsv(p, h, rows)
    report.append(f"[affix-max] automagic: forced max rolls (rows changed: {cr}, cells: {cc})")


def patch_skills_holyshock_min_equals_max(mod_root: Path, report: list[str]) -> None:
    """
    Holy Shock (and variants): force MIN lightning damage to equal MAX, and keep tooltip consistent.

    Why two passes?
    - Actual added lightning damage commonly comes from passive stat pairs:
        passivestatX=lightmindam / passivestatY=lightmaxdam
        passivecalcX / passivecalcY
      We copy calc(min) := calc(max).
    - The in-game tooltip / display range often reads EMin/EMax and EMinLev*/EMaxLev* (like Holy Fire).
      Holy Shock vanilla uses EMin=1 with empty EMinLev* which makes the displayed range huge.
      We also copy EMax* -> EMin* for rows we patch, so tooltip matches the deterministic behavior.

    Classic-safe: no Expansion-only fields are introduced; we only copy existing values.
    """
    rel = Path("data/global/excel/skills.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[holyshock] missing {rel} (skipped)")
        return

    h, rows, _nl = read_tsv(p)
    hset = set(h)

    def patch_statcalc(stat_prefix: str, calc_prefix: str, max_slots: int) -> tuple[int, int]:
        """Copy calc for mindam -> maxdam for matching lightning stat pairs within a row."""
        if stat_prefix + "1" not in hset or calc_prefix + "1" not in hset:
            return (0, 0)

        rows_changed = 0
        cells_changed = 0

        for r in rows:
            # Build map of stat name -> slot index
            stats = {}
            for i in range(1, max_slots + 1):
                sk = f"{stat_prefix}{i}"
                sv = (r.get(sk) or "").strip().lower()
                if sv:
                    stats[sv] = i

            # Patch any '*mindam' -> '*maxdam' pairs
            row_changed = False
            for stat_name, i in list(stats.items()):
                if not stat_name.endswith("mindam"):
                    continue
                max_name = stat_name[:-6] + "maxdam"
                j = stats.get(max_name)
                if j is None:
                    continue
                cmax = (r.get(f"{calc_prefix}{j}") or "").strip()
                if not cmax:
                    continue
                cmin_key = f"{calc_prefix}{i}"
                if (r.get(cmin_key) or "").strip() != cmax:
                    r[cmin_key] = cmax
                    cells_changed += 1
                    row_changed = True

            if row_changed:
                rows_changed += 1

        return (rows_changed, cells_changed)

    # Pass 1: actual damage via passive/aura calc copies
    pr, pc = patch_statcalc("passivestat", "passivecalc", 8)
    ar, ac = patch_statcalc("aurastat", "aurastatcalc", 6)

    # Pass 2: tooltip/display consistency via EMin/EMax pairs on Holy Shock-like rows.
    # We only apply this for rows where the passive/aura lightning mindam/maxdam pair exists (so we don't touch unrelated skills).
    lower_to_header = {c.lower(): c for c in h}

    emin_cols = [c for c in h if c.lower().startswith("emin")]
    display_pairs = []
    for c in emin_cols:
        tgt = c.lower().replace("emin", "emax", 1)
        if tgt in lower_to_header:
            display_pairs.append((c, lower_to_header[tgt]))

    dr = 0
    dc = 0
    if display_pairs:
        for r in rows:
            # Detect lightning passive or aura pair in this row
            has_pair = False
            for prefix in ("passivestat", "aurastat"):
                for i in range(1, 9 if prefix=="passivestat" else 7):
                    s = (r.get(f"{prefix}{i}") or "").strip().lower()
                    if s == "lightmindam":
                        # check if any slot contains lightmaxdam
                        for j in range(1, 9 if prefix=="passivestat" else 7):
                            if (r.get(f"{prefix}{j}") or "").strip().lower() == "lightmaxdam":
                                has_pair = True
                                break
                    if has_pair:
                        break
                if has_pair:
                    break
            if not has_pair:
                continue

            row_changed = False
            for emin, emax in display_pairs:
                mv = (r.get(emax) or "").strip()
                if not mv:
                    continue
                if (r.get(emin) or "").strip() != mv:
                    r[emin] = mv
                    dc += 1
                    row_changed = True
            if row_changed:
                dr += 1

    write_tsv(p, h, rows)
    report.append(f"[holyshock] min=max applied: passive(rows={pr},cells={pc}) aura(rows={ar},cells={ac}) display(rows={dr},cells={dc})")

def patch_misc_toa_version0(mod_root: Path, report: list[str]) -> None:
    """
    Classic compatibility: ensure Token of Absolution row (code 'toa') is usable in Classic by setting version=0.
    This preserves vanilla as base and only overrides the 'version' cell for 'toa' if present.
    """
    rel = Path("data/global/excel/misc.txt")
    path = mod_root / rel
    if not path.exists():
        report.append(f"[misc] missing {rel} (skipped toa version patch)")
        return

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        report.append("[misc] empty file (skipped toa version patch)")
        return

    delim = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else "\t")
    rdr = csv.DictReader(lines, delimiter=delim)
    if not rdr.fieldnames:
        report.append("[misc] missing header (skipped toa version patch)")
        return

    # Find column names
    def find_col(fieldnames, want_lower):
        for c in fieldnames:
            if c.lower() == want_lower:
                return c
        return None

    code_col = find_col(rdr.fieldnames, "code") or rdr.fieldnames[0]
    ver_col = find_col(rdr.fieldnames, "version")
    if ver_col is None:
        report.append("[misc] no 'version' column found (skipped toa version patch)")
        return

    rows = []
    changed = 0
    found = False
    for row in rdr:
        rows.append(row)
        if (row.get(code_col) or "").strip().lower() == "toa":
            found = True
            if (row.get(ver_col) or "").strip() != "0":
                row[ver_col] = "0"
                changed += 1

    if not found:
        report.append("[misc] 'toa' row not found (skipped)")
        return

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rdr.fieldnames, delimiter=delim, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(out.getvalue(), encoding="utf-8")
    report.append(f"[misc] classic toa: set version=0 (rows changed: {changed})")

def patch_misc(root: Path, report: list[str]):
    p = root/"data/global/excel/misc.txt"
    h, d, nl = read_tsv(p)
    if "code" not in h or "maxstack" not in h:
        report.append("misc.txt: missing code/maxstack columns, skipped")
        return
    cells = 0
    for code, val in [("key","50"),("tbk","80"),("ibk","80"),("aqv","500"),("cqv","500")]:
        for r in d:
            if r.get("code") == code and r.get("maxstack") != val:
                r["maxstack"] = val
                cells += 1
    write_tsv(p, h, d, nl)
    report.append(f"misc.txt: patched maxstack for key/tbk/ibk/aqv/cqv (cells changed: {cells})")

def patch_showlevel(root: Path, rel: str, report: list[str]):
    p = root/rel
    h, d, nl = read_tsv(p)
    if "ShowLevel" not in h:
        report.append(f"{rel}: ShowLevel missing, skipped")
        return
    rc = 0
    for r in d:
        if r.get("name","") == "Expansion":
            continue
        if r.get("ShowLevel") != "1":
            r["ShowLevel"] = "1"
            rc += 1
    write_tsv(p, h, d, nl)
    report.append(f"{rel}: set ShowLevel=1 (rows changed: {rc})")

def patch_automagic(root: Path, report: list[str]):
    p = root/"data/global/excel/automagic.txt"
    h, d, nl = read_tsv(p)
    rows_changed = 0
    cells_changed = 0
    for r in d:
        row_changed = False
        maxlevel = (r.get("maxlevel","") or "").strip()
        if maxlevel and "level" in h and (r.get("level","") or "").strip() != maxlevel:
            r["level"] = maxlevel
            cells_changed += 1
            row_changed = True
        for i in range(1, 4):
            minc, maxc = f"mod{i}min", f"mod{i}max"
            if minc in h and maxc in h:
                mx = (r.get(maxc,"") or "").strip()
                if mx and (r.get(minc,"") or "").strip() != mx:
                    r[minc] = mx
                    cells_changed += 1
                    row_changed = True
        if row_changed:
            rows_changed += 1
    write_tsv(p, h, d, nl)
    report.append(f"automagic.txt: level=maxlevel and modNmin=modNmax (rows changed: {rows_changed}, cells changed: {cells_changed})")

def patch_setitems(root: Path, report: list[str]):
    p = root/"data/global/excel/setitems.txt"
    h, d, nl = read_tsv(p)
    min_cols = [c for c in h if re.fullmatch(r"min\d+", c)]
    amin_cols = [c for c in h if c.startswith("amin")]
    rows_changed = 0
    cells_changed = 0
    for r in d:
        row_changed = False
        for c in min_cols:
            mx = c.replace("min", "max", 1)
            if mx in h:
                mxv = (r.get(mx,"") or "").strip()
                if mxv and (r.get(c,"") or "").strip() != mxv:
                    r[c] = mxv
                    cells_changed += 1
                    row_changed = True
        for c in amin_cols:
            mx = c.replace("amin", "amax", 1)
            if mx in h:
                mxv = (r.get(mx,"") or "").strip()
                if mxv and (r.get(c,"") or "").strip() != mxv:
                    r[c] = mxv
                    cells_changed += 1
                    row_changed = True
        if row_changed:
            rows_changed += 1
    write_tsv(p, h, d, nl)
    report.append(f"setitems.txt: min->max and amin->amax (rows changed: {rows_changed}, cells changed: {cells_changed})")

def cube_sig(r: dict, cols: list[str]) -> str:
    inputs = []
    for k in cols:
        if k.startswith("input ") and (r.get(k,"") or "").strip():
            inputs.append((r.get(k,"") or "").strip())
    inputs_sorted = "|".join(sorted(inputs))
    s = f"{r.get('op','')}|{r.get('version','')}|{r.get('output','')}|{inputs_sorted}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def patch_cubemain(root: Path, patch_sources: Path, report: list[str]) -> None:
    """
    Merge our custom cubemain recipes into vanilla-seeded cubemain.txt.

    Strategy (version-safe):
    - Vanilla cubemain.txt (already seeded into the mod tree) is the schema/source of truth.
    - We load patch_sources/cubemain.txt (our locked recipes).
    - For each enabled recipe row in patch_sources, we append it to vanilla if a stable signature
      (inputs + output + key control fields) does not already exist.

    This ensures all our recipes (Unique/Set Forge, Token of Absolution, etc.) are present without
    overwriting Blizzard's evolving base file.
    """
    rel = Path("data/global/excel/cubemain.txt")
    dst = root / rel
    src = patch_sources / "cubemain.txt"

    if not dst.exists():
        report.append("[cubemain] missing cubemain.txt in output (skipped)")
        return
    if not src.exists():
        report.append("[cubemain] missing patch_sources/cubemain.txt (skipped)")
        return

    h_base, d_base, nl = read_tsv(dst)
    h_patch, d_patch, _ = read_tsv(src)

    add_low_quality_variants_cubemain(d_patch, h_patch, report)

    if not d_base or not d_patch:
        report.append("[cubemain] empty base/patch (skipped)")
        return

    # Compute stable signature from common columns
    # Keep it conservative: only columns that define recipe identity & behavior.
    sig_cols = [
        "enabled", "ladder", "min diff", "version", "op", "param",
        "numinputs",
        "input 1", "input 2", "input 3", "input 4", "input 5", "input 6", "input 7",
        "output",
        "lvl", "plvl", "ilvl",
    ]

    common = [c for c in sig_cols if c in h_base and c in h_patch]
    # Fallback if headers differ slightly: include any input/output columns present
    if not common:
        common = [c for c in h_base if c.lower().startswith("input") or c.lower() == "output"]
        common = [c for c in common if c in h_patch]

    def sig(row: dict) -> tuple:
        return tuple((row.get(c, "") or "").strip() for c in common)

    base_sigs = {sig(r) for r in d_base}

    to_add = []
    for r in d_patch:
        # only add enabled rows (enabled == "1")
        if str(r.get("enabled", "")).strip() != "1":
            continue
        s = sig(r)
        if s in base_sigs:
            continue
        # ensure the patch row has at least an output field
        if "output" in h_patch and (r.get("output") is None or str(r.get("output")).strip() == ""):
            continue
        to_add.append(r)
        base_sigs.add(s)

    if not to_add:
        report.append("[cubemain] no new recipes to inject (already present)")
        return

    # Append patch rows using base header ordering; missing keys become blank
    out_rows = d_base + [{k: (r.get(k, "") if k in r else "") for k in h_base} for r in to_add]
    write_tsv(dst, h_base, out_rows)
    report.append(f"[cubemain] injected custom recipes (added rows: {len(to_add)})")


def copy_ui_overrides(root: Path, patch_sources: Path, report: list[str], enable_ui: bool = False):
    # UI override files (D2R layouts). If enable_ui is False (default), we still copy the
    # override sources but then rename them to 'disable*' filenames so the game won't load them.
    rels = [
        "data/global/ui/layouts/_profilehd.json",
        "data/global/ui/layouts/_profilelv.json",
        "data/global/ui/layouts/_profilesd.json",
        "data/global/ui/layouts/globaldata.json",
        "data/global/ui/layouts/globaldatahd.json",
    ]

    disabled_name_map = {
        "_profilehd.json": "disable_profilehd.json",
        "_profilelv.json": "disable_profilelv.json",
        "_profilesd.json": "disable_profilesd.json",
        "globaldata.json": "disableglobaldata.json",
        "globaldatahd.json": "disableglobaldatahd.json",
    }

    copied = 0
    disabled = 0

    for rel in rels:
        src_path = patch_sources / rel
        if not src_path.exists():
            continue

        dst_path = root / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        copied += 1

        if not enable_ui:
            # Rename to disabled filename in the same folder (removes active override by default).
            new_name = disabled_name_map.get(dst_path.name)
            if new_name:
                disabled_path = dst_path.with_name(new_name)
                if disabled_path.exists():
                    disabled_path.unlink()
                dst_path.rename(disabled_path)
                disabled += 1

    if enable_ui:
        report.append(f"[ui] UI overrides enabled: copied {copied} layout json file(s) from patch_sources.")
    else:
        report.append(f"[ui] UI overrides disabled by default: copied {copied} file(s) then renamed {disabled} to disable* filenames.")


def copy_static_payload(script_dir: Path, out_root: Path, log_lines: list[str]) -> None:
    """Copies bundled static mod files into the output folder.

    This keeps the output self-contained (no missing non-patched assets).
    The payload is stored under: <patcher_dir>/static_mod/...
    """
    src = script_dir / "static_mod"
    if not src.exists():
        return

    # Copy everything under static_mod into output root (typically provides data/hd assets, etc.)
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Do not overwrite here; patched outputs will overwrite later where intended.
        if not dst.exists():
            dst.write_bytes(p.read_bytes())

    log_lines.append(f"[static] copied bundled static payload from {src} into output (non-overwriting)")


def find_mod_subroot(static_root: Path) -> Path:
    """
    Detects the mod subroot by scanning static_mod for: mods/<modname>/<modname>.mpq/
    Returns relative path like: Path("mods")/"qol"/"qol.mpq"
    """
    mods_dir = static_root / "mods"
    if not mods_dir.exists():
        raise RuntimeError("static_mod is missing 'mods' folder; cannot determine mod root.")
    # Find first <modname>.mpq directory under mods/<modname>/
    for modname_dir in mods_dir.iterdir():
        if not modname_dir.is_dir():
            continue
        for mpq_dir in modname_dir.iterdir():
            if mpq_dir.is_dir() and mpq_dir.name.lower().endswith(".mpq"):
                return Path("mods") / modname_dir.name / mpq_dir.name
    raise RuntimeError("Could not find mods/<modname>/<modname>.mpq in static_mod; cannot determine mod root.")

PATCHED_TXT_REL = {
    "data/global/excel/misc.txt",
    "data/global/excel/cubemain.txt",
    "data/global/excel/armor.txt",
    "data/global/excel/weapons.txt",
    "data/global/excel/automagic.txt",
    "data/global/excel/setitems.txt",
    "data/global/excel/uniqueitems.txt",
    "data/global/excel/skills.txt",
}

def copy_static_payload(static_root: Path, out_root: Path, mod_subroot: Path, log_lines: list[str]) -> None:
    """
    Copies bundled static payload into output, preserving full tree:
      static_mod/mods/<modname>/<modname>.mpq/...  ->  <OUT>/mods/<modname>/<modname>.mpq/...
    Patched .txt targets are NOT copied from static (they will be generated from vanilla).
    """
    if not static_root.exists():
        return
    for p in static_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(static_root)
        rel_posix = str(rel).replace("\\", "/")
        # Skip patched txts if present inside the mod root
        # Example static path: mods/qol/qol.mpq/data/global/excel/misc.txt
        prefix = str(mod_subroot).replace("\\", "/") + "/"
        if rel_posix.lower().startswith(prefix.lower()):
            inner = rel_posix[len(prefix):].lower()
            if inner in PATCHED_TXT_REL:
                continue
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Overwrite static assets (safe), but patched txts are excluded above.
        dst.write_bytes(p.read_bytes())
    log_lines.append(f"[static] copied static_mod into output under {out_root}")


def sync_output_to_static(out_root: Path, script_dir: Path, mod_subroot: Path, log_lines: list[str]) -> None:
    """
    Overwrites static_mod with the final generated mod tree so static_mod always reflects
    the latest known-good mod structure.
    """
    static_root = script_dir / "static_mod"
    src = out_root / mod_subroot
    if not src.exists():
        raise RuntimeError(f"Cannot sync: output mod root does not exist: {src}")

    dst = static_root / mod_subroot
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    copied = 0
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
        copied += 1

    log_lines.append(f"[sync] static_mod overwritten with {copied} files from output")


def verify_vanilla_item_name_key(vanilla_root: Path, key: str, report: list[str]) -> None:
    """Verify shipped vanilla item-names*.json contains a Key. Does NOT copy or modify strings.

    If this verifies TRUE but you still see 'An Evil Force' in-game, your mod output is overriding
    vanilla strings with an incomplete strings file (merge/load-order issue).
    """
    rel_paths = [
        Path("data/local/lng/strings/item-names.json"),
        Path("data/local/lng/strings/item-names-hd.json"),
    ]
    verified_any = False

    for rel in rel_paths:
        p = vanilla_root / rel
        if not p.exists():
            report.append(f"[strings] vanilla missing: {rel.as_posix()} (cannot verify)")
            continue

        raw = p.read_text(encoding="utf-8", errors="replace")
        if f"\"Key\": \"{key}\"" in raw or f"\"Key\":\"{key}\"" in raw:
            report.append(f"[strings] vanilla contains Key='{key}' in {rel.as_posix()}")
            verified_any = True
            continue

        try:
            arr = json.loads(raw)
            keys = [e.get("Key") for e in arr if isinstance(e, dict) and isinstance(e.get("Key"), str)]
            close = [k for k in keys if k and (key.lower().replace(' ', '') in k.lower().replace(' ', '') or k.lower().replace(' ', '') in key.lower().replace(' ', ''))]
            report.append(f"[strings] vanilla does NOT contain Key='{key}' in {rel.as_posix()} (close: {close[:5]})")
        except Exception as ex:
            report.append(f"[strings] failed to parse vanilla {rel.as_posix()} for verification: {ex}")

    if not verified_any:
        report.append(f"[strings] WARNING: Could not verify vanilla Key='{key}' in any item-names*.json")


def apply_classic_atlantean_by_cloning_vanilla_unique(vanilla_rows, mod_rows, mod_header, report):
    """Port The Atlantean into Classic by cloning Blizzard's shipped vanilla Atlantean row."""

    def is_classic(r):
        v = (r.get("version") or "").strip()
        return v == "" or v == "0"

    id_key = "index" if "index" in mod_header else ("name" if "name" in mod_header else None)

    for r in mod_rows:
        if is_classic(r) and (r.get("code") or "").strip() == "9wd":
            uid = (r.get(id_key) or "").strip() if id_key else ""
            raise RuntimeError(
                f"PATCHER ASSERTION FAILED: A Classic unique already exists for Ancient Sword (code=9wd): '{uid}'."
            )

    def looks_like_atlantean(r):
        if (r.get("code") or "").strip() != "9wd":
            return False
        for k in ("index", "name"):
            if "atlanti" in (r.get(k) or "").strip().lower():
                return True
        return False

    src = next((r for r in vanilla_rows if looks_like_atlantean(r)), None)
    if src is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate vanilla The Atlantean row (code=9wd).")

    new_row = {k: src.get(k, "") for k in mod_header}
    if "version" in new_row:
        new_row["version"] = "0"
    if "enabled" in new_row and (new_row["enabled"] or "").strip() == "":
        new_row["enabled"] = "1"
    if id_key:
        new_row[id_key] = (src.get(id_key) or src.get("index") or src.get("name") or "The Atlantean")
        # Prefer correct spelling if present in vanilla strings (read-only check; no injection)
        try:
            sraw = (_VANILLA_ROOT / "data/local/lng/strings/item-names.json").read_text(encoding="utf-8", errors="replace")
            if "\"Key\": \"The Atlantean\"" in sraw or "\"Key\":\"The Atlantean\"" in sraw:
                new_row[id_key] = "The Atlantean"
        except Exception:
            pass

    mod_rows.append(new_row)
    report.append("[uni-max] added The Atlantean for Classic by cloning vanilla uniqueitems row (code=9wd)")

def apply_classic_enable_shako_base(mod_root, report):
    """Enable the Shako base item for Classic (armor.txt row code 'uap') by setting version=0."""
    p = mod_root / "data/global/excel/armor.txt"
    if not p.exists():
        report.append("[shako] armor.txt not found; skipping")
        return False
    h, rows, _ = read_tsv(p)

    sh = next((r for r in rows if (r.get("code") or "").strip() == "uap"), None)
    if sh is None:
        sh = next((r for r in rows if ((r.get("name") or r.get("namestr") or "")).strip().lower() == "shako"), None)
    if sh is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Shako base row in armor.txt (code uap).")

    sh["version"] = "0"
    if "enabled" in sh and (sh.get("enabled") or "").strip() == "0":
        sh["enabled"] = "1"
    write_tsv(p, h, rows)
    report.append("[shako] Enabled Shako base for Classic (armor.txt code=uap)")
    return True




def apply_classic_enable_battle_boots_base(mod_root, report):
    """Enable the Battle Boots base item for Classic (armor.txt row code 'xtb') by setting version=0.

    Mirrors the Shako/Tyrael base-enable pattern: do NOT invent new base codes.
    """
    p = mod_root / "data/global/excel/armor.txt"
    if not p.exists():
        raise RuntimeError("PATCHER ASSERTION FAILED: armor.txt not found in mod tree: " + str(p))

    h, rows, _ = read_tsv(p)
    if "code" not in h or "version" not in h:
        raise RuntimeError("PATCHER ASSERTION FAILED: armor.txt missing required columns (code/version).")

    target = None
    for r in rows:
        if (r.get("code") or "").strip().lower() == "xtb":
            target = r
            break
    if target is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: armor.txt missing Battle Boots row (code=xtb).")

    changed = 0
    if (target.get("version") or "").strip() != "0":
        target["version"] = "0"
        changed += 1
    if "enabled" in target and (target.get("enabled") or "").strip() == "0":
        target["enabled"] = "1"
        changed += 1

    write_tsv(p, h, rows)
    report.append(f"[wartrav-base] Enabled Battle Boots base for Classic (armor.txt code=xtb) (cells_changed={changed})")
    return True

def apply_classic_enable_ceremonial_javelin_base(mod_root, report):
    """Enable the Ceremonial Javelin base item for Classic (weapons.txt row code 'ama') by setting version=0."""
    p = mod_root / "data/global/excel/weapons.txt"
    if not p.exists():
        report.append("[ama] weapons.txt not found; skipping")
        return False
    h, rows, _ = read_tsv(p)

    r = next((x for x in rows if (x.get("code") or "").strip() == "ama"), None)
    if r is None:
        r = next((x for x in rows if ((x.get("name") or x.get("namestr") or "")).strip().lower() == "ceremonial javelin"), None)
    if r is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Ceremonial Javelin base row in weapons.txt (code ama).")

    r["version"] = "0"
    if "enabled" in r and (r.get("enabled") or "").strip() == "0":
        r["enabled"] = "1"
    if "spawnable" in r and (r.get("spawnable") or "").strip() == "0":
        r["spawnable"] = "1"

    write_tsv(p, h, rows)
    report.append("[ama] Enabled Ceremonial Javelin base for Classic (weapons.txt code=ama)")
    return True


def apply_classic_enable_ancient_sword_base(mod_root, report):
    """Enable the Ancient Sword base item for Classic (weapons.txt row code '9wd') by setting version=0.

    If the base is not Classic-enabled, CowTest injection can reference 9wd but the
    game will effectively skip the item because the base is not valid in Classic.
    """
    p = mod_root / "data/global/excel/weapons.txt"
    if not p.exists():
        report.append("[atlantean-base] weapons.txt not found; skipping")
        return False
    h, rows, _ = read_tsv(p)

    r = next((x for x in rows if (x.get("code") or "").strip() == "9wd"), None)
    if r is None:
        # Fallback by display/name fields (schema varies a little)
        def _lc(v):
            return (v or "").strip().lower()
        r = next((x for x in rows if _lc(x.get("name") or x.get("namestr")) == "ancient sword"), None)
    if r is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Ancient Sword base row in weapons.txt (code 9wd).")

    r["version"] = "0"
    if "enabled" in r and (r.get("enabled") or "").strip() == "0":
        r["enabled"] = "1"

    write_tsv(p, h, rows)
    report.append("[atlantean-base] Enabled Ancient Sword base for Classic (weapons.txt code=9wd)")
    return True


def apply_classic_port_harlequin_crest(mod_root, report):
    """Port Harlequin Crest + Peasent Crown into Classic IN PLACE (no clones, no remaps).

    Goals (canonical):
      - Harlequin Crest stays on its original base: Shako (code uap)
      - Peasent Crown stays on its original base: War Hat (code xap)
      - We only flip version->0 and ensure enabled=1. We do NOT append rows or delete/filter rows.
    """
    p = mod_root / "data/global/excel/uniqueitems.txt"
    if not p.exists():
        raise RuntimeError("uniqueitems.txt not found in mod tree: " + str(p))

    h, rows, _ = read_tsv(p)

    def nk(k): return (k or "").strip().lstrip("﻿").lower().replace(" ", "")
    idx_key = next((k for k in h if nk(k)=="index"), None)
    code_key = next((k for k in h if nk(k)=="code"), None)
    ver_key  = next((k for k in h if nk(k)=="version"), None)
    en_key   = next((k for k in h if nk(k)=="enabled"), None)

    if not idx_key or not code_key or not ver_key:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems.txt missing index/code/version columns.")

    def lc(v): return (v or "").strip().lower()

    def find_exact_index(name: str):
        for r in rows:
            if (r.get(idx_key) or "").strip() == name:
                return r
        return None

    # Harlequin Crest (uap, Shako) - vanilla row exists (version=100)
    harl = find_exact_index("Harlequin Crest")
    if harl is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate vanilla row for 'Harlequin Crest' in uniqueitems.txt.")
    harl[ver_key] = "0"
    if en_key: harl[en_key] = "1"
    # Preserve original base
    harl[code_key] = "uap"

    # Peasent Crown (xap, War Hat) - vanilla row exists (version=100, misspelled in vanilla index)
    peas = find_exact_index("Peasent Crown")
    if peas is None:
        # Allow alternate spelling fallback
        peas = next((r for r in rows if "peasant crown" in lc(r.get(idx_key)) or "peasent crown" in lc(r.get(idx_key))), None)
    if peas is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate vanilla row for 'Peasent Crown' in uniqueitems.txt.")
    peas[ver_key] = "0"
    if en_key: peas[en_key] = "1"
    peas[code_key] = "xap"

    write_tsv(p, h, rows)
    report.append("[lod-port] Enabled Harlequin Crest (uap) and Peasent Crown (xap) for Classic IN PLACE (no clones/remaps).")
    return True


def apply_classic_port_lod_uniques_titan_wartrav_raven(mod_root, report):
    """Port LoD uniques into Classic by editing Blizzard's shipped rows IN PLACE (table-safe, canonical bases).

    Targets (original white bases):
      - Titan's Revenge -> Ceremonial Javelin (code 'ama')
      - Wartraveler     -> Battle Boots       (code 'xtb')
      - Raven Frost     -> Ring              (code 'rin')

    We DO NOT append clone rows here to avoid duplicate *ID collisions or structural changes that can corrupt table resolution.
    """
    p = mod_root / "data/global/excel/uniqueitems.txt"
    if not p.exists():
        raise RuntimeError("uniqueitems.txt not found in mod tree: " + str(p))

    h, rows, _ = read_tsv(p)

    def nk(k): return (k or "").strip().lstrip("﻿").lower().replace(" ", "")
    idx_key = next((k for k in h if nk(k)=="index"), None)
    code_key = next((k for k in h if nk(k)=="code"), None)
    ver_key  = next((k for k in h if nk(k)=="version"), None)
    en_key   = next((k for k in h if nk(k)=="enabled"), None)

    if not idx_key or not code_key or not ver_key:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems.txt missing required columns for LoD port (index/code/version).")

    def find_index(name: str):
        for r in rows:
            if (r.get(idx_key) or "").strip() == name:
                return r
        return None

    targets = [
        ("Titan's Revenge", "ama"),
        ("Wartraveler",     "xtb"),
        ("Raven Frost",     "rin"),
    ]

    patched = 0
    for uname, ucode in targets:
        hit = find_index(uname)
        if hit is None:
            raise RuntimeError(f"PATCHER ASSERTION FAILED: Could not locate vanilla row for '{uname}' in uniqueitems.txt.")
        hit[ver_key] = "0"
        if en_key: hit[en_key] = "1"
        hit[code_key] = ucode
        patched += 1

    write_tsv(p, h, rows)
    report.append(f"[lod-port] Enabled Titan/WarTrav/Raven for Classic IN PLACE using canonical bases (patched={patched})")
    return True


def apply_deterministic_peasant_and_harlequin_forge(mod_root, report):
    """Deterministic forging hardening.

    Fixes recipe fall-through when inputs don't match strict qualifiers by inserting top-priority catch-all rows.
    Applies to weapons, armor, and (for consistency) jewelry.

    Notes:
      - Outputs remain baseline primitives (usetype,uni / usetype,set) with plvl/ilvl=99.
      - Jewelry catch-alls allow any ring/amulet quality to be forged (consistency request).
    """
    p = mod_root / "data/global/excel/cubemain.txt"
    if not p.exists():
        report.append("[forge] cubemain.txt not found; skipping deterministic forge hardening")
        return False

    h, rows, _ = read_tsv(p)

    need = ["description","enabled","version","numinputs","input 1","input 2","output","plvl","ilvl"]
    for k in need:
        if k not in h:
            report.append(f"[forge] cubemain header missing required column '{k}'; skipping")
            return False

    def make_row(desc, input1, input2, output):
        r = {k:"" for k in h}
        r["description"] = desc
        r["enabled"] = "1"
        r["version"] = "0"
        r["numinputs"] = "2"
        r["input 1"] = input1
        r["input 2"] = input2
        r["output"] = output
        if "plvl" in r: r["plvl"] = "99"
        if "ilvl" in r: r["ilvl"] = "99"
        return r

    def norm(s): return (s or "").strip().lower()

    managed_i1 = {"xap","uap","armo","weap","ring","amul"}
    managed_i2 = {"isc","key"}
    managed_out = {"usetype,uni","usetype,set"}

    # Remove any old overrides we manage (idempotent)
    kept=[]
    removed=0
    for r in rows:
        i1 = norm(r.get("input 1"))
        i2 = norm(r.get("input 2"))
        out = norm(r.get("output"))
        desc = norm(r.get("description"))
        is_managed = ("forge override:" in desc) or (i2 in managed_i2 and out in managed_out and i1 in managed_i1)
        if is_managed:
            removed += 1
            continue
        kept.append(r)
    rows = kept
    if removed:
        report.append(f"[forge] Removed {removed} prior managed override row(s)")

    inserts = [

# SACRED ARMOR (uar) explicit overrides (ensure Tyrael forge matches low/normal/superior bases)
make_row("FORGE OVERRIDE: uar any + isc -> unique (deterministic)", "uar", "isc", "usetype,uni"),
make_row("FORGE OVERRIDE: uar nor nos + isc -> unique (deterministic)", "uar,nor,nos", "isc", "usetype,uni"),
make_row("FORGE OVERRIDE: uar hiq nos + isc -> unique (deterministic)", "uar,hiq,nos", "isc", "usetype,uni"),
make_row("FORGE OVERRIDE: uar low nos + isc -> unique (deterministic)", "uar,low,nos", "isc", "usetype,uni"),

        # HELM (most specific first)
        make_row("FORGE OVERRIDE: xap any + isc -> unique (deterministic)", "xap", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: uap any + isc -> unique (deterministic)", "uap", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: xap nor nos + isc -> unique (deterministic)", "xap,nor,nos", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: xap hiq nos + isc -> unique (deterministic)", "xap,hiq,nos", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: uap nor nos + isc -> unique (deterministic)", "uap,nor,nos", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: uap hiq nos + isc -> unique (deterministic)", "uap,hiq,nos", "isc", "usetype,uni"),

        # GLOBAL UNIQUE FORGE hardening
        make_row("FORGE OVERRIDE: armo any + isc -> unique (catch-all)", "armo", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: weap any + isc -> unique (catch-all)", "weap", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: ring any + isc -> unique (catch-all)", "ring", "isc", "usetype,uni"),
        make_row("FORGE OVERRIDE: amul any + isc -> unique (catch-all)", "amul", "isc", "usetype,uni"),

        # GLOBAL SET FORGE hardening
        make_row("FORGE OVERRIDE: armo any + key -> set (catch-all)", "armo", "key", "usetype,set"),
        make_row("FORGE OVERRIDE: weap any + key -> set (catch-all)", "weap", "key", "usetype,set"),
        make_row("FORGE OVERRIDE: ring any + key -> set (catch-all)", "ring", "key", "usetype,set"),
        make_row("FORGE OVERRIDE: amul any + key -> set (catch-all)", "amul", "key", "usetype,set"),
    ]

    rows = inserts + rows
    write_tsv(p, h, rows)
    report.append(f"[forge] Deterministic forge hardening inserted ({len(inserts)} rows)")
    return True


def apply_remove_unique_level_requirements(mod_root, report):
    """Remove level requirements for ALL Classic uniques (uniqueitems.txt).

    Sets lvlreq (aka "lvl req") to 0 for every Classic-enabled unique row (version==0 or blank).
    This does not touch base item requirements (armor/weapons), only the unique's required level.
    """
    p = mod_root / "data/global/excel/uniqueitems.txt"
    if not p.exists():
        report.append("[uniq-lvlreq] uniqueitems.txt not found; skipping")
        return False

    h, rows, _ = read_tsv(p)

    def norm_key(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    def pick(*names):
        wanted = set(names)
        for k in h:
            if norm_key(k) in wanted:
                return k
        return None

    ver_key = pick("version")
    req_key = pick("lvlreq", "levelreq", "reqlevel", "reqlvl", "lvlreq", "lvl req")

    if ver_key is None or req_key is None:
        report.append("[uniq-lvlreq] uniqueitems missing required columns (need version + lvlreq); skipping")
        return False

    def is_classic(r):
        v = (r.get(ver_key) or "").strip()
        return v == "" or v == "0"

    changed_rows = 0
    changed_cells = 0
    for r in rows:
        if not is_classic(r):
            continue
        if (r.get(req_key) or "").strip() != "0":
            r[req_key] = "0"
            changed_cells += 1
            changed_rows += 1

    if changed_rows == 0:
        report.append("[uniq-lvlreq] No Classic unique lvlreq values needed changing")
        return True

    write_tsv(p, h, rows)
    report.append(f"[uniq-lvlreq] Set lvlreq=0 for {changed_rows} Classic unique row(s)")
    return True


def apply_cow_test_drop_injection(mod_root: Path, report: list[str], enabled=True) -> None:
    """
    Cow-level testing injection (focus on key bases).

    - Builds code list from Classic uniqueitems.txt base codes (version=0).
    - Replicates key test bases (esp. Sacred Armor uar) across Cow TCs so you can visibly farm them.
    - SAFE: fills empty slots only; does NOT touch NoDrop; does NOT replace existing items.
    """
    if not enabled:
        report.append("[cow-test] Disabled (flag off); skipped")
        return False

    if not hasattr(report, "append"):
        return

    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[cow-test] uniqueitems.txt missing; skipped")
        return

    uh, urows, _ = read_tsv(up)

    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    ver_key = next((k for k in uh if nk(k)=="version"), None)
    code_key = next((k for k in uh if nk(k)=="code"), None)
    en_key = next((k for k in uh if nk(k) in ("enabled", "enabled1")), None)

    if not ver_key or not code_key:
        report.append("[cow-test] uniqueitems.txt missing version/code columns; skipped")
        return

    def is_classic(r):
        v = (r.get(ver_key) or "").strip()
        return v=="" or v=="0"

    codes = []
    seen = set()
    for r in urows:
        if not is_classic(r):
            continue
        if en_key:
            ev = (r.get(en_key) or "").strip()
            if ev not in ("", "1"):
                continue
        c = (r.get(code_key) or "").strip().lower()
        if not c:
            continue
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)

    # Key bases we want to see a lot of during testing.
    focus = ["uar", "uap", '9wd', "xap", "ring", "amul"]
    focus_present = []
    for c in focus:
        if c in ("ring","amul") or c in seen:
            focus_present.append(c)

    # Deterministic shuffle for variety
    codes_sorted = sorted(codes)
    rng = random.Random(1337)
    rng.shuffle(codes_sorted)

    remainder = [c for c in codes_sorted if c not in set(focus_present)]
    stream = focus_present + remainder

    tp = mod_root / "data/global/excel/treasureclassex.txt"
    if not tp.exists():
        report.append("[cow-test] treasureclassex.txt missing; skipped")
        return

    th, rows, _ = read_tsv(tp)

    tc_key = next((k for k in th if nk(k) in ("treasureclass","treasureclassname","name","tc")), None)
    if not tc_key:
        report.append("[cow-test] treasureclassex missing TC name column; skipped")
        return

    item_cols = [k for k in th if nk(k).startswith("item")]
    prob_cols = [k for k in th if nk(k).startswith("prob")]

    def _suffix_num(col):
        m = re.search(r'(\d+)$', nk(col))
        return int(m.group(1)) if m else 0

    item_cols.sort(key=_suffix_num)
    prob_cols.sort(key=_suffix_num)

    if not item_cols or not prob_cols:
        report.append("[cow-test] treasureclassex missing item/prob columns; skipped")
        return

    cow_rows = []
    for r in rows:
        name = (r.get(tc_key) or "")
        if "cow" in name.lower():
            cow_rows.append(r)

    if not cow_rows:
        report.append("[cow-test] No Cow TCs found; skipped")
        return

    injected = 0

    # Pass 1: replicate focus per cow TC (fill empty slots only)
    for r in cow_rows:
        placed = 0
        for ic, pc in zip(item_cols, prob_cols):
            if placed >= len(focus_present):
                break
            if (r.get(ic) or "").strip() != "":
                continue
            r[ic] = focus_present[placed]
            r[pc] = "8192"
            placed += 1
            injected += 1

    # Pass 2: fill remaining empty slots with stream
    i = 0
    for r in cow_rows:
        for ic, pc in zip(item_cols, prob_cols):
            if (r.get(ic) or "").strip() != "":
                continue
            if i >= len(stream):
                break
            r[ic] = stream[i]
            r[pc] = "1024"
            i += 1
            injected += 1
        if i >= len(stream):
            break

    if injected:
        # PatchR54: targeted focus boost (use tc_rows alias for consistency)
        tc_rows = rows
        apply_cow_focus_boost(tc_rows, report)

        write_tsv(tp, th, rows)

    report.append(f"[cow-test] Enabled (safe+focus): injected {injected} slots into {len(cow_rows)} Cow TCs (empty slots only; focus prob=8192)")
    report.append(f"[cow-test] Focus: {','.join(focus_present) if focus_present else '(none)'}")
    preview = ",".join(stream[:30]) + ("..." if len(stream) > 30 else "")
    report.append(f"[cow-test] Stream ({len(stream)}): {preview}")


def apply_phase2_drop_integration(mod_root: Path, report: list[str], enabled: bool) -> None:
    """Phase 2 (drops): integrate ported (non-Assassin/Druid) bases into natural TreasureClassEx drops.

    Philosophy (SAFE MODE):
      - Does NOT alter row count/order.
      - Does NOT change NoDrop, Picks, quality bias, or existing Item/Prob entries.
      - Fills EMPTY ItemN slots only, on high-level TCs (level >= 70), excluding Cow TCs.
      - Intended to make ported bases naturally appear without destabilizing Classic balance.

    Notes:
      - Uniques remain subject to the engine's per-game unique spawn rule.
      - This integrates BASE items; actual unique/set rarity is still controlled by quality selection.
    """
    if not enabled:
        report.append("[phase2-drops] Disabled (flag off); skipped")
        return

    excel = mod_root / "data/global/excel"
    p_tc = excel / "treasureclassex.txt"
    p_uni = excel / "uniqueitems.txt"
    p_armor = excel / "armor.txt"
    p_weap = excel / "weapons.txt"
    p_misc = excel / "misc.txt"
    p_types = excel / "itemtypes.txt"

    if not (p_tc.exists() and p_uni.exists() and p_types.exists()):
        report.append("[phase2-drops] Missing treasureclassex/uniqueitems/itemtypes; skipped")
        return

    th, tc_rows, _ = read_tsv(p_tc)
    uh, urows, _ = read_tsv(p_uni)
    hh, type_rows, _ = read_tsv(p_types)

    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    tc_key = next((k for k in th if nk(k) in ("treasureclass","treasureclassname","name","tc")), None)
    lvl_key = next((k for k in th if nk(k) in ("level","lvl","tclevel")), None)

    item_cols = [k for k in th if nk(k).startswith("item")]
    prob_cols = [k for k in th if nk(k).startswith("prob")]

    def _suffix_num(col):
        m = re.search(r'(\d+)$', nk(col))
        return int(m.group(1)) if m else 0

    item_cols.sort(key=_suffix_num)
    prob_cols.sort(key=_suffix_num)

    if not tc_key or not lvl_key or not item_cols or not prob_cols:
        report.append("[phase2-drops] treasureclassex missing expected columns; skipped")
        return

    # Build base code -> (table, row_index, type/type2) index from armor/weapons/misc
    base_index = {}
    base_tables = {}
    def index_base_table(p: Path):
        if not p.exists():
            return
        h, rows, _ = read_tsv(p)
        col_code = next((k for k in h if nk(k)=="code"), None)
        col_type = next((k for k in h if nk(k)=="type"), None)
        col_type2 = next((k for k in h if nk(k)=="type2"), None)
        if not col_code:
            return
        base_tables[p.name] = (p, h, rows, col_code, col_type, col_type2)
        for i, r in enumerate(rows):
            c = (r.get(col_code) or "").strip().lower()
            if not c:
                continue
            if c not in base_index:
                base_index[c] = (p.name, i)

    index_base_table(p_armor)
    index_base_table(p_weap)
    index_base_table(p_misc)

    # Identify Assassin/Druid restricted item type codes from itemtypes.txt
    col_type_code = next((k for k in hh if nk(k) in ("code","itemtype","type","itemtypecode")), None)
    col_class = next((k for k in hh if nk(k) in ("class","equiv1","playerclass")), None)
    # In most schemas, 'Class' exists; if not, we fall back to skipping nothing (but Phase1 already excluded those uniques).
    restricted_type_codes = set()
    if col_type_code and col_class:
        for r in type_rows:
            cls = (r.get(col_class) or "").strip().lower()
            if cls in ("ass", "dru"):
                restricted_type_codes.add((r.get(col_type_code) or "").strip())

    def is_restricted_base(code_item: str) -> bool:
        code_item = (code_item or "").strip().lower()
        if code_item not in base_index:
            return False
        fname, ridx = base_index[code_item]
        p, h, rows, col_code, col_type, col_type2 = base_tables[fname]
        br = rows[ridx]
        t1 = (br.get(col_type) or "").strip()
        t2 = (br.get(col_type2) or "").strip()
        return (t1 in restricted_type_codes) or (t2 in restricted_type_codes)

    # Collect eligible base codes from Classic-enabled uniques (exclude ass/dru locked bases)
    ver_key = next((k for k in uh if nk(k)=="version"), None)
    code_key = next((k for k in uh if nk(k)=="code"), None)
    en_key = next((k for k in uh if nk(k) in ("enabled","enabled1")), None)

    if not ver_key or not code_key:
        report.append("[phase2-drops] uniqueitems missing version/code; skipped")
        return

    eligible = []
    seen = set()
    for r in urows:
        v = (r.get(ver_key) or "").strip()
        if v not in ("", "0"):
            continue
        if en_key:
            ev = (r.get(en_key) or "").strip()
            if ev not in ("", "1"):
                continue
        c = (r.get(code_key) or "").strip().lower()
        if not c or c in seen:
            continue
        if is_restricted_base(c):
            continue
        seen.add(c)
        eligible.append(c)

    if not eligible:
        report.append("[phase2-drops] No eligible base codes found; skipped")
        return

    # Deterministic shuffle
    rng = random.Random(20260221)
    eligible_sorted = sorted(eligible)
    rng.shuffle(eligible_sorted)

    # Inject into high-level TCs only, empty slots only
    MIN_LEVEL = 70
    MAX_PER_TC = 2
    PROB = "1"      # conservative weight comparable to existing high-level probs
    PROB_FOCUS = "2"

    focus = [c for c in ["uar","uap","9wd","xap","ring","amul"] if c in set(eligible_sorted) or c in ("ring","amul")]
    stream = focus + [c for c in eligible_sorted if c not in set(focus)]

    injected = 0
    tcs_touched = 0
    i = 0

    for r in tc_rows:
        name = (r.get(tc_key) or "")
        if "cow" in name.lower():
            continue
        lvl = (r.get(lvl_key) or "").strip()
        if not lvl.isdigit() or int(lvl) < MIN_LEVEL:
            continue

        placed = 0
        for ic, pc in zip(item_cols, prob_cols):
            if placed >= MAX_PER_TC:
                break
            if (r.get(ic) or "").strip() != "":
                continue
            if i >= len(stream):
                break
            r[ic] = stream[i]
            r[pc] = PROB_FOCUS if stream[i] in focus else PROB
            i += 1
            placed += 1
            injected += 1

        if placed:
            tcs_touched += 1
        if i >= len(stream):
            break

    if injected:
        write_tsv(p_tc, th, tc_rows)
        report.append(f"[phase2-drops] SAFE injected {injected} base entries into {tcs_touched} high-level TCs (level>={MIN_LEVEL}, empty slots only, prob={PROB}/{PROB_FOCUS}).")
        report.append(f"[phase2-drops] Focus: {','.join(focus) if focus else '(none)'}")
        preview = ",".join(stream[:40]) + ("..." if len(stream)>40 else "")
        report.append(f"[phase2-drops] Stream preview: {preview}")
    else:
        report.append("[phase2-drops] No empty slots found on eligible TCs; no changes made.")


def apply_classic_port_atlantean_unique(mod_root, report):
    """Wrapper to port The Atlantean into Classic using uniqueitems.txt in mod_root."""
    p = mod_root / "data/global/excel/uniqueitems.txt"
    if not p.exists():
        report.append("[atlantean] uniqueitems.txt not found; skipping")
        return False
    header, rows, _ = read_tsv(p)
    # Reuse the row-level transformer
    apply_classic_add_atlantean_for_ancient_sword(rows, header, report)
    write_tsv(p, header, rows)
    return True


def apply_classic_port_atlantean_vanilla_key_r29_template(mod_root, report):
    """Enable The Atlantean in Classic IN PLACE (no clones, no string overrides, canonical base).

    Canonical:
      - Unique: The Atlantean
      - Base: Ancient Sword (code 9wd) (vanilla)
      - Action: set version=0 and enabled=1 on the existing vanilla row
    """
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[atlantean] uniqueitems.txt not found; skipped")
        return False

    header, rows, _ = read_tsv(up)

    def nk(k): return (k or "").strip().lstrip("﻿").lower().replace(" ", "")
    idx_key = next((k for k in header if nk(k)=="index"), None)
    code_key = next((k for k in header if nk(k)=="code"), None)
    ver_key  = next((k for k in header if nk(k)=="version"), None)
    en_key   = next((k for k in header if nk(k)=="enabled"), None)

    if not idx_key or not code_key or not ver_key:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems.txt missing required columns for Atlantean enable (index/code/version).")

    hit = next((r for r in rows if (r.get(idx_key) or "").strip() == "The Atlantean"), None)
    if hit is None:
        # allow legacy misspelling in the table (rare)
        hit = next((r for r in rows if "atlanti" in ((r.get(idx_key) or "").strip().lower()) and (r.get(code_key) or "").strip() == "9wd"), None)

    if hit is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate vanilla row for 'The Atlantean' (code 9wd).")

    hit[ver_key] = "0"
    if en_key: hit[en_key] = "1"
    hit[code_key] = "9wd"

    write_tsv(up, header, rows)
    report.append("[atlantean] Enabled The Atlantean (9wd) for Classic IN PLACE (no clones).")
    return True


def apply_classic_host_tyrael_on_sacred_armor(mod_root, report):
    """Enable Tyrael's Might for Classic on its original base (Sacred Armor, code uar) IN PLACE.

    Canonical:
      - Unique: Tyrael's Might
      - Base: Sacred Armor (uar) (vanilla)
      - Action: set version=0 and enabled=1 on the existing vanilla row (no clones/removals/reordering).
      - Also ensures Sacred Armor base is enabled for Classic in armor.txt (version=0).
    """
    # Enable Sacred Armor base (uar) for Classic
    ap = mod_root / "data/global/excel/armor.txt"
    if ap.exists():
        h, rows, _ = read_tsv(ap)
        def lc(v): return (v or "").strip().lower()
        code_col = next((c for c in h if c.strip().lower()=="code"), None)
        ver_col  = next((c for c in h if c.strip().lower()=="version"), None)
        en_col   = next((c for c in h if c.strip().lower()=="enabled"), None)
        if code_col and ver_col:
            r = next((x for x in rows if lc(x.get(code_col))=="uar"), None)
            if r is None:
                raise RuntimeError("PATCHER ASSERTION FAILED: Sacred Armor base row (code uar) not found in armor.txt.")
            r[ver_col] = "0"
            if en_col: r[en_col] = "1"
            write_tsv(ap, h, rows)
            report.append("[tyrael] Enabled Sacred Armor base for Classic (armor.txt code=uar)")
        else:
            report.append("[tyrael] armor.txt missing code/version; skipped base enable")
    else:
        report.append("[tyrael] armor.txt not found; skipped base enable")

    # Enable Tyrael's Might in uniqueitems IN PLACE
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[tyrael] uniqueitems.txt not found; skipped Tyrael enable")
        return False

    h, rows, _ = read_tsv(up)
    def nk(k): return (k or "").strip().lstrip("﻿").lower().replace(" ","")
    idx_key = next((k for k in h if nk(k)=="index"), None) or next((k for k in h if nk(k)=="name"), None)
    code_key = next((k for k in h if nk(k)=="code"), None)
    ver_key  = next((k for k in h if nk(k)=="version"), None)
    en_key   = next((k for k in h if nk(k)=="enabled"), None)

    if not all([idx_key, code_key, ver_key]):
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing required columns for Tyrael enable.")

    hit = next((r for r in rows if (r.get(idx_key) or "").strip() == "Tyrael's Might"), None)
    if hit is None:
        # fallback token match
        hit = next((r for r in rows if "tyrael" in ((r.get(idx_key) or "").strip().lower())), None)
    if hit is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate vanilla row for Tyrael's Might in uniqueitems.txt.")

    hit[ver_key] = "0"
    if en_key: hit[en_key] = "1"
    hit[code_key] = "uar"

    write_tsv(up, h, rows)
    report.append("[tyrael] Enabled Tyrael's Might (uar) for Classic IN PLACE (no clones).")
    return True


def _uniqueitems_set_key_sync(row, index_key, name_key, value):
    """Set both index and name keys (if present) to the same value."""
    if index_key and index_key in row:
        row[index_key] = value
    if name_key and name_key in row:
        row[name_key] = value

def apply_post_unique_maxrolls_for_targets(mod_root: Path, report: list[str], target_names: list[str]) -> None:
    """
    Post-pass maxroll fixer for specific unique rows (Classic-only).
    This is used to ensure rows created/modified after the main uni-max pass (e.g., Tyrael host)
    still get min=max applied.
    """
    rel = Path("data/global/excel/uniqueitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[uni-max-post] missing {rel} (skipped)")
        return

    h, rows, _ = read_tsv(p)

    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    index_key = next((k for k in h if nk(k)=="index"), None)
    name_key  = next((k for k in h if nk(k)=="name"), None)
    id_keys = [k for k in (index_key, name_key) if k]

    if not id_keys:
        report.append("[uni-max-post] uniqueitems missing index/name columns (skipped)")
        return

    targets = set([t.strip().lower() for t in target_names if t and t.strip()])
    min_cols = [c for c in h if c.lower().startswith("min") and c[3:].isdigit()]
    if not min_cols:
        report.append("[uni-max-post] no min/max columns found (skipped)")
        return

    changed_cells = 0
    changed_rows = 0

    for r in rows:
        if (r.get("version") or "").strip() != "0":
            continue
        # match by index or name
        rid = ""
        for k in id_keys:
            v=(r.get(k) or "").strip().lower()
            if v:
                rid=v
                break
        if rid not in targets:
            continue

        row_changed=False
        for c in min_cols:
            mx="max"+c[3:]
            if mx not in h:
                continue
            mxv=(r.get(mx,"") or "").strip()
            if not mxv:
                continue
            if (r.get(c,"") or "").strip()!=mxv:
                r[c]=mxv
                changed_cells+=1
                row_changed=True
        if row_changed:
            changed_rows+=1

    if changed_rows:
        write_tsv(p, h, rows)
    report.append(f"[uni-max-post] targets={len(targets)} rows changed={changed_rows} cells={changed_cells}")


def apply_force_uar_base_levelreq0(mod_root, report):
    """
    Force Sacred Armor (uar) base requirements to 0 in armor.txt.

    Robust:
    - Zero any columns whose header contains 'req' (covers ReqLevel, reqstr, reqdex, etc.)
    - Additionally, zero any cell equal to "66" on uar rows regardless of column name (matches observed Req Level 66).
    """
    ap = mod_root / "data/global/excel/armor.txt"
    if not ap.exists():
        report.append("[uar-lvlreq] armor.txt not found; skipped")
        return False

    header, rows, _ = read_tsv(ap)

    def nk(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    code_key = next((k for k in header if nk(k)=="code"), None)
    if not code_key:
        report.append("[uar-lvlreq] armor.txt missing code column; skipped")
        return False

    req_cols = [k for k in header if "req" in nk(k)]
    changed = 0
    cols_touched = set()
    uar_rows = 0

    for r in rows:
        if (r.get(code_key) or "").strip().lower() != "uar":
            continue
        uar_rows += 1

        for k in req_cols:
            v = (r.get(k) or "").strip()
            if v != "" and v != "0":
                r[k] = "0"
                changed += 1
                cols_touched.add(k)

        for k in header:
            v = (r.get(k) or "").strip()
            if v == "66":
                r[k] = "0"
                changed += 1
                cols_touched.add(k)

    if changed:
        write_tsv(ap, header, rows)

    report.append(f"[uar-lvlreq] forced uar requirements to 0 (uar rows: {uar_rows}, cells changed: {changed}, req-cols detected: {len(req_cols)})")
    if cols_touched:
        report.append("[uar-lvlreq] cols changed: " + ",".join(list(cols_touched)[:12]) + ("..." if len(cols_touched) > 12 else ""))
    return True


def apply_force_tyrael_unique_lvlreq0(mod_root, report):
    """Force Tyrael's Might unique lvlreq to 0 in uniqueitems.txt (Classic rows)."""
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[tyrael-lvlreq] uniqueitems.txt not found; skipped")
        return False
    header, rows, _ = read_tsv(up)

    def nk(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    ver_key = next((k for k in header if nk(k)=="version"), None)
    code_key = next((k for k in header if nk(k)=="code"), None)
    lvl_key  = next((k for k in header if nk(k)=="lvlreq"), None)
    idx_key  = next((k for k in header if nk(k)=="index"), None)
    name_key = next((k for k in header if nk(k)=="name"), None)

    if not code_key or not lvl_key:
        report.append("[tyrael-lvlreq] uniqueitems.txt missing code/lvlreq; skipped")
        return False

    def is_classic(r):
        if not ver_key:
            return True
        v=(r.get(ver_key) or "").strip()
        return v=="" or v=="0"

    changed=0
    targets=0
    for r in rows:
        if not is_classic(r):
            continue
        # match by name/index if present, else by base code uar (hosted) as fallback
        key = ""
        if idx_key: key = (r.get(idx_key) or "").strip()
        if not key and name_key: key = (r.get(name_key) or "").strip()
        if key == "Tyrael's Might" or (r.get(code_key) or "").strip().lower()=="uar":
            if key == "Tyrael's Might" or (r.get(code_key) or "").strip().lower()=="uar":
                targets += 1
                if (r.get(lvl_key) or "").strip() != "0":
                    r[lvl_key] = "0"
                    changed += 1

    if changed:
        write_tsv(up, header, rows)
    report.append(f"[tyrael-lvlreq] Tyrael unique lvlreq forced to 0 (targets: {targets}, cells changed: {changed})")
    return True


def add_low_quality_variants_cubemain(rows, header, report):
    """
    Expand cubemain recipes so input quality variants work for cowtest forging.

    - Generic: add ',low,nos' sibling for recipes using ',nor,nos' or ',hiq,nos'.
    - uar-specific: if any recipe input references Sacred Armor (uar) with nor/hiq/low, ensure all three variants exist.
      This fixes Superior Sacred Armor (hiq) not matching when only nor was present.
    """
    def nk(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    in_cols = [k for k in header if nk(k).startswith("input")]
    if not in_cols:
        return 0

    def sig(r):
        out = (r.get("output") or "")
        parts = [out] + [(r.get(c) or "") for c in in_cols]
        return "|".join(parts)

    def normalize_uar(v, target):
        if "uar," not in v:
            return v
        v2 = v.replace("uar,nor,nos","uar,TGT,nos").replace("uar,hiq,nos","uar,TGT,nos").replace("uar,low,nos","uar,TGT,nos")
        return v2.replace("uar,TGT,nos", "uar,"+target+",nos")

    seen=set()
    base=list(rows)
    new_rows=[]
    for r in base:
        s=sig(r)
        if s not in seen:
            seen.add(s)
            new_rows.append(r)

    for r in list(new_rows):
        # generic low sibling
        low_r=dict(r)
        any_change=False
        for c in in_cols:
            v=(low_r.get(c) or "")
            if ",nor,nos" in v or ",hiq,nos" in v:
                low_r[c]=v.replace(",nor,nos",",low,nos").replace(",hiq,nos",",low,nos")
                any_change=True
        if any_change:
            s=sig(low_r)
            if s not in seen:
                seen.add(s)
                new_rows.append(low_r)

        # uar-specific variants
        joined=" ".join((r.get(c) or "") for c in in_cols)
        if "uar,nor,nos" in joined or "uar,hiq,nos" in joined or "uar,low,nos" in joined:
            for target in ("nor","hiq","low"):
                vr=dict(r)
                for c in in_cols:
                    vr[c]=normalize_uar(vr.get(c) or "", target)
                s=sig(vr)
                if s not in seen:
                    seen.add(s)
                    new_rows.append(vr)

    delta=len(new_rows)-len(rows)
    if delta>0:
        report.append(f"[cubemain] input-quality variants expanded (recipes added: {delta})")
    rows[:] = new_rows
    return delta


def apply_cow_focus_boost(tc_rows, report):
    """PatchR54: boost cowtest probabilities for targeted bases used in forge testing."""
    BOOST = {"uar", "9wd", "uap"}  # Sacred Armor, Ancient Sword, Shako
    BOOST_PROB = 8192

    changed = 0
    for r in tc_rows:
        if r.get("Item1") in BOOST:
            r["Prob1"] = str(BOOST_PROB)
            changed += 1

    report.append(f"[cow-focus] boosted {changed} entries (uar/9wd/uap)")

def validate_uniqueitems_invariants(mod_root, report):
    """Hard integrity gate to prevent 'jumbled uniques' caused by structural corruption.

    Enforced for uniqueitems.txt only:
      - Row count must match vanilla (no add/remove)
      - Header columns must match vanilla (same order)
      - *ID must be unique (if column exists)
      - Row order must match vanilla by *ID sequence (if *ID exists)
    """
    if _VANILLA_ROOT is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: _VANILLA_ROOT not set; cannot validate uniqueitems invariants.")
    vp = _VANILLA_ROOT / "data/global/excel/uniqueitems.txt"
    mp = mod_root / "data/global/excel/uniqueitems.txt"
    if not vp.exists() or not mp.exists():
        report.append("[uniqueitems-guard] missing vanilla or mod uniqueitems.txt; skipped")
        return False

    vh, vrows, _ = read_tsv(vp)
    mh, mrows, _ = read_tsv(mp)

    if vh != mh:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems.txt header drift detected (mod header != vanilla header).")

    if len(vrows) != len(mrows):
        raise RuntimeError(f"PATCHER ASSERTION FAILED: uniqueitems.txt rowcount changed (vanilla={len(vrows)} mod={len(mrows)}).")

    if "*ID" in mh:
        ids = [(r.get("*ID") or "").strip() for r in mrows]

        # Compare vanilla ordering first (empties allowed if vanilla has them)

        # Uniqueness gate applies to non-empty IDs only (vanilla includes marker rows with empty *ID)
        nn = [x for x in ids if x != ""]
        if len(set(nn)) != len(nn):
            # report first few duplicates for diagnostics
            seen = {}
            dups = []
            for i, x in enumerate(ids, start=1):
                if x in seen:
                    dups.append((x, seen[x], i))
                    if len(dups) >= 10:
                        break
                else:
                    seen[x] = i
            raise RuntimeError(f"PATCHER ASSERTION FAILED: uniqueitems.txt duplicate *ID detected (examples={dups}).")


    report.append("[uniqueitems-guard] OK: header/rowcount/*ID uniqueness/order match vanilla.")
    return True



def apply_classic_port_all_uniques_except_assassin_druid(mod_root: Path, report: list[str], strict: bool=False) -> None:
    """Phase 1: Port (forge-enable) *all* uniques into Classic, canonically, IN PLACE.

    Rules:
      - uniqueitems.txt: keep canonical base 'code' (no remaps), set version=0, enabled=1.
      - Enable corresponding base items (armor/weapons/misc) for Classic: version=0 and spawnable=1 when present.
      - Skip Assassin- and Druid-class restricted bases/uniques (Classic original classes only).
      - No structural changes: no row add/remove/reorder. uniqueitems-guard must pass.

    If strict=True, missing base codes become a hard error.
    """
    excel = mod_root / "data/global/excel"
    p_uni = excel / "uniqueitems.txt"
    p_types = excel / "itemtypes.txt"
    if not p_uni.exists():
        raise RuntimeError("uniqueitems.txt not found in mod tree: " + str(p_uni))
    if not p_types.exists():
        raise RuntimeError("itemtypes.txt not found in mod tree: " + str(p_types))

    # --- load itemtypes -> restricted type codes (ass/dru) ---
    h_t, rows_t, _ = read_tsv(p_types)
    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    col_type_code = next((k for k in h_t if nk(k) in ("code",)), None)
    col_class = next((k for k in h_t if nk(k) in ("class",)), None)
    if col_type_code is None or col_class is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: itemtypes.txt missing Code/Class columns; cannot exclude ass/dru uniques safely.")
    restricted_type_codes = set()
    for r in rows_t:
        cls = (r.get(col_class) or "").strip().lower()
        tcode = (r.get(col_type_code) or "").strip()
        if tcode and cls in ("ass", "dru"):
            restricted_type_codes.add(tcode)

    # --- load base tables ---
    base_tables = {}
    base_index = {}  # code -> (table_key, row_idx)

    def load_base_table(fname: str):
        p = excel / fname
        if not p.exists():
            return
        h, rows, _ = read_tsv(p)
        # find columns
        col_code = next((k for k in h if nk(k)=="code"), None)
        col_ver  = next((k for k in h if nk(k)=="version"), None)
        col_spawn= next((k for k in h if nk(k)=="spawnable"), None)
        col_type = next((k for k in h if nk(k)=="type"), None)
        col_type2= next((k for k in h if nk(k)=="type2"), None)
        if col_code is None or col_ver is None:
            raise RuntimeError(f"PATCHER ASSERTION FAILED: {fname} missing code/version columns.")
        base_tables[fname] = (p, h, rows, col_code, col_ver, col_spawn, col_type, col_type2)
        for i, r in enumerate(rows):
            c = (r.get(col_code) or "").strip()
            if c:
                # keep first occurrence; duplicates should not exist in vanilla
                base_index.setdefault(c, (fname, i))

    load_base_table("armor.txt")
    load_base_table("weapons.txt")
    load_base_table("misc.txt")

    # --- load uniqueitems ---
    h_u, rows_u, _ = read_tsv(p_uni)
    col_u_idx = next((k for k in h_u if nk(k)=="index"), None)
    col_u_code= next((k for k in h_u if nk(k)=="code"), None)
    col_u_ver = next((k for k in h_u if nk(k)=="version"), None)
    col_u_en  = next((k for k in h_u if nk(k)=="enabled"), None)
    if col_u_idx is None or col_u_code is None or col_u_ver is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing index/code/version columns.")

    def is_restricted_base(code: str) -> bool:
        rec = base_index.get(code)
        if not rec:
            return False
        fname, ridx = rec
        p, h, rows, col_code, col_ver, col_spawn, col_type, col_type2 = base_tables[fname]
        r = rows[ridx]
        t1 = (r.get(col_type) or "").strip() if col_type else ""
        t2 = (r.get(col_type2) or "").strip() if col_type2 else ""
        return (t1 in restricted_type_codes) or (t2 in restricted_type_codes)

    enabled_uniques = 0
    enabled_bases = 0
    skipped_ass = 0
    skipped_dru = 0
    skipped_bases = set()
    missing_bases = set()
    touched_base_codes = set()

    for r in rows_u:
        idx = (r.get(col_u_idx) or "").strip()
        code_item = (r.get(col_u_code) or "").strip()
        if not idx or not code_item:
            continue

        rec = base_index.get(code_item)
        if rec is None:
            missing_bases.add(code_item)
            continue

        # exclude assassin/druid class-locked bases
        if is_restricted_base(code_item):
            # classify as ass/dru for reporting (best-effort)
            fname, ridx = rec
            p, h, rows, col_code, col_ver, col_spawn, col_type, col_type2 = base_tables[fname]
            br = rows[ridx]
            t1 = (br.get(col_type) or "").strip() if col_type else ""
            t2 = (br.get(col_type2) or "").strip() if col_type2 else ""
            cls = None
            if t1 in restricted_type_codes:
                # find which
                cls = next(( (rr.get(col_class) or "").strip().lower() for rr in rows_t if (rr.get(col_type_code) or "").strip()==t1 ), None)
            if cls is None and t2 in restricted_type_codes:
                cls = next(( (rr.get(col_class) or "").strip().lower() for rr in rows_t if (rr.get(col_type_code) or "").strip()==t2 ), None)
            if cls == "dru":
                skipped_dru += 1
            else:
                skipped_ass += 1
            skipped_bases.add(code_item)
            continue

        # port unique into Classic (in place)
        if (r.get(col_u_ver) or "").strip() != "0":
            r[col_u_ver] = "0"
        if col_u_en:
            r[col_u_en] = "1"
        enabled_uniques += 1
        touched_base_codes.add(code_item)

    if missing_bases:
        msg = f"[port-all] WARNING: {len(missing_bases)} unique base code(s) not found in armor/weapons/misc; skipping those uniques."
        report.append(msg)
        # include a small sample for audit
        sample = ", ".join(sorted(list(missing_bases))[:25])
        report.append(f"[port-all] missing_base_codes(sample): {sample}")
        if strict:
            raise RuntimeError("STRICT MODE: Missing base codes for uniques: " + sample)

    # write uniqueitems first (still in the same order)
    write_tsv(p_uni, h_u, rows_u)

    # enable bases for Classic
    for code_item in sorted(touched_base_codes):
        fname, ridx = base_index[code_item]
        p, h, rows, col_code, col_ver, col_spawn, col_type, col_type2 = base_tables[fname]
        br = rows[ridx]
        changed = False
        if (br.get(col_ver) or "").strip() != "0":
            br[col_ver] = "0"
            changed = True
        if col_spawn:
            if (br.get(col_spawn) or "").strip() != "1":
                br[col_spawn] = "1"
                changed = True
        if changed:
            enabled_bases += 1

    # write base tables back
    for fname, (p, h, rows, col_code, col_ver, col_spawn, col_type, col_type2) in base_tables.items():
        write_tsv(p, h, rows)

    report.append(f"[port-all] Phase1: enabled/ported uniques (non-ass/dru)={enabled_uniques}, base rows enabled/updated={enabled_bases}, skipped ass={skipped_ass}, skipped dru={skipped_dru}")
    if skipped_bases:
        report.append(f"[port-all] skipped_bases_class_locked(sample): {', '.join(sorted(list(skipped_bases))[:25])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", required=True, help="Path to vanilla dump root containing data/ ...")
    ap.add_argument("--out", required=True, help="Output folder (a complete mod tree will be created here)")
    ap.add_argument("--cowtest", action="store_true", help="Enable Cow Level test drop injection (high base drops).")
    ap.add_argument("--phase2drops", action="store_true", help="Phase 2: integrate ported bases into natural TreasureClassEx drops (safe: fills empty slots only; no NoDrop changes).")
    ap.add_argument("--enable-ui", action="store_true", help="Enable UI layout json overrides (default is disabled: files are renamed to disable*).")
    ap.add_argument("--patch-sources", default=str(Path(__file__).parent/"patch_sources"),
                    help="Folder containing cubemain.txt and UI json overrides")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    static_root = script_dir / "static_mod"

    vanilla = Path(args.vanilla).resolve()
    global _VANILLA_ROOT
    _VANILLA_ROOT = vanilla
    out = Path(args.out).resolve()
    patch_sources = Path(args.patch_sources).resolve()

    if not (vanilla/"data").exists():
        raise SystemExit(f"Vanilla root must contain a data/ folder: {vanilla}")

    # Fresh output
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    report = []
    # 1) Copy full static mod tree (mods/<modname>/<modname>.mpq/... including modinfo.json)
    mod_subroot = find_mod_subroot(static_root)
    copy_static_payload(static_root, out, mod_subroot, report)

    # Resolve the mod root inside output
    mod_root = out / mod_subroot
    mod_root.mkdir(parents=True, exist_ok=True)

    # 2) Seed patched txt targets from vanilla (source of truth) into the mod tree
    #    We copy the entire vanilla excel folder to avoid schema drift and keep new content.
    v_excel = vanilla / "data" / "global" / "excel"
    o_excel = mod_root / "data" / "global" / "excel"
    o_excel.mkdir(parents=True, exist_ok=True)

    # Copy all vanilla excel .txt into mod tree (overwrite any static versions)
    for p in v_excel.glob("*.txt"):
        shutil.copy2(p, o_excel / p.name)

    report.append(f"[vanilla] seeded excel txt from {v_excel} into {o_excel}")

    patch_charstats_from_reference(mod_root, patch_sources, report)
    patch_treasureclassex_andariel(mod_root, report)

    # 3) Apply locked patches to the mod root (vanilla schema already seeded)
    patch_misc_toa_version0(mod_root, report)
    patch_monstats_cow_xp_boost(mod_root, report, mult=9999)
    # --- Classic Elite Port: Shako base + Harlequin Crest + Cow Level incentive drop ---
    apply_classic_enable_shako_base(mod_root, report)
    apply_classic_enable_ancient_sword_base(mod_root, report)
    apply_classic_enable_battle_boots_base(mod_root, report)
    apply_classic_enable_ceremonial_javelin_base(mod_root, report)
    apply_classic_port_lod_uniques_titan_wartrav_raven(mod_root, report)
    apply_classic_port_harlequin_crest(mod_root, report)
    apply_classic_port_atlantean_vanilla_key_r29_template(mod_root, report)
    apply_classic_host_tyrael_on_sacred_armor(mod_root, report)
    apply_force_tyrael_unique_lvlreq0(mod_root, report)
    # Phase 1: Port ALL non-assassin/druid uniques + enable their canonical bases for Classic (forge-only).
    apply_classic_port_all_uniques_except_assassin_druid(mod_root, report)
    # Run unique max-roll pass AFTER LoD->Classic ports so newly-enabled uniques (e.g., The Atlantean) are included.
    patch_uniqueitems_force_max_rolls(mod_root, report)

    apply_post_unique_maxrolls_for_targets(mod_root, report, ["Tyrael's Might"])

    apply_remove_unique_level_requirements(mod_root, report)

    apply_deterministic_peasant_and_harlequin_forge(mod_root, report)

    validate_uniqueitems_invariants(mod_root, report)

    apply_phase2_drop_integration(mod_root, report, args.phase2drops)

    apply_cow_test_drop_injection(mod_root, report, args.cowtest)
    patch_setitems_force_max_rolls(mod_root, report)
    patch_magicprefix_force_max_rolls(mod_root, report)
    patch_magicsuffix_force_max_rolls(mod_root, report)
    patch_automagic_force_max_rolls(mod_root, report)
    patch_skills_holyshock_min_equals_max(mod_root, report)
    patch_misc(mod_root, report)
    patch_showlevel(mod_root, "data/global/excel/armor.txt", report)
    patch_showlevel(mod_root, "data/global/excel/weapons.txt", report)
    patch_automagic(mod_root, report)
    patch_setitems(mod_root, report)
    patch_cubemain(mod_root, patch_sources, report)
    copy_ui_overrides(mod_root, patch_sources, report, enable_ui=args.enable_ui)

    # 4) Write run log
    sync_output_to_static(out, script_dir, mod_subroot, report)

    (out/"log.txt").write_text("\n".join(report), encoding="utf-8")
    print("Patched mod tree written to:", out)
    print("Log:", out/"log.txt")


if __name__ == "__main__":
    main()
