from __future__ import annotations
import random

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





def apply_classic_unique_armor_remap_tyrael(*args, **kwargs):
    """Legacy stub retained for backward compatibility; no longer used."""
    report = None
    if len(args) >= 2:
        report = args[1]
    if isinstance(report, list):
        report.append("[tyrael] legacy remap stub invoked; no-op.")
    return False


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
    apply_classic_atlantean_by_cloning_vanilla_unique(_van_rows, rows, h, report)

    # --- Guarded Atlantean enablement (deterministic) ---
    # Port 'The Atlantean' into Classic by adding a new Ancient Sword (ans) unique row (no replacement needed).
    # --- Guarded Classic Tyrael enablement (deterministic) ---
    # Strict post-condition: abort rather than silently degrading back to Venomsward.
    report.append("[tyrael] legacy remap assignment removed (function deleted).")
    tyrael_guard_applied = False
    if not tyrael_guard_applied:
        report.append("[tyrael] Legacy Goldskin->Tyrael repurpose assertion skipped (Tyrael hosted via Chaos Armor restore).")

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





def copy_ui_overrides(root: Path, patch_sources: Path, report: list[str]):
    rels = [
        "data/global/ui/layouts/_profilehd.json",
        "data/global/ui/layouts/_profilelv.json",
        "data/global/ui/layouts/_profilesd.json",
        "data/global/ui/layouts/globaldata.json",
        "data/global/ui/layouts/globaldatahd.json",
            ]
    copied = 0
    for rel in rels:
        src = patch_sources/rel
        if src.exists():
            dst = root/rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    report.append(f"UI/strings: copied {copied} json files from patch_sources (as-is)")


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
    """Classic-only Shako + Harlequin + Peasant Crown normalization (no strings injection)."""
    p = mod_root / "data/global/excel/uniqueitems.txt"
    if not p.exists():
        raise RuntimeError("uniqueitems.txt not found in mod tree: " + str(p))

    h, rows, _ = read_tsv(p)

    def norm_key(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    def lc(v):
        return (v or "").strip().lower()

    def pick(*names):
        wanted = set(names)
        for k in h:
            if norm_key(k) in wanted:
                return k
        return None

    idx_key = pick("index")
    code_key = pick("code")
    ver_key = pick("version")
    enabled_key = pick("enabled")

    def is_classic(r):
        return lc(r.get(ver_key)) == "0" or (r.get(ver_key) or "").strip() == ""

    # --- Locate Harlequin source ---
    harl_src = None
    for r in rows:
        if is_classic(r):
            continue
        if "harlequin" in lc(r.get(idx_key)):
            harl_src = r
            break
    if harl_src is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Harlequin Crest row.")

    # --- Locate Peas(e)nt Crown source ---
    peas_src = None
    for r in rows:
        idx_l = lc(r.get(idx_key))
        if "peasant" in idx_l or "peasent" in idx_l:
            peas_src = r
            break
    if peas_src is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Peas(e)nt Crown row.")

    # Remove wrong classic mappings
    filtered = []
    for r in rows:
        if is_classic(r) and lc(r.get(code_key)) == "xap" and "harlequin" in lc(r.get(idx_key)):
            continue
        if is_classic(r) and lc(r.get(code_key)) == "uap" and ("peasant" in lc(r.get(idx_key)) or "peasent" in lc(r.get(idx_key))):
            continue
        filtered.append(r)
    rows = filtered

    # Classic Harlequin on uap
    classic_harl = dict(harl_src)
    classic_harl[ver_key] = "0"
    classic_harl[code_key] = "uap"
    classic_harl[enabled_key] = "1"

    # Classic Peas(e)nt on xap
    classic_peas = dict(peas_src)
    classic_peas[ver_key] = "0"
    classic_peas[code_key] = "xap"
    classic_peas[enabled_key] = "1"

    rows.append(classic_harl)
    rows.append(classic_peas)

    write_tsv(p, h, rows)
    report.append("[unique-remap] uap->Harlequin, xap->Peas(e)nt mapping locked.")
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



def apply_classic_port_chaos_armor_and_restore_canonical_armor(mod_root, report):
    """Port Chaos Armor (xar) to Classic and host Tyrael's Might on xar ONLY.

    This intentionally does NOT remap/clear any other Classic armor uniques (Goldskin/Venom Ward/Rattlecage),
    relying on vanilla canonical mappings to avoid unintended broad mapping shifts.
    """
    # Enable Chaos Armor base (xar) for Classic
    ap = mod_root / "data/global/excel/armor.txt"
    if ap.exists():
        h, rows, _ = read_tsv(ap)
        def lc(v): return (v or "").strip().lower()
        code_col = next((c for c in h if c.strip().lower()=="code"), None)
        ver_col  = next((c for c in h if c.strip().lower()=="version"), None)
        en_col   = next((c for c in h if c.strip().lower()=="enabled"), None)
        if code_col and ver_col:
            for r in rows:
                if lc(r.get(code_col))=="xar":
                    r[ver_col] = "0"
                    if en_col: r[en_col] = "1"
                    write_tsv(ap, h, rows)
                    report.append("[chaos] Enabled Chaos Armor base for Classic (armor.txt code=xar)")
                    break

    # Host Tyrael on xar in uniqueitems Classic rows, without touching other armor mappings.
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[chaos] uniqueitems.txt not found; skipping Tyrael xar host")
        return False

    h, rows, _ = read_tsv(up)
    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ","")
    idx_key = next((k for k in h if nk(k)=="index"), None)
    code_key = next((k for k in h if nk(k)=="code"), None)
    ver_key = next((k for k in h if nk(k)=="version"), None)
    en_key  = next((k for k in h if nk(k)=="enabled"), None)
    if not all([idx_key, code_key, ver_key, en_key]):
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing required columns for Tyrael xar host.")

    def lc(v): return (v or "").strip().lower()
    def is_classic(r):
        v=(r.get(ver_key) or "").strip()
        return v=="" or v=="0"

    # Find a Tyrael source row (prefer non-classic)
    src_ty = None
    for r in rows:
        if not is_classic(r) and "tyrael" in lc(r.get(idx_key)):
            src_ty = r
            break
    if src_ty is None:
        for r in rows:
            if "tyrael" in lc(r.get(idx_key)):
                src_ty = r
                break
    if src_ty is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Tyrael row not found in uniqueitems.")

    # Remove any existing Classic Tyrael rows (wherever they currently are), then append Classic Tyrael on xar.
    kept=[]
    removed=0
    for r in rows:
        if is_classic(r) and "tyrael" in lc(r.get(idx_key)):
            removed += 1
            continue
        kept.append(r)
    rows = kept
    if removed:
        report.append(f"[chaos] Removed {removed} prior Classic Tyrael row(s)")

    new_row = dict(src_ty)
    new_row[ver_key] = "0"
    new_row[code_key] = "xar"
    new_row[en_key] = "1"
    rows.append(new_row)
    write_tsv(up, h, rows)
    report.append("[chaos] Classic mapping: Tyrael's Might -> xar (no other armor remaps)")
    return True

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
    """Port Atlantean to Classic using r29 template, but force the *vanilla string key*.

    Vanilla commonly uses the misspelled key: "The Atlantian".
    If we set index/name to "The Atlantean" without adding string overrides, the game can fall back to "An Evil Force".
    Therefore we force the vanilla key here.

    Behavior:
      - Patch ALL Classic rows where code==9wd in-place (no deletions/reordering):
          * index/name -> "The Atlantian" (vanilla key)
          * stats -> r29 template
      - If none exist, append a new Classic row with that template.
    """
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[atlantean] uniqueitems.txt not found; skipped")
        return False

    header, rows, _ = read_tsv(up)

    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    index_key = next((k for k in header if nk(k)=="index"), None)
    name_key  = next((k for k in header if nk(k)=="name"), None)
    id_key = index_key or name_key
    code_key = next((k for k in header if nk(k)=="code"), None)
    ver_key  = next((k for k in header if nk(k)=="version"), None)
    en_key   = next((k for k in header if nk(k)=="enabled"), None)

    lvl_key = next((k for k in header if nk(k)=="lvl"), None)
    lvlreq_key = next((k for k in header if nk(k)=="lvlreq"), None)

    if not all([id_key, code_key, ver_key]):
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing required columns for Atlantean template port.")

    def is_classic(r):
        v = (r.get(ver_key) or "").strip()
        return v == "" or v == "0"

    def clear_props(r):
        for n in range(1, 13):
            for kk in (f"prop{n}", f"par{n}", f"min{n}", f"max{n}"):
                if kk in r:
                    r[kk] = ""

    def apply_template(r):
        _uniqueitems_set_key_sync(r, index_key, name_key, "The Atlantian")   # vanilla key (misspelled)
        r[ver_key] = "0"
        r[code_key] = "9wd"
        if en_key: r[en_key] = "1"
        if lvl_key: r[lvl_key] = "50"
        if lvlreq_key: r[lvlreq_key] = "42"

        clear_props(r)
        props = [
            ("dmg%", "", "250", "250"),
            ("pal", "", "2", "2"),
            ("att%", "", "50", "50"),
            ("ac", "", "75", "75"),
            ("vit", "", "8", "8"),
            ("dex", "", "12", "12"),
            ("str", "", "16", "16"),
        ]
        for i, (prop, par, mn, mx) in enumerate(props, start=1):
            if f"prop{i}" in r: r[f"prop{i}"] = prop
            if f"par{i}" in r:  r[f"par{i}"]  = par
            if f"min{i}" in r:  r[f"min{i}"]  = mn
            if f"max{i}" in r:  r[f"max{i}"]  = mx

    patched=0
    seen=0
    for r in rows:
        if is_classic(r) and (r.get(code_key) or "").strip() == "9wd":
            seen += 1
            prev = (r.get(id_key) or "").strip()
            apply_template(r)
            patched += 1
            report.append(f"[atlantean] Patched Classic 9wd row: '{prev}' -> 'The Atlantian' (vanilla key)")

    if patched == 0:
        new_row = {k: "" for k in header}
        apply_template(new_row)
        rows.append(new_row)
        report.append("[atlantean] No Classic 9wd row existed; appended Atlantean row (vanilla key)")

    write_tsv(up, header, rows)
    report.append(f"[atlantean] Locked Classic 9wd to vanilla key 'The Atlantian' (rows_patched={patched}, classic_9wd_seen={seen})")
    return True

def apply_classic_host_tyrael_on_sacred_armor(mod_root, report):
    """Host Tyrael's Might on Sacred Armor (uar) for Classic, without remapping any other armor uniques."""
    # Enable Sacred Armor base (uar) for Classic
    ap = mod_root / "data/global/excel/armor.txt"
    if ap.exists():
        h, rows, _ = read_tsv(ap)
        def lc(v): return (v or "").strip().lower()
        code_col = next((c for c in h if c.strip().lower()=="code"), None)
        ver_col  = next((c for c in h if c.strip().lower()=="version"), None)
        en_col   = next((c for c in h if c.strip().lower()=="enabled"), None)
        if code_col and ver_col:
            enabled=False
            for r in rows:
                if lc(r.get(code_col))=="uar":
                    r[ver_col]="0"
                    if en_col: r[en_col]="1"
                    enabled=True
                    break
            if enabled:
                write_tsv(ap, h, rows)
                report.append("[tyrael] Enabled Sacred Armor base for Classic (armor.txt code=uar)")
            else:
                report.append("[tyrael] Sacred Armor base (uar) not found in armor.txt; skipped base enable")
        else:
            report.append("[tyrael] armor.txt missing code/version; skipped base enable")
    else:
        report.append("[tyrael] armor.txt not found; skipped base enable")

    # Host Tyrael on uar in uniqueitems
    up = mod_root / "data/global/excel/uniqueitems.txt"
    if not up.exists():
        report.append("[tyrael] uniqueitems.txt not found; skipped Tyrael host")
        return False

    h, rows, _ = read_tsv(up)
    def nk(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ","")
    index_key = next((k for k in h if nk(k)=="index"), None)
    name_key  = next((k for k in h if nk(k)=="name"), None)
    idx_key = index_key or name_key
    code_key = next((k for k in h if nk(k)=="code"), None)
    ver_key  = next((k for k in h if nk(k)=="version"), None)
    en_key   = next((k for k in h if nk(k)=="enabled"), None)

    if not all([idx_key, code_key, ver_key]):
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing required columns for Tyrael uar host.")

    def lc(v): return (v or "").strip().lower()
    def is_classic(r):
        v=(r.get(ver_key) or "").strip()
        return v=="" or v=="0"

    # Find Tyrael source row (prefer non-classic)
    src_row=None
    for r in rows:
        if not is_classic(r) and "tyrael" in lc(r.get(idx_key)):
            src_row=r; break
    if src_row is None:
        for r in rows:
            if "tyrael" in lc(r.get(idx_key)):
                src_row=r; break
    if src_row is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: Could not locate Tyrael's Might row in uniqueitems.txt.")

    # Remove any existing Classic Tyrael rows
    kept=[]
    removed=0
    for r in rows:
        if is_classic(r) and "tyrael" in lc(r.get(idx_key)):
            removed += 1
            continue
        kept.append(r)
    rows = kept
    if removed:
        report.append(f"[tyrael] Removed {removed} prior Classic Tyrael row(s)")

    new_row=dict(src_row)
    new_row[ver_key]="0"
    new_row[code_key]="uar"
    _uniqueitems_set_key_sync(new_row, index_key, name_key, (src_row.get(idx_key) or "Tyrael\'s Might"))
    if en_key: new_row[en_key]="1"
    rows.append(new_row)

    write_tsv(up, h, rows)
    report.append("[tyrael] Classic mapping: Tyrael's Might -> uar (Sacred Armor) (no other armor remaps)")
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", required=True, help="Path to vanilla dump root containing data/ ...")
    ap.add_argument("--out", required=True, help="Output folder (a complete mod tree will be created here)")
    ap.add_argument("--cowtest", action="store_true", help="Enable Cow Level test drop injection (high base drops).")
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
    patch_uniqueitems_force_max_rolls(mod_root, report)
    # --- Classic Elite Port: Shako base + Harlequin Crest + Cow Level incentive drop ---
    apply_classic_enable_shako_base(mod_root, report)
    apply_classic_enable_ancient_sword_base(mod_root, report)
    apply_classic_port_harlequin_crest(mod_root, report)
    apply_classic_port_atlantean_vanilla_key_r29_template(mod_root, report)
    apply_classic_host_tyrael_on_sacred_armor(mod_root, report)
    apply_force_tyrael_unique_lvlreq0(mod_root, report)

    apply_post_unique_maxrolls_for_targets(mod_root, report, ["Tyrael's Might"])

    apply_remove_unique_level_requirements(mod_root, report)

    apply_deterministic_peasant_and_harlequin_forge(mod_root, report)

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
    copy_ui_overrides(mod_root, patch_sources, report)

    # 4) Write run log
    sync_output_to_static(out, script_dir, mod_subroot, report)

    (out/"log.txt").write_text("\n".join(report), encoding="utf-8")
    print("Patched mod tree written to:", out)
    print("Log:", out/"log.txt")




if __name__ == "__main__":
    main()
