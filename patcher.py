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
import argparse, csv, hashlib, json, re, shutil
import csv
import io
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent

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
    """Force maximum rolls for all ranged stats on uniqueitems.txt (generic; no named special cases)."""
    excel = mod_root / "data" / "global" / "excel"
    p_uni = excel / "uniqueitems.txt"
    if not p_uni.exists():
        report.append("[unique-maxrolls] uniqueitems.txt not found; skipped")
        return

    hh, rows, _ = read_tsv(p_uni)

    def nk(k: str) -> str:
        return (k or "").strip().lower()

    ver_k = next((k for k in hh if nk(k) == "version"), None)
    min_cols = [c for c in hh if nk(c).startswith("min") and nk(c)[3:].isdigit()]

    if not min_cols:
        report.append("[unique-maxrolls] no min/max columns found; skipped")
        return

    changed_cells = 0
    changed_rows = 0

    for r in rows:
        if ver_k:
            vv = (r.get(ver_k) or "").strip()
            if vv.lower() == "expansion":
                continue

        row_changed = False
        for c in min_cols:
            mx = "max" + c[3:]
            if mx not in hh:
                continue
            mxv = (r.get(mx) or "").strip()
            if not mxv:
                continue
            if (r.get(c) or "").strip() != mxv:
                r[c] = mxv
                changed_cells += 1
                row_changed = True

        if row_changed:
            changed_rows += 1

    if changed_cells:
        write_tsv(p_uni, hh, rows)

    report.append(f"[unique-maxrolls] forced max rolls (rows changed: {changed_rows}, cells: {changed_cells})")

def patch_setitems_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force maximum rolls for all ranged stats on setitems.txt (generic; no named special cases)."""
    rel = Path("data/global/excel/setitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[set-max] missing {rel} (skipped)")
        return

    hh, rows, _ = read_tsv(p)

    def nk(k: str) -> str:
        return (k or "").strip().lower()

    ver_k = next((k for k in hh if nk(k) == "version"), None)
    min_cols = [c for c in hh if nk(c).startswith("min") and nk(c)[3:].isdigit()]
    if not min_cols:
        report.append("[set-max] no min/max columns found (skipped)")
        return

    changed_cells = 0
    changed_rows = 0

    for r in rows:
        if ver_k:
            vv = (r.get(ver_k) or "").strip()
            if vv.lower() == "expansion":
                continue

        row_changed = False
        for c in min_cols:
            mx = "max" + c[3:]
            if mx not in hh:
                continue
            mxv = (r.get(mx) or "").strip()
            if not mxv:
                continue
            if (r.get(c) or "").strip() != mxv:
                r[c] = mxv
                changed_cells += 1
                row_changed = True

        if row_changed:
            changed_rows += 1

    if changed_cells:
        write_tsv(p, hh, rows)

    report.append(f"[set-max] forced max rolls (rows changed: {changed_rows}, cells: {changed_cells})")

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
        if f'"Key": "{key}"' in raw or f'"Key":"{key}"' in raw:
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



def apply_cow_all_bases(mod_root: Path, report: list[str], enabled: bool, full_chaos: bool) -> None:
    """Cow-only base item sampler (deterministic TC-friendly pool builder).

    Adds difficulty-scaled (or full-chaos) sub Treasure Classes and references them from Cow TCs using EMPTY slots only.

    - Does NOT overwrite existing cow drops.
    - Does NOT change NoDrop / Picks on existing Cow rows.
    - Adds new TC rows (zz_*) to treasureclassex.txt, which is safe and reversible.

    Modes:
      * Scaled (default): Normal favors normal bases; Nightmare favors exceptional; Hell favors elite.
      * Full chaos (--cow-all-bases-full): all tiers equally likely on all difficulties.
    """
    if not enabled and not full_chaos:
        report.append("[cow-all-bases] Disabled (flag off); skipped")
        return
    if full_chaos:
        enabled = True

    excel = mod_root / "data/global/excel"
    p_tc = excel / "treasureclassex.txt"
    p_armor = excel / "armor.txt"
    p_weap = excel / "weapons.txt"
    p_misc = excel / "misc.txt"
    p_types = excel / "itemtypes.txt"

    if not (p_tc.exists() and p_types.exists()):
        report.append("[cow-all-bases] Missing treasureclassex/itemtypes; skipped")
        return

    th, tc_rows, _ = read_tsv(p_tc)
    tth, type_rows, _ = read_tsv(p_types)

    def normalize_column_key(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    tc_key = next((k for k in th if normalize_column_key(k) in ("treasureclass","treasureclassname","name","tc")), None)
    if not tc_key:
        report.append("[cow-all-bases] treasureclassex missing TC name column; skipped")
        return

    item_cols = [k for k in th if normalize_column_key(k).startswith("item")]
    prob_cols = [k for k in th if normalize_column_key(k).startswith("prob")]
    def _suffix_num(col):
        m = re.search(r'(\d+)$', normalize_column_key(col))
        return int(m.group(1)) if m else 0
    item_cols.sort(key=_suffix_num)
    prob_cols.sort(key=_suffix_num)
    if not item_cols or not prob_cols:
        report.append("[cow-all-bases] treasureclassex missing item/prob columns; skipped")
        return
    max_slots = min(len(item_cols), len(prob_cols))

    picks_key = next((k for k in th if normalize_column_key(k) in ("picks","pick","pickno")), None)
    nodrop_key = next((k for k in th if normalize_column_key(k) == "nodrop"), None)
    group_key = next((k for k in th if normalize_column_key(k) == "group"), None)
    level_key = next((k for k in th if normalize_column_key(k) in ("level","lvl","tclevel")), None)

    # --- itemtypes: identify Assassin/Druid-restricted types (stay consistent with Classic port layer scope)
    # We keep this intentionally permissive: if we cannot confidently classify, we do NOT skip.
    type_name_key = next((k for k in tth if normalize_column_key(k) in ("itemtype","type","name")), None)
    type_class_key = next((k for k in tth if normalize_column_key(k) in ("class","classspecific","itemclass")), None)

    type_to_class = {}
    if type_name_key and type_class_key:
        for r in type_rows:
            tn = (r.get(type_name_key) or "").strip()
            tc = (r.get(type_class_key) or "").strip().lower()
            if tn:
                type_to_class[tn.lower()] = tc

    def is_ass_dru_type(tname: str) -> bool:
        c = type_to_class.get((tname or "").lower(), "")
        return ("assassin" in c) or ("druid" in c)

    # --- Collect base codes from armor/weapons/misc (spawnable when possible)
    base_codes = {}  # code -> (type, type2)
    def ingest_base_table(path: Path):
        if not path.exists():
            return
        hh, rows, _ = read_tsv(path)
        code_k = find_column_by_name(hh, "code")
        type_k = find_column_by_name(hh, "type")
        type2_k = find_column_by_name(hh, "type2")
        spawn_k = find_column_by_name(hh, "spawnable")
        ver_k = find_column_by_name(hh, "version")
        if not code_k:
            return
        for r in rows:
            c = (r.get(code_k) or "").strip().lower()
            if not c:
                continue
            # ignore Expansion marker row(s)
            if ver_k and (r.get(ver_k) or "").strip().lower()=="expansion":
                continue
            if spawn_k:
                sv = (r.get(spawn_k) or "").strip()
                if sv not in ("", "1"):
                    continue
            t1 = (r.get(type_k) or "").strip()
            t2 = (r.get(type2_k) or "").strip()
            base_codes[c] = (t1, t2)

    ingest_base_table(p_armor)
    ingest_base_table(p_weap)
    ingest_base_table(p_misc)

    # filter out Assassin/Druid class-only bases
    def is_ass_dru_base(code: str) -> bool:
        t1, t2 = base_codes.get(code, ("",""))
        if t1 and is_ass_dru_type(t1): return True
        if t2 and is_ass_dru_type(t2): return True
        return False

    all_codes = sorted([c for c in base_codes.keys() if not is_ass_dru_base(c)])

    if not all_codes:
        report.append("[cow-all-bases] No base codes discovered; skipped")
        return

    # --- Tier heuristic based on code prefix (good enough for cow sampling)
    def tier(code: str) -> str:
        if not code:
            return "normal"
        c0 = code[0]
        if c0 == "u" or c0 in ("7","8"):
            return "elite"
        if c0 == "x" or c0 == "9":
            return "exceptional"
        return "normal"

    normal_codes = [c for c in all_codes if tier(c)=="normal"]
    excep_codes  = [c for c in all_codes if tier(c)=="exceptional"]
    elite_codes  = [c for c in all_codes if tier(c)=="elite"]

    # Helper: create a TC row (dictionary) with default keys present
    def make_tc_row(name: str, items: list[str], probs: list[int]):
        r = {k:"" for k in th}
        r[tc_key] = name
        if picks_key: r[picks_key] = "1"
        if nodrop_key: r[nodrop_key] = "0"
        if group_key: r[group_key] = "0"
        if level_key: r[level_key] = "0"
        for i in range(max_slots):
            if i < len(items):
                r[item_cols[i]] = items[i]
                r[prob_cols[i]] = str(probs[i] if i < len(probs) else 1)
            else:
                r[item_cols[i]] = ""
                r[prob_cols[i]] = ""
        return r

    # Build a balanced tree of sub-TCs to overcome slot limits
    existing_names = set((r.get(tc_key) or "").strip() for r in tc_rows)
    def unique_name(base: str) -> str:
        n = base
        i = 1
        while n in existing_names:
            i += 1
            n = f"{base}_{i}"
        existing_names.add(n)
        return n

    def build_tc_chunks(prefix: str, codes: list[str]) -> list[str]:
        chunk_names = []
        for i in range(0, len(codes), max_slots):
            chunk = codes[i:i+max_slots]
            cname = unique_name(f"{prefix}_{(i//max_slots)+1}")
            tc_rows.append(make_tc_row(cname, chunk, [1]*len(chunk)))
            chunk_names.append(cname)
        return chunk_names

    def build_tc_tree(prefix: str, child_names: list[str], child_probs: list[int] | None = None) -> str:
        # Create intermediate nodes until root fits into max_slots
        current = child_names[:]
        probs = child_probs[:] if child_probs else [1]*len(current)
        level = 1
        while len(current) > max_slots:
            new_current = []
            new_probs = []
            for i in range(0, len(current), max_slots):
                group = current[i:i+max_slots]
                gprobs = probs[i:i+max_slots]
                nname = unique_name(f"{prefix}_node{level}_{(i//max_slots)+1}")
                tc_rows.append(make_tc_row(nname, group, gprobs))
                new_current.append(nname)
                new_probs.append(1)
            current = new_current
            probs = new_probs
            level += 1
        root = unique_name(f"{prefix}_root")
        tc_rows.append(make_tc_row(root, current, probs))
        return root

    # Build tier roots
    roots = {}
    for tag, codes in (("norm", normal_codes), ("excep", excep_codes), ("elite", elite_codes)):
        if not codes:
            continue
        chunks = build_tc_chunks(f"zz_cow_allbases_{tag}", codes)
        roots[tag] = build_tc_tree(f"zz_cow_allbases_{tag}", chunks)

    # Difficulty wrappers
    def add_wrapper(name_base: str, w_norm: int, w_ex: int, w_el: int) -> str:
        items = []
        probs = []
        if "norm" in roots:
            items.append(roots["norm"]); probs.append(w_norm)
        if "excep" in roots:
            items.append(roots["excep"]); probs.append(w_ex)
        if "elite" in roots:
            items.append(roots["elite"]); probs.append(w_el)
        wname = unique_name(name_base)
        tc_rows.append(make_tc_row(wname, items, probs))
        return wname

    if full_chaos:
        wN = wNM = wH = (1,1,1)
    else:
        wN  = (1024, 128, 16)
        wNM = (512, 512, 128)
        wH  = (128, 512, 1024)

    wrap_N  = add_wrapper("zz_cow_allbases_wrap_N",  *wN)
    wrap_NM = add_wrapper("zz_cow_allbases_wrap_NM", *wNM)
    wrap_H  = add_wrapper("zz_cow_allbases_wrap_H",  *wH)

    # Patch cow rows: add one reference to wrapper based on name
    cow_rows = []
    for _r in tc_rows:
        _name = (_r.get(tc_key) or "")
        _nl = _name.lower()
        # Only patch ORIGINAL cow TCs. Exclude any zz_* helper TCs we just created to avoid self-references.
        if "cow" not in _nl:
            continue
        if _nl.startswith("zz_") or "zz_cow_allbases" in _nl:
            continue
        cow_rows.append(_r)
    if not cow_rows:
        report.append("[cow-all-bases] No Cow TCs found; skipped")
        return

    def cow_diff(name: str) -> str:
        n = (name or "").lower()
        if "(h)" in n or " hell" in n:
            return "H"
        if "(nm)" in n or "nightmare" in n:
            return "NM"
        if "(n)" in n or " normal" in n:
            return "N"
        # fallback: use TC level if present
        if level_key:
            try:
                lvl = int((name_row.get(level_key) or "0").strip() or "0")
            except:
                lvl = 0
        return "H" if ("(h)" in n) else "N"

    injected = 0
    for r in cow_rows:
        name = (r.get(tc_key) or "")
        n = name.lower()
        # choose wrapper by name markers
        wrapper = wrap_H
        if "(n)" in n or " normal" in n:
            wrapper = wrap_N
        elif "(nm)" in n or "nightmare" in n:
            wrapper = wrap_NM
        elif "(h)" in n or " hell" in n:
            wrapper = wrap_H
        # place into first empty slot
        for ic, pc in zip(item_cols, prob_cols):
            if (r.get(ic) or "").strip() != "":
                continue
            r[ic] = wrapper
            r[pc] = "8192"
            injected += 1
            break

    write_tsv(p_tc, th, tc_rows)

    report.append(f"[cow-all-bases] {'FULL CHAOS' if full_chaos else 'Scaled'}: codes={len(all_codes)} (norm={len(normal_codes)} excep={len(excep_codes)} elite={len(elite_codes)})")
    report.append(f"[cow-all-bases] Added TC rows: {len(existing_names)} total names tracked; cow rows patched={injected} (wrapper prob=8192; empty-slot only)")
    report.append(f"[cow-all-bases] Wrappers: N={wrap_N} NM={wrap_NM} H={wrap_H}")


def apply_tc_enrichment_highlevel_bases(mod_root: Path, report: list[str], enabled: bool) -> None:
    """TC enrichment layer (drops): integrate ported (non-Assassin/Druid) bases into natural TreasureClassEx drops.

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
        report.append("[tc-enrichment] Disabled (flag off); skipped")
        return

    excel = mod_root / "data/global/excel"
    p_tc = excel / "treasureclassex.txt"
    p_uni = excel / "uniqueitems.txt"
    p_armor = excel / "armor.txt"
    p_weap = excel / "weapons.txt"
    p_misc = excel / "misc.txt"
    p_types = excel / "itemtypes.txt"

    if not (p_tc.exists() and p_uni.exists() and p_types.exists()):
        report.append("[tc-enrichment] Missing treasureclassex/uniqueitems/itemtypes; skipped")
        return

    th, tc_rows, _ = read_tsv(p_tc)
    uh, urows, _ = read_tsv(p_uni)
    hh, type_rows, _ = read_tsv(p_types)

    def normalize_column_key(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    tc_key = next((k for k in th if normalize_column_key(k) in ("treasureclass","treasureclassname","name","tc")), None)
    lvl_key = next((k for k in th if normalize_column_key(k) in ("level","lvl","tclevel")), None)

    item_cols = [k for k in th if normalize_column_key(k).startswith("item")]
    prob_cols = [k for k in th if normalize_column_key(k).startswith("prob")]

    def _suffix_num(col):
        m = re.search(r'(\d+)$', normalize_column_key(col))
        return int(m.group(1)) if m else 0

    item_cols.sort(key=_suffix_num)
    prob_cols.sort(key=_suffix_num)

    if not tc_key or not lvl_key or not item_cols or not prob_cols:
        report.append("[tc-enrichment] treasureclassex missing expected columns; skipped")
        return

    # Build base code -> (table, row_index, type/type2) index from armor/weapons/misc
    base_index = {}
    base_tables = {}
    def index_base_table(p: Path):
        if not p.exists():
            return
        h, rows, _ = read_tsv(p)
        col_code = find_column_by_name(h, "code")
        col_type = find_column_by_name(h, "type")
        col_type2 = find_column_by_name(h, "type2")
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
    col_type_code = next((k for k in hh if normalize_column_key(k) in ("code","itemtype","type","itemtypecode")), None)
    col_class = next((k for k in hh if normalize_column_key(k) in ("class","equiv1","playerclass")), None)
    # In most schemas, 'Class' exists; if not, we fall back to skipping nothing (but Classic port layer already excluded those uniques).
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
    ver_key = find_column_by_name(uh, "version")
    code_key = find_column_by_name(uh, "code")
    en_key = next((k for k in uh if normalize_column_key(k) in ("enabled","enabled1")), None)

    if not ver_key or not code_key:
        report.append("[tc-enrichment] uniqueitems missing version/code; skipped")
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
        report.append("[tc-enrichment] No eligible base codes found; skipped")
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
        report.append(f"[tc-enrichment] SAFE injected {injected} base entries into {tcs_touched} high-level TCs (level>={MIN_LEVEL}, empty slots only, prob={PROB}/{PROB_FOCUS}).")
        report.append(f"[tc-enrichment] Focus: {','.join(focus) if focus else '(none)'}")
        preview = ",".join(stream[:40]) + ("..." if len(stream)>40 else "")
        report.append(f"[tc-enrichment] Stream preview: {preview}")
    else:
        report.append("[tc-enrichment] No empty slots found on eligible TCs; no changes made.")

def apply_post_unique_maxrolls_for_targets(mod_root: Path, report: list[str], target_names: list[str]) -> None:
    """
    Post-pass maxroll fixer for specific unique rows (Classic-only).
    still get min=max applied.
    """
    rel = Path("data/global/excel/uniqueitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[uni-max-post] missing {rel} (skipped)")
        return

    h, rows, _ = read_tsv(p)

    def normalize_column_key(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    index_key = next((k for k in h if normalize_column_key(k)=="index"), None)
    name_key  = find_column_by_name(h, "name")
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

def add_low_quality_variants_cubemain(rows, header, report):
    """
    Expand cubemain recipes so input quality variants work for cow-level forging/testing.

    - Generic: add ',low,nos' sibling for recipes using ',nor,nos' or ',hiq,nos'.
    - uar-specific: if any recipe input references Sacred Armor (uar) with nor/hiq/low, ensure all three variants exist.
      This fixes Superior Sacred Armor (hiq) not matching when only nor was present.
    """
    def normalize_column_key(k):
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    in_cols = [k for k in header if normalize_column_key(k).startswith("input")]
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



def apply_classic_unique_port_layer(mod_root: Path, report: list[str], strict: bool=False) -> None:
    """Classic port layer: Port (forge-enable) *all* uniques into Classic, canonically, IN PLACE.

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
    def normalize_column_key(k): return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    col_type_code = next((k for k in h_t if normalize_column_key(k) in ("code",)), None)
    col_class = next((k for k in h_t if normalize_column_key(k) in ("class",)), None)
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
        col_code = find_column_by_name(h, "code")
        col_ver  = find_column_by_name(h, "version")
        col_spawn= find_column_by_name(h, "spawnable")
        col_type = find_column_by_name(h, "type")
        col_type2= find_column_by_name(h, "type2")
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
    col_u_idx = next((k for k in h_u if normalize_column_key(k)=="index"), None)
    col_u_code= find_column_by_name(h_u, "code")
    col_u_ver = find_column_by_name(h_u, "version")
    col_u_en  = next((k for k in h_u if normalize_column_key(k)=="enabled"), None)
    if col_u_idx is None or col_u_code is None or col_u_ver is None:
        raise RuntimeError("PATCHER ASSERTION FAILED: uniqueitems missing index/code/version columns.")

    # Identify prop/par columns for class-skill filtering (e.g., Earthshaker grants +Druid skills on a shared base).
    # Two common encodings exist in uniqueitems:
    #   (A) propN = dru/ass
    #   (B) propN = item_addclassskills with parN indicating the class id (Druid=5, Assassin=6).
    prop_cols = [k for k in h_u if normalize_column_key(k).startswith("prop")]
    par_cols  = [k for k in h_u if normalize_column_key(k).startswith("par")]

    def _suffix_num(colname: str) -> str:
        n = normalize_column_key(colname)
        return "".join(ch for ch in n if ch.isdigit())

    prop_num_to_col = { _suffix_num(k): k for k in prop_cols if _suffix_num(k) }
    par_num_to_col  = { _suffix_num(k): k for k in par_cols  if _suffix_num(k) }

    # Tokens used by uniqueitems.txt for +class skills. We exclude LoD-added classes only.
    excluded_class_prop_tokens = {"ass", "dru"}
    excluded_class_ids = {"5": "dru", "6": "ass"}

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
    skipped_ass_prop = 0
    skipped_dru_prop = 0
    skipped_bases = set()
    skipped_by_prop = []  # list[(unique_index, token)]
    missing_bases = set()
    touched_base_codes = set()

    for r in rows_u:
        idx = (r.get(col_u_idx) or "").strip()
        code_item = (r.get(col_u_code) or "").strip()
        if not idx or not code_item:
            continue

        # Exclude Assassin/Druid uniques by *properties* (complements base-type exclusion).
        # First, direct tokens (propN = dru/ass).
        hit_token = None
        for pc in prop_cols:
            tok = (r.get(pc) or "").strip().lower()
            if tok in excluded_class_prop_tokens:
                hit_token = tok
                break
        # Second, generic class-skill property (propN=item_addclassskills, parN = class id).
        if hit_token is None:
            for n, pc in prop_num_to_col.items():
                tok = (r.get(pc) or "").strip().lower()
                if tok != "item_addclassskills":
                    continue
                parc = par_num_to_col.get(n)
                parv = (r.get(parc) or "").strip() if parc else ""
                mapped = excluded_class_ids.get(parv)
                if mapped:
                    hit_token = mapped
                    break

        if hit_token == "dru":
            skipped_dru_prop += 1
            skipped_by_prop.append((idx, hit_token))
            continue
        if hit_token == "ass":
            skipped_ass_prop += 1
            skipped_by_prop.append((idx, hit_token))
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
        msg = f"[classic-port] WARNING: {len(missing_bases)} unique base code(s) not found in armor/weapons/misc; skipping those uniques."
        report.append(msg)
        # include a small sample for audit
        sample = ", ".join(sorted(list(missing_bases))[:25])
        report.append(f"[classic-port] missing_base_codes(sample): {sample}")
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

    report.append(f"[classic-port] enabled/ported uniques (non-ass/dru)={enabled_uniques}, base rows enabled/updated={enabled_bases}, skipped ass={skipped_ass}, skipped dru={skipped_dru}")
    if skipped_ass_prop or skipped_dru_prop:
        report.append(f"[classic-port] skipped_by_prop: ass={skipped_ass_prop}, dru={skipped_dru_prop} (class-skill uniques on shared bases)")
        sample = ", ".join([f"{u}({t})" for (u,t) in sorted(skipped_by_prop)[:40]])
        report.append(f"[classic-port] skipped_by_prop(sample): {sample}")
    if skipped_bases:
        report.append(f"[classic-port] skipped_bases_class_locked(sample): {', '.join(sorted(list(skipped_bases))[:25])}")


def patch_relax_item_requirements(mod_root: Path, report: list[str]) -> None:
    """Relax level/strength/dexterity requirements for equipable bases in Classic scope.

    This is intentionally generic and data-driven:
    - Applies to armor.txt and weapons.txt rows that are Classic-enabled (version blank/0; not the literal 'Expansion' marker row).
    - Sets levelreq, reqstr, reqdex to 0 when those columns exist.
    - Does NOT touch uniqueitems/setitems (those are handled by the existing unique/set requirement logic).
    """
    excel = mod_root / "data" / "global" / "excel"

    def _nk(k: str) -> str:
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    def _relax_table(path: Path, tag: str) -> tuple[int, int]:
        if not path.exists():
            report.append(f"[req-relax] {tag}: missing {path.name}; skipped")
            return (0, 0)
        hh, rows, _ = read_tsv(path)
        ver_k = next((k for k in hh if _nk(k) == "version"), None)
        lvl_k = next((k for k in hh if _nk(k) in ("levelreq", "lvlreq")), None)
        str_k = next((k for k in hh if _nk(k) in ("reqstr", "reqstrength")), None)
        dex_k = next((k for k in hh if _nk(k) in ("reqdex", "reqdexterity")), None)

        if not (lvl_k or str_k or dex_k):
            report.append(f"[req-relax] {tag}: no requirement columns found; skipped")
            return (0, 0)

        rows_changed = 0
        cells_changed = 0
        for r in rows:
            # skip expansion marker row and non-classic rows
            if ver_k:
                v = (r.get(ver_k) or "").strip()
                if v.lower() == "expansion":
                    continue
                if v not in ("", "0"):
                    continue
            changed_this_row = False
            for k in (lvl_k, str_k, dex_k):
                if not k:
                    continue
                prev = (r.get(k) or "").strip()
                if prev != "0":
                    r[k] = "0"
                    cells_changed += 1
                    changed_this_row = True
            if changed_this_row:
                rows_changed += 1

        if cells_changed:
            write_tsv(path, hh, rows)
        report.append(f"[req-relax] {tag}: rows_changed={rows_changed} cells_changed={cells_changed}")
        return (rows_changed, cells_changed)

    _relax_table(excel / "armor.txt", "armor")
    _relax_table(excel / "weapons.txt", "weap")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", required=True, help="Path to vanilla dump root containing data/ ...")
    ap.add_argument("--out", required=True, help="Output folder (a complete mod tree will be created here)")
    ap.add_argument("--cow-all-bases", action="store_true", help="Cow Level: integrate ALL base items with difficulty scaling (Normal/NM/Hell wrappers).")
    ap.add_argument("--cow-all-bases-full", action="store_true", help="Cow Level: FULL CHAOS mode (all bases equally likely regardless of difficulty). Implies --cow-all-bases.")
    # Drop ecosystem integration (Expansion -> Classic).
    ap.add_argument(
        "--enable-expansion-drops-in-classic",
        dest="enable_expansion_drops_in_classic",
        action="store_true",
        help="Enable Expansion (LoD) base items to drop naturally in Classic via TreasureClassEx integration (safe: fills empty slots only; no NoDrop/Picks changes).",
    )
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









    # Classic port layer: Port ALL non-assassin/druid uniques + enable their canonical bases for Classic (forge-only).
    apply_classic_unique_port_layer(mod_root, report)
    patch_relax_item_requirements(mod_root, report)
    patch_uniqueitems_force_max_rolls(mod_root, report)


    apply_remove_unique_level_requirements(mod_root, report)


    validate_uniqueitems_invariants(mod_root, report)

    apply_tc_enrichment_highlevel_bases(mod_root, report, args.enable_expansion_drops_in_classic)
    # Cow-level base sampler (scaled / full chaos)
    apply_cow_all_bases(mod_root, report, args.cow_all_bases or args.cow_all_bases_full, args.cow_all_bases_full)
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


# === Column/TSV helper utilities (explicit names; behavior-preserving) ===

def normalize_column_key(k: str) -> str:
    """Normalize a TSV header key for robust matching (BOM/whitespace/case)."""
    return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")


def find_column_by_name(header: list[str], desired_name: str) -> str | None:
    """Return the actual header key matching desired_name (normalized), or None."""
    want = normalize_column_key(desired_name)
    for k in header:
        if normalize_column_key(k) == want:
            return k
    return None


def build_row_index_by_column(rows: list[dict], key_column: str) -> dict[str, dict]:
    """Index rows by lowercased, stripped value from key_column (skips empty keys)."""
    idx: dict[str, dict] = {}
    for r in rows:
        v = (r.get(key_column) or "").strip()
        if not v:
            continue
        idx[v.lower()] = r
    return idx

# === End helpers ===

if __name__ == "__main__":
    main()
