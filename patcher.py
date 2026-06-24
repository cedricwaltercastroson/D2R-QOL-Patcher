#!/usr/bin/env python3
"""
D2R Classic++ R200 Canon patcher / merger (vanilla as source of truth).

This is a behaviour-preserving cleanup of the known-good patcher branch.
Cleanup rules applied:
- Keep current generated mod outcome as the golden reference.
- Remove unused feature/experiment code from the live patcher.
- Keep intentional safety guards and wire guards that have no gameplay-output effect.
- Remove CLI flag dependency; this is now a zero-flag canon runner.
- Preserve Cow King's native boss TreasureClass chain while regular cows use the flat mixed pool.

Usage:
    python patcher.py

Expected folders beside patcher.py:
    vanilla/
    static_mod/
    patch_sources/

Output folder:
    output/

Notes:
- vanilla/ must contain data/ as extracted from CASC.
- This tool never reads or writes .bin files.
"""

import argparse
import csv
import io
import json
import os
import random
import re
import shutil
from pathlib import Path

_VANILLA_ROOT = None  # set in main()
SCRIPT_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    """Read an optional 0/1-style environment toggle while keeping zero-flag execution."""
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


# --- Canon zero-flag profile ---
# These are intentionally baked in so running `python patcher.py` generates the canon patch.
# Mirrored from the old canon batch defaults.
CANON_ENABLE_EXPANSION_DROPS_IN_CLASSIC = True
CANON_COWALLBASES = True
CANON_COWCHAOS = True
CANON_EXP_DROPS_STAGE = 4
CANON_UITOGGLE = _env_bool("UITOGGLE", False)
CANON_LODSTASH = True
CANON_COWALWAYSDROP = True
CANON_NOLOWQUALITY = True
CANON_STAGE1_PRESET = ""  # empty = disabled
COW_XP_MULTIPLIER = 9999

# Stage-4 cow-all-bases determinism.
# Last verified Stage4 run used this seed; keep it fixed so canon output is reproducible.
CANON_COW_ALLBASES_SEED = 1782137524
CANON_COW_ALLBASES_POOL_SIZE = 45
CANON_COW_ALLBASES_WRAP_PROB = 8192
CANON_COW_DIRECT_POOL = True  # R200 direct cow pool: cows roll the full mixed zz_cow_allbases wrapper directly.
# Preserve Cow King's native boss TreasureClass rows. Direct cow-all-bases is for regular
# cow farming; overwriting Cow King rows can suppress/bypass his boss-style loot chain.
CANON_PRESERVE_COW_KING_NATIVE_DROPS = True
# Equalize cow drops across Normal/Nightmare/Hell and across eligible base codes.
# Without this, FULL CHAOS weights normal/exceptional/elite tiers equally, which overweights elite codes
# because there are fewer elite codes than normal codes.
CANON_COW_FLAT_MIXED_POOL = True

# TC enrichment used to front-load a small focus list (including uar/Sacred Armor).
# For this branch canon we keep enrichment broad/flat so the broader item pool is not
# visually biased toward a few high-level bases while cows use the flat mixed direct pool.
CANON_TC_ENRICHMENT_FLAT_NO_FOCUS = True

# R200 safety filter: quest-bound / quest-only bases must never enter cow pools,
# TC enrichment, or LoD->Classic unique porting. These were filtered out in the
# original R200 branch; direct cow-pool mode makes the filter more visible/important.
CANON_FILTER_QUEST_BASES = True

# R200 duplicate-unique preference: Azurewrath has a Classic-era and a LoD-era row.
# Branch canon prefers the LoD Azurewrath on Phase Blade (base code 7cr) and suppresses
# older duplicate Azurewrath unique rows. This is unique-row filtering, not base filtering:
# Phase Blade itself remains allowed in the mixed base pool.
CANON_PREFER_LOD_AZUREWRATH = True
R200_AZUREWRATH_LOD_BASE_CODE = '7cr'

# R200 canon JAVE support based on the v7 stability finding.
# Monster TreasureClass generation produced Long Sword fallback while JAVE stayed on
# the stackable/throwable `jave` path. Canon converts regular JAVE bases into
# stackless spear-like bases for Classic-safe monster drops/forge resolution.
# No diagnostic ID-scroll cube recipe or nuclear TreasureClass overrides are included.
CANON_ENABLE_JAVE_STACKLESS_SPEAR_BRANCH = True
JAVE_STACKLESS_BASE_CODES = {
    'jav', 'pil', 'ssp', 'glv', 'tsp',
    '9ja', '9pi', '9s9', '9gl', '9ts',
    '7ja', '7pi', '7s7', '7gl', '7ts',
}
JAVE_STACKLESS_UNIQUE_SANITIZE_PROPS = {'rep-quant', 'stack'}

# R200 canon Amazon-specific support. After regular JAVE stability proved sound,
# Amazon class-specific bow/spear/javelin bases are enabled through stable non-class
# itemtype profiles for Classic-safe monster drops/forge resolution.
# The Amazon stability harness is not part of canon and remains disabled.
CANON_ENABLE_AMAZON_SPECIFIC_BRANCH = True
CANON_ENABLE_AMAZON_SPECIFIC_STABILITY_HARNESS = False

AMAZON_ABOW_BASE_CODES = {'am1', 'am2', 'am6', 'am7', 'amb', 'amc'}
AMAZON_ASPE_BASE_CODES = {'am3', 'am4', 'am8', 'am9', 'amd', 'ame'}
AMAZON_AJAV_BASE_CODES = {'am5', 'ama', 'amf'}
AMAZON_SPECIFIC_BASE_CODES = AMAZON_ABOW_BASE_CODES | AMAZON_ASPE_BASE_CODES | AMAZON_AJAV_BASE_CODES
AMAZON_SPECIFIC_HARNESS_CODES_ORDERED = [
    'am1', 'am2', 'am3', 'am4', 'am5',
    'am6', 'am7', 'am8', 'am9', 'ama',
    'amb', 'amc', 'amd', 'ame', 'amf',
]
AMAZON_SPECIFIC_UNIQUE_SANITIZE_PROPS = {'rep-quant', 'stack'}
R200_SUPERSEDED_UNIQUE_KEYS: set[tuple[str, str]] = set()  # (unique index/name, base code)
R200_SUPERSEDED_BASE_CODES: set[str] = set()  # kept for future duplicate-base suppressions

R200_QUEST_RESTRICTED_TYPE_CODES = {
    'ques', 'quest', 'body', 'part', 'qst', 'questitem',
}
R200_QUEST_RESTRICTED_BASE_CODES = {
    # Act I / generic quest-only objects
    'leg', 'ear',
    # Act II staff/cube/viper chain
    'box', 'hst', 'msf', 'vip',
    # Act III figurine/book/Khalim chain / Gidbinn variants
    'j34', 'g34', 'bbb', 'g33', 'qey', 'qhr', 'qbr', 'qf1', 'qf2',
    # Act IV / Act V quest-only objects
    'mss', 'hfh', 'tr1', 'tr2', 'std',
}

def is_r200_quest_restricted_base(code: str, type1: str = '', type2: str = '') -> bool:
    if not CANON_FILTER_QUEST_BASES:
        return False
    c = (code or '').strip().lower()
    t1 = (type1 or '').strip().lower()
    t2 = (type2 or '').strip().lower()
    return (
        c in R200_QUEST_RESTRICTED_BASE_CODES
        or t1 in R200_QUEST_RESTRICTED_TYPE_CODES
        or t2 in R200_QUEST_RESTRICTED_TYPE_CODES
    )


def is_r200_superseded_base_code(code: str) -> bool:
    return (code or '').strip().lower() in R200_SUPERSEDED_BASE_CODES


def is_r200_superseded_unique(index_name: str, base_code: str) -> bool:
    key = ((index_name or '').strip().lower(), (base_code or '').strip().lower())
    return key in R200_SUPERSEDED_UNIQUE_KEYS


def is_r200_blocked_base(code: str, type1: str = '', type2: str = '') -> bool:
    """R200 pool/port blocklist: quest/restricted bases + superseded duplicate bases."""
    return is_r200_quest_restricted_base(code, type1, type2) or is_r200_superseded_base_code(code)


def patch_prefer_lod_azurewrath(mod_root: Path, report: list[str]) -> None:
    """Suppress older duplicate Azurewrath unique rows and prefer LoD Azurewrath.

    LoD Azurewrath is the Phase Blade version (base code 7cr). Classic-era duplicate
    Azurewrath rows are left in place for row-order safety, but marked not enabled /
    Expansion-only where possible and registered in R200_SUPERSEDED_UNIQUE_KEYS so the
    LoD->Classic port layer cannot re-enable them.
    """
    if not CANON_PREFER_LOD_AZUREWRATH:
        report.append('[azurewrath] LoD Azurewrath preference disabled; skipped')
        return

    p = mod_root / 'data/global/excel/uniqueitems.txt'
    if not p.exists():
        report.append('[azurewrath] uniqueitems.txt not found; skipped')
        return

    h, rows, nl = read_tsv(p)
    idx_k = find_column_by_name(h, 'index') or find_column_by_name(h, '*index') or h[0]
    code_k = find_column_by_name(h, 'code')
    ver_k = find_column_by_name(h, 'version')
    en_k = find_column_by_name(h, 'enabled')
    if not code_k:
        report.append('[azurewrath] uniqueitems.txt missing code column; skipped')
        return

    def _norm_text(v: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', (v or '').strip().lower())

    azure_rows: list[tuple[int, str, str]] = []  # row index, unique index/name, base code
    for i, r in enumerate(rows):
        idx = (r.get(idx_k) or '').strip()
        code = (r.get(code_k) or '').strip().lower()
        # In D2 uniqueitems, the index/name column is the reliable identity. Scan all
        # string-ish values as a fallback for schema drift.
        vals = [idx] + [(r.get(c) or '') for c in h if c != idx_k]
        if any(_norm_text(v) == 'azurewrath' for v in vals):
            azure_rows.append((i, idx, code))

    if not azure_rows:
        report.append('[azurewrath] no Azurewrath rows detected; skipped')
        return

    lod_rows = [(i, idx, code) for (i, idx, code) in azure_rows if code == R200_AZUREWRATH_LOD_BASE_CODE]
    if not lod_rows:
        sample = ','.join([f'{idx}:{code}' for _, idx, code in azure_rows[:10]])
        report.append(f'[azurewrath] WARNING: Azurewrath rows found but LoD base code {R200_AZUREWRATH_LOD_BASE_CODE} was not present; no rows suppressed (rows={sample})')
        return

    suppressed: list[str] = []
    changed_cells = 0
    for i, idx, code in azure_rows:
        if code == R200_AZUREWRATH_LOD_BASE_CODE:
            continue
        R200_SUPERSEDED_UNIQUE_KEYS.add(((idx or '').strip().lower(), code))
        r = rows[i]
        if en_k and (r.get(en_k) or '').strip() != '0':
            r[en_k] = '0'
            changed_cells += 1
        # Keep old Azurewrath out of Classic-visible unique pools. The port layer also
        # checks R200_SUPERSEDED_UNIQUE_KEYS, so this cannot be undone later.
        if ver_k and (r.get(ver_k) or '').strip() != '100':
            r[ver_k] = '100'
            changed_cells += 1
        suppressed.append(f'{idx}:{code}')

    if changed_cells:
        write_tsv(p, h, rows, nl)

    if suppressed:
        report.append(
            f'[azurewrath] preferred LoD Azurewrath base={R200_AZUREWRATH_LOD_BASE_CODE}; '
            f'suppressed duplicate Azurewrath row(s)={",".join(suppressed)} cells_changed={changed_cells}'
        )
    else:
        report.append(f'[azurewrath] OK: only LoD Azurewrath base={R200_AZUREWRATH_LOD_BASE_CODE} detected')

# --- Stage-1 harness stability allow/deny lists (single source of truth) ---
# These drive Classic LoD port gating AND any drop/TC enrichment that depends on "ported" bases.
# Keep these in sync with the Stage-1 ledger.
STAGE1_STABLE_TYPE_CODES = {
    'tors','helm','glov','boot','belt','shie','head',
    'swor','axe','mace','wand','scep','staf','spea','knif','pole',
    'bow','xbow','orb','ring','amul','hamm',
}
# Categories we never port/enrich in Stage-1 (known-crashy or explicitly excluded pools)
STAGE1_EXCLUDED_TYPE_CODES = {'jave','thro','club','jewl','rune','scha','mcha','lcha','gcha'}


def _validate_stage1_type_lists(report: list[str] | None = None) -> None:
    overlap = STAGE1_STABLE_TYPE_CODES.intersection(STAGE1_EXCLUDED_TYPE_CODES)
    if overlap:
        raise RuntimeError(f"PATCHER ASSERTION FAILED: Stage-1 stable/excluded type lists overlap: {sorted(overlap)}")
    if report is not None:
        report.append(f"[stage1-allowlist] stable_types={len(STAGE1_STABLE_TYPE_CODES)} excluded_types={len(STAGE1_EXCLUDED_TYPE_CODES)}")


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


def patch_monstats_cow_xp_boost(mod_root, report, mult=COW_XP_MULTIPLIER):
    """Increase XP for Cow Level monsters (hellbovine, cowking) via monstats.txt only.

    - Scope: Classic-safe; does not touch levels/treasure classes/experience curve.
    - Canon default: x9999.
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
            # Some property types encode two different semantics in min/max (e.g. chance-to-cast skills:
            # min = chance, max = skill level). For these, do NOT force min=max.
            try:
                idx = int(c[3:])  # "min12" -> 12
            except Exception:
                idx = None
            prop = (r.get(f"prop{idx}") or "").strip().lower() if idx else ""
            if prop in {
                "hit-skill", "gethit-skill", "kill-skill", "death-skill", "levelup-skill",
                "att-skill", "strskill", "cast-skill", "charged",
            }:
                continue

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


def verify_and_enforce_unique_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Final safety pass: verify Classic-enabled unique rows no longer contain ranged min/max values.
    If any remain, force min=max again and report the exact residual count fixed.
    """
    p_uni = mod_root / "data/global/excel/uniqueitems.txt"
    if not p_uni.exists():
        report.append("[unique-maxrolls-verify] uniqueitems.txt not found; skipped")
        return

    hh, rows, _ = read_tsv(p_uni)

    def nk(k: str) -> str:
        return (k or "").strip().lower().replace(" ", "")

    ver_k = next((k for k in hh if nk(k) == "version"), None)
    min_cols = [c for c in hh if nk(c).startswith("min") and nk(c)[3:].isdigit()]
    if not min_cols:
        report.append("[unique-maxrolls-verify] no min/max columns found; skipped")
        return

    skip_props = {
        "hit-skill", "gethit-skill", "kill-skill", "death-skill", "levelup-skill",
        "att-skill", "strskill", "cast-skill", "charged",
    }

    fixed_rows = 0
    fixed_cells = 0
    remaining_rows = 0

    for r in rows:
        if ver_k:
            vv = (r.get(ver_k) or "").strip().lower()
            if vv and vv not in {"0", ""}:
                continue

        row_fixed = False
        row_remaining = False
        for c in min_cols:
            try:
                idx = int(re.sub(r"\D", "", c))
            except Exception:
                idx = None
            prop = (r.get(f"prop{idx}") or "").strip().lower() if idx else ""
            if prop in skip_props:
                continue
            mx = "max" + re.sub(r"^.*?(\d+)$", r"\1", c)
            if mx not in hh:
                continue
            minv = (r.get(c) or "").strip()
            maxv = (r.get(mx) or "").strip()
            if not maxv:
                continue
            if minv != maxv:
                r[c] = maxv
                fixed_cells += 1
                row_fixed = True
                row_remaining = True
        if row_fixed:
            fixed_rows += 1
        if row_remaining:
            remaining_rows += 1

    if fixed_cells:
        write_tsv(p_uni, hh, rows)
    report.append(f"[unique-maxrolls-verify] classic rows re-fixed: {fixed_rows}, cells: {fixed_cells}")


ROLL_SPECIAL_PROPS_WITH_NON_RANGE_MINMAX = {
    # For these property encodings min/max are different parameters rather than a random range.
    # Example: chance-to-cast often stores chance in min and skill level in max.
    "hit-skill", "gethit-skill", "kill-skill", "death-skill", "levelup-skill",
    "att-skill", "strskill", "cast-skill", "charged",
}


def _row_is_classic_enabled(row: dict[str, str], version_key: str | None, enabled_key: str | None = None) -> bool:
    v = (row.get(version_key) or "").strip().lower() if version_key else ""
    e = (row.get(enabled_key) or "").strip() if enabled_key else ""
    # Treat blank/no version as eligible; skip explicit expansion markers.
    classic = (v == "" or v == "0")
    enabled = (e == "" or e == "1")
    return classic and enabled


def _force_roll_pairs_max(
    rows: list[dict[str, str]],
    headers: list[str],
    *,
    report_tag: str,
    version_value: str | None = "0",
    skip_special_props: bool = True,
    include_set_bonus_pairs: bool = False,
) -> tuple[int, int]:
    """Force real ranged roll min columns to their matching max values.

    Supports D2/D2R schemas:
      - min1/max1, min2/max2, ...
      - mod1min/mod1max, mod2min/mod2max, ...
      - set bonus amin*/amax* when include_set_bonus_pairs=True.

    Special proc/charged props are intentionally skipped because their min/max cells are
    separate parameters, not a random roll range.
    """
    norm_to_header = {normalize_column_key(h): h for h in headers}
    version_key = find_column_by_name(headers, "version")
    enabled_key = find_column_by_name(headers, "enabled")

    pairs: list[tuple[str, str, str | None]] = []

    for c in headers:
        nk = normalize_column_key(c)
        m = re.fullmatch(r"min(\d+)", nk)
        if m:
            n = m.group(1)
            mx = norm_to_header.get(f"max{n}")
            prop = norm_to_header.get(f"prop{n}")
            if mx:
                pairs.append((c, mx, prop))
            continue

        m = re.fullmatch(r"mod(\d+)min", nk)
        if m:
            n = m.group(1)
            mx = norm_to_header.get(f"mod{n}max")
            prop = norm_to_header.get(f"mod{n}code") or norm_to_header.get(f"mod{n}")
            if mx:
                pairs.append((c, mx, prop))
            continue

        if include_set_bonus_pairs:
            # setitems.txt bonus columns are commonly amin1a/amax1a, amin1b/amax1b, etc.
            m = re.fullmatch(r"amin(.+)", nk)
            if m:
                suffix = m.group(1)
                mx = norm_to_header.get(f"amax{suffix}")
                prop = norm_to_header.get(f"aprop{suffix}")
                if mx:
                    pairs.append((c, mx, prop))

    if not pairs:
        return (0, 0)

    changed_rows = 0
    changed_cells = 0

    for r in rows:
        if version_key is not None:
            v = (r.get(version_key) or "").strip()
            if version_value is not None and v != version_value:
                continue
        # If there is no version column, all rows are eligible by design.

        row_changed = False
        for mn, mx, prop_col in pairs:
            if skip_special_props and prop_col:
                prop = (r.get(prop_col) or "").strip().lower()
                if prop in ROLL_SPECIAL_PROPS_WITH_NON_RANGE_MINMAX:
                    continue
            mxv = (r.get(mx) or "").strip()
            if mxv == "":
                continue
            if (r.get(mn) or "").strip() != mxv:
                r[mn] = mxv
                changed_cells += 1
                row_changed = True
        if row_changed:
            changed_rows += 1

    return changed_rows, changed_cells


def patch_setitems_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force maximum rolls for set item stat ranges, including set bonus amin/amax columns."""
    rel = Path("data/global/excel/setitems.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[set-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_roll_pairs_max(
        rows,
        h,
        report_tag="set-max",
        version_value="0",
        skip_special_props=True,
        include_set_bonus_pairs=True,
    )
    if cc:
        write_tsv(p, h, rows, nl)
    report.append(f"[set-max] forced max rolls including set bonuses (rows changed: {cr}, cells: {cc})")


def patch_magicprefix_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for Classic magic prefixes."""
    rel = Path("data/global/excel/magicprefix.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[affix-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_roll_pairs_max(rows, h, report_tag="affix-max", version_value="0", skip_special_props=True)
    if cc:
        write_tsv(p, h, rows, nl)
    report.append(f"[affix-max] magicprefix: forced max rolls (rows changed: {cr}, cells: {cc})")


def patch_magicsuffix_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for Classic magic suffixes."""
    rel = Path("data/global/excel/magicsuffix.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[affix-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_roll_pairs_max(rows, h, report_tag="affix-max", version_value="0", skip_special_props=True)
    if cc:
        write_tsv(p, h, rows, nl)
    report.append(f"[affix-max] magicsuffix: forced max rolls (rows changed: {cr}, cells: {cc})")


def patch_automagic_force_max_rolls(mod_root: Path, report: list[str]) -> None:
    """Force max rolls for automagic entries and pin level to maxlevel when available."""
    rel = Path("data/global/excel/automagic.txt")
    p = mod_root / rel
    if not p.exists():
        report.append(f"[automagic-max] missing {rel} (skipped)")
        return
    h, rows, nl = read_tsv(p)
    cr, cc = _force_roll_pairs_max(rows, h, report_tag="automagic-max", version_value="0", skip_special_props=True)

    level_key = find_column_by_name(h, "level")
    maxlevel_key = find_column_by_name(h, "maxlevel")
    level_rows = 0
    level_cells = 0
    version_key = find_column_by_name(h, "version")
    if level_key and maxlevel_key:
        for r in rows:
            if version_key and (r.get(version_key) or "").strip() != "0":
                continue
            mx = (r.get(maxlevel_key) or "").strip()
            if mx and (r.get(level_key) or "").strip() != mx:
                r[level_key] = mx
                level_rows += 1
                level_cells += 1

    total_cells = cc + level_cells
    if total_cells:
        write_tsv(p, h, rows, nl)
    report.append(
        f"[automagic-max] forced max rolls (rows changed: {cr}, cells: {cc}); "
        f"level=maxlevel rows={level_rows}, cells={level_cells}"
    )


def apply_all_item_rolls_max(mod_root: Path, report: list[str]) -> None:
    """Canon max-roll suite: uniques, sets, automagic, magic prefixes, and magic suffixes."""
    patch_uniqueitems_force_max_rolls(mod_root, report)
    verify_and_enforce_unique_max_rolls(mod_root, report)
    patch_setitems_force_max_rolls(mod_root, report)
    patch_automagic_force_max_rolls(mod_root, report)
    patch_magicprefix_force_max_rolls(mod_root, report)
    patch_magicsuffix_force_max_rolls(mod_root, report)
    report.append("[item-rolls] COMPLETE: unique/set/automagic/magicprefix/magicsuffix rolls forced to max where schema-safe")

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
    for code, val in [("key","100"),("tbk","100"),("ibk","100"),("aqv","999"),("cqv","999")]:
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


def apply_qol_baseline(mod_root: Path, patch_sources: Path, report: list[str]):
    """Stage-0 QoL baseline. Must always apply (independent of Stage-1 harness/ports)."""
    # Cube QoL (unsocket/respec/cow portal/etc.)
    patch_cubemain(mod_root, patch_sources, report)

    # Andariel quest-drop fix
    patch_treasureclassex_andariel(mod_root, report)

    # Tome of Town Portal safety (Classic version=0)
    patch_misc_toa_version0(mod_root, report)

    # Stack sizes (key/tomes/arrows/bolts)
    patch_misc(mod_root, report)

    # Item level display on items
    patch_showlevel(mod_root, "data/global/excel/armor.txt", report)
    patch_showlevel(mod_root, "data/global/excel/weapons.txt", report)

    # Town-cast overrides (from reference file)
    patch_skills_intown_from_reference(mod_root, patch_sources, report)

    report.append("[qol-stage0] APPLIED: cubemain + andariel + toa + stacks + showlevel + intown")


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

    # Compute stable signature from common columns (schema-robust)
    # We map by normalized header keys (case/space/BOM-insensitive) so minor vanilla header drift
    # (e.g. "min diff" vs "mindiff") can't break recipe identity or row materialization.
    def _norm(k: str) -> str:
        return normalize_column_key(k)

    base_norm = {_norm(k): k for k in h_base}
    patch_norm = {_norm(k): k for k in h_patch}

    sig_cols = [
        "enabled", "ladder", "min diff", "version", "op", "param",
        "numinputs",
        "input 1", "input 2", "input 3", "input 4", "input 5", "input 6", "input 7",
        "output",
        "lvl", "plvl", "ilvl",
    ]

    # Build signature column pairs (base_key, patch_key) for columns present in BOTH schemas.
    sig_pairs = []
    for c in sig_cols:
        nk = _norm(c)
        if nk in base_norm and nk in patch_norm:
            sig_pairs.append((base_norm[nk], patch_norm[nk]))

    # Fallback if nothing matched: use any input/output columns present in both by normalized key.
    if not sig_pairs:
        for bk in h_base:
            nbk = _norm(bk)
            if nbk.startswith("input") or nbk == "output":
                if nbk in patch_norm:
                    sig_pairs.append((bk, patch_norm[nbk]))

    def _sig_base(row: dict) -> tuple:
        return tuple((row.get(bk, "") or "").strip() for (bk, _) in sig_pairs)

    def _sig_patch(row: dict) -> tuple:
        return tuple((row.get(pk, "") or "").strip() for (_, pk) in sig_pairs)

    base_sigs = {_sig_base(r) for r in d_base}

    to_add = []
    enabled_key_patch = patch_norm.get(_norm("enabled"), "enabled")
    output_key_patch = patch_norm.get(_norm("output"), "output")

    for r in d_patch:
        # only add enabled rows (enabled == "1")
        if str(r.get(enabled_key_patch, "")).strip() != "1":
            continue
        # ensure the patch row has at least an output field
        if (r.get(output_key_patch) is None) or (str(r.get(output_key_patch)).strip() == ""):
            continue

        s = _sig_patch(r)
        if s in base_sigs:
            continue
        to_add.append(r)
        base_sigs.add(s)

    if not to_add:
        report.append("[cubemain] no new recipes to inject (already present)")
        return

    # Append patch rows using base header ordering, mapping by normalized header key
    def _materialize_row(patch_row: dict) -> dict:
        out = {}
        for bk in h_base:
            nbk = _norm(bk)
            pk = patch_norm.get(nbk)
            out[bk] = patch_row.get(pk, "") if pk is not None else ""
        return out

    out_rows = d_base + [_materialize_row(r) for r in to_add]
    write_tsv(dst, h_base, out_rows)
    report.append(f"[cubemain] injected custom recipes (added rows: {len(to_add)})")

    # Hard validation: ensure forge recipes survived schema mapping
    # (prevents "injected but blank/invalid" regressions)
    try:
        _, chk_rows, _ = read_tsv(dst)
        blob = "\n".join(" ".join(str(v) for v in row.values()) for row in chk_rows if str(row.get(base_norm.get(_norm('enabled'),'enabled'), '')).strip() == '1')
        if ("usetype,uni" not in blob) or ("usetype,set" not in blob):
            report.append("[cubemain-validate] WARNING: expected forge outputs (usetype,uni / usetype,set) not found after injection")
    except Exception as e:
        report.append(f"[cubemain-validate] WARNING: validation failed: {e}")



def copy_ui_overrides(root: Path, patch_sources: Path, report: list[str], enable_ui: bool = False):
    """Restore R200 UITOGGLE support for D2R layout overrides.

    UITOGGLE=1 copies the active layout JSON files from patch_sources.
    UITOGGLE=0 keeps the files present but renamed to disable* so the game will not load them.
    This mirrors the old batch-era behavior without requiring command-line flags.
    """
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
    missing = 0

    for rel in rels:
        src_path = patch_sources / rel
        if not src_path.exists():
            missing += 1
            continue

        dst_path = root / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale active/disabled counterparts before writing the requested state.
        disabled_name = disabled_name_map.get(dst_path.name)
        if disabled_name:
            disabled_path = dst_path.with_name(disabled_name)
            if disabled_path.exists():
                disabled_path.unlink()
        if dst_path.exists():
            dst_path.unlink()

        shutil.copy2(src_path, dst_path)
        copied += 1

        if not enable_ui and disabled_name:
            disabled_path = dst_path.with_name(disabled_name)
            dst_path.rename(disabled_path)
            disabled += 1

    if enable_ui:
        report.append(f"[ui] UITOGGLE=1: UI overrides enabled; copied {copied} layout json file(s) from patch_sources (missing={missing}).")
    else:
        report.append(f"[ui] UITOGGLE=0: UI overrides disabled; copied {copied} file(s) then renamed {disabled} to disable* filenames (missing={missing}).")

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
            # Skip ALL excel txt: static_mod must not contribute gameplay data.
            if inner.startswith("data/global/excel/") and inner.endswith(".txt"):
                continue
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Overwrite static assets (safe), but patched txts are excluded above.
        dst.write_bytes(p.read_bytes())
    log_lines.append(f"[static] copied static_mod into output under {out_root}")

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


def apply_remove_set_level_requirements(mod_root, report):
    """Remove level requirements for set items in setitems.txt.

    D2R setitems.txt schemas vary by dump/version. Some have a Version column, some do not;
    some spell the requirement column as "lvl req" or "*lvl req". The old cleanup build was
    too strict and skipped the table when either Version or the exact lvlreq spelling was absent.

    Canon behavior:
    - If a version column exists, patch Classic-enabled rows only (blank or 0).
    - If no version column exists, treat all set item rows as eligible, matching the Classic mod output table.
    - Patch only the set item's level requirement column; do not touch base armor/weapon requirements.
    """
    p = mod_root / "data/global/excel/setitems.txt"
    if not p.exists():
        report.append("[set-lvlreq] setitems.txt not found; skipping")
        return False

    h, rows, nl = read_tsv(p)

    def norm_key(k):
        # Accept schema/comment variants: "lvl req", "*lvl req", "Level Req", "req_level", etc.
        return re.sub(r"[^a-z0-9]", "", (k or "").strip().lstrip("\ufeff").lstrip("*#").lower())

    def pick_exact(*names):
        wanted = {norm_key(n) for n in names}
        for k in h:
            if norm_key(k) in wanted:
                return k
        return None

    ver_key = pick_exact("version")
    req_key = pick_exact(
        "lvlreq", "lvl req", "levelreq", "level req", "requiredlevel", "required level",
        "reqlevel", "req level", "reqlvl", "req lvl", "levelrequirement", "level requirement"
    )

    # Fallback: choose a header that clearly contains both level/lvl and req/require.
    if req_key is None:
        for k in h:
            nk = norm_key(k)
            if ("req" in nk or "require" in nk) and ("lvl" in nk or "level" in nk):
                req_key = k
                break

    if req_key is None:
        sample = ", ".join(h[:24])
        report.append(f"[set-lvlreq] setitems missing level requirement column; skipping (header sample: {sample})")
        return False

    def is_classic(r):
        if ver_key is None:
            return True
        v = (r.get(ver_key) or "").strip().lower()
        return v == "" or v == "0"

    eligible_rows = 0
    changed_rows = 0
    for r in rows:
        if not is_classic(r):
            continue
        eligible_rows += 1
        if (r.get(req_key) or "").strip() != "0":
            r[req_key] = "0"
            changed_rows += 1

    if changed_rows:
        write_tsv(p, h, rows, nl)

    ver_note = ver_key if ver_key is not None else "<none; all rows eligible>"
    report.append(
        f"[set-lvlreq] Set {req_key}=0 for {changed_rows} set row(s) "
        f"(eligible={eligible_rows}, version_col={ver_note})"
    )
    return True




def apply_jave_stackless_spear_forward_branch(mod_root: Path, report: list[str]) -> None:
    """Canon JAVE stackless support derived from the v7 isolation result.

    Findings from v7:
      - `jav` is valid in Classic through cubemain.
      - Monster TC generation produced Long Sword until the selected JAVE row was
        converted from stackable/throwable `type=jave` into a stackless spear-like row.

    This branch applies that mutation to the regular JAVE family only (type `jave`, not
    Amazon `ajav`) so normal Stage4 cow/TC logic can drop JAVE without diagnostic hacks.
    It deliberately does NOT inject the temporary `Identify Scroll -> Javelin` cube recipe.
    """
    if not CANON_ENABLE_JAVE_STACKLESS_SPEAR_BRANCH:
        report.append("[jave-forward] disabled; regular JAVE behavior unchanged")
        return

    excel = mod_root / "data/global/excel"
    p_weapons = excel / "weapons.txt"
    p_unique = excel / "uniqueitems.txt"
    if not p_weapons.exists():
        report.append("[jave-forward] weapons.txt not found; skipped")
        return

    h, rows, nl = read_tsv(p_weapons)
    code_k = find_column_by_name(h, "code")
    type_k = find_column_by_name(h, "type")
    if not code_k or not type_k:
        report.append("[jave-forward] weapons.txt missing code/type column; skipped")
        return

    def set_cell(row: dict[str, str], colname: str, value: str) -> int:
        k = find_column_by_name(h, colname)
        if not k:
            return 0
        if (row.get(k) or "") != value:
            row[k] = value
            return 1
        return 0

    def get_cell(row: dict[str, str], colname: str) -> str:
        k = find_column_by_name(h, colname)
        return (row.get(k) or "").strip() if k else ""

    changed_rows = 0
    changed_cells = 0
    touched_codes: list[str] = []

    for r in rows:
        code = (r.get(code_k) or "").strip().lower()
        typ = (r.get(type_k) or "").strip().lower()
        if code not in JAVE_STACKLESS_BASE_CODES or typ != "jave":
            continue

        # Preserve the original melee damage by moving it to the two-handed fields used by spears.
        orig_mindam = get_cell(r, "mindam") or "1"
        orig_maxdam = get_cell(r, "maxdam") or "5"

        row_cells = 0
        for col, val in [
            ("type", "spea"),
            ("type2", ""),
            ("version", "0"),
            ("spawnable", "1"),
            ("ShowLevel", "1"),
            ("stackable", ""),
            ("minstack", ""),
            ("maxstack", ""),
            ("spawnstack", ""),
            ("missiletype", "0"),
            ("qntwarning", "0"),
            ("hasinv", "1"),
            ("useable", "0"),
            ("wclass", "2ht"),
            ("2handedwclass", "2ht"),
            ("hit class", "2ht"),
            ("1or2handed", ""),
            ("2handed", "1"),
            ("2handmindam", orig_mindam),
            ("2handmaxdam", orig_maxdam),
            ("mindam", ""),
            ("maxdam", ""),
            ("minmisdam", ""),
            ("maxmisdam", ""),
            ("dropsound", "item_staff"),
            ("usesound", "item_staff"),
        ]:
            row_cells += set_cell(r, col, val)

        if row_cells:
            changed_rows += 1
            changed_cells += row_cells
        touched_codes.append(code)

    if touched_codes:
        write_tsv(p_weapons, h, rows, nl)
        report.append(
            f"[jave-forward] STACKLESS SPEAR PROFILE: mutated regular JAVE base rows "
            f"codes={','.join(sorted(touched_codes))} rows_changed={changed_rows} cells_changed={changed_cells}"
        )
    else:
        report.append("[jave-forward] WARNING: no regular JAVE rows were mutated")

    # Unique cleanup: some LoD javelin uniques carry quantity-only properties.  If the base
    # is now stackless, those properties are no longer meaningful and may be unsafe/noisy.
    if not p_unique.exists() or not touched_codes:
        return

    uh, urows, unl = read_tsv(p_unique)
    u_code_k = find_column_by_name(uh, "code")
    if not u_code_k:
        report.append("[jave-forward] uniqueitems.txt missing code column; unique quantity cleanup skipped")
        return

    def clear_unique_prop(row: dict[str, str], n: str) -> int:
        cells = 0
        for prefix in ("prop", "par", "min", "max"):
            k = find_column_by_name(uh, f"{prefix}{n}")
            if k and (row.get(k) or "") != "":
                row[k] = ""
                cells += 1
        return cells

    unique_rows_changed = 0
    unique_cells_changed = 0
    unique_names: list[str] = []
    idx_k = find_column_by_name(uh, "index") or find_column_by_name(uh, "*index") or (uh[0] if uh else None)

    for r in urows:
        code = (r.get(u_code_k) or "").strip().lower()
        if code not in JAVE_STACKLESS_BASE_CODES:
            continue
        row_cells = 0
        for k in uh:
            nk = normalize_column_key(k)
            if not nk.startswith("prop"):
                continue
            suffix = ''.join(ch for ch in nk if ch.isdigit())
            if not suffix:
                continue
            prop = (r.get(k) or "").strip().lower()
            if prop in JAVE_STACKLESS_UNIQUE_SANITIZE_PROPS:
                row_cells += clear_unique_prop(r, suffix)
        if row_cells:
            unique_rows_changed += 1
            unique_cells_changed += row_cells
            unique_names.append((r.get(idx_k) or code) if idx_k else code)

    if unique_cells_changed:
        write_tsv(p_unique, uh, urows, unl)
    report.append(
        f"[jave-forward] unique quantity cleanup: rows_changed={unique_rows_changed} "
        f"cells_changed={unique_cells_changed} names={','.join(unique_names) if unique_names else '<none>'}"
    )


def apply_amazon_specific_forward_branch(mod_root: Path, report: list[str]) -> None:
    """Canon Amazon-specific stable-profile support.

    Applies the regular-JAVE lesson to Amazon class-specific bases:
      - Amazon bows become normal bow-type bases for Classic monster drops/forge.
      - Amazon spears become normal spear-type bases.
      - Amazon javelins become stackless spear-like bases, matching the safe JAVE route.

    This keeps the original item codes/names/graphics, so uniques/sets can still resolve
    by their vanilla code. It deliberately does not add any diagnostic cube recipe.
    """
    if not CANON_ENABLE_AMAZON_SPECIFIC_BRANCH:
        report.append("[amazon-forward] disabled; Amazon-specific bases unchanged")
        return

    excel = mod_root / "data/global/excel"
    p_weapons = excel / "weapons.txt"
    p_unique = excel / "uniqueitems.txt"
    if not p_weapons.exists():
        report.append("[amazon-forward] weapons.txt not found; skipped")
        return

    h, rows, nl = read_tsv(p_weapons)
    code_k = find_column_by_name(h, "code")
    type_k = find_column_by_name(h, "type")
    if not code_k or not type_k:
        report.append("[amazon-forward] weapons.txt missing code/type column; skipped")
        return

    def set_cell(row: dict[str, str], colname: str, value: str) -> int:
        k = find_column_by_name(h, colname)
        if not k:
            return 0
        if (row.get(k) or "") != value:
            row[k] = value
            return 1
        return 0

    def get_cell(row: dict[str, str], colname: str) -> str:
        k = find_column_by_name(h, colname)
        return (row.get(k) or "").strip() if k else ""

    changed_rows = 0
    changed_cells = 0
    bow_codes: list[str] = []
    spear_codes: list[str] = []
    ajav_codes: list[str] = []

    for r in rows:
        code = (r.get(code_k) or "").strip().lower()
        if code not in AMAZON_SPECIFIC_BASE_CODES:
            continue
        orig_type = (r.get(type_k) or "").strip().lower()
        row_cells = 0

        # Common Classic visibility/drop safety.
        for col, val in [
            ("version", "0"),
            ("spawnable", "1"),
            ("ShowLevel", "1"),
            ("hasinv", "1"),
            ("useable", "0"),
        ]:
            row_cells += set_cell(r, col, val)

        if code in AMAZON_ABOW_BASE_CODES:
            # Keep bow mechanics but remove the class-specific abow itemtype gate.
            for col, val in [
                ("type", "bow"),
                ("type2", ""),
                ("stackable", ""),
                ("minstack", ""),
                ("maxstack", ""),
                ("spawnstack", ""),
                ("missiletype", "0"),
                ("wclass", "bow"),
                ("2handedwclass", "bow"),
                ("hit class", "bow"),
                ("1or2handed", ""),
                ("2handed", "1"),
                ("dropsound", "item_bow"),
                ("usesound", "item_bow"),
            ]:
                row_cells += set_cell(r, col, val)
            bow_codes.append(code)

        elif code in AMAZON_ASPE_BASE_CODES:
            # Keep spear mechanics but remove the class-specific aspe itemtype gate.
            orig_mindam = get_cell(r, "mindam") or get_cell(r, "2handmindam") or "1"
            orig_maxdam = get_cell(r, "maxdam") or get_cell(r, "2handmaxdam") or "5"
            for col, val in [
                ("type", "spea"),
                ("type2", ""),
                ("stackable", ""),
                ("minstack", ""),
                ("maxstack", ""),
                ("spawnstack", ""),
                ("missiletype", "0"),
                ("wclass", "2ht"),
                ("2handedwclass", "2ht"),
                ("hit class", "2ht"),
                ("1or2handed", ""),
                ("2handed", "1"),
                ("2handmindam", orig_mindam),
                ("2handmaxdam", orig_maxdam),
                ("mindam", ""),
                ("maxdam", ""),
                ("minmisdam", ""),
                ("maxmisdam", ""),
                ("dropsound", "item_staff"),
                ("usesound", "item_staff"),
            ]:
                row_cells += set_cell(r, col, val)
            spear_codes.append(code)

        elif code in AMAZON_AJAV_BASE_CODES:
            # Same safe route as regular JAVE: convert thrown stacks to stackless spear-like bases.
            orig_mindam = get_cell(r, "mindam") or "1"
            orig_maxdam = get_cell(r, "maxdam") or "5"
            for col, val in [
                ("type", "spea"),
                ("type2", ""),
                ("stackable", ""),
                ("minstack", ""),
                ("maxstack", ""),
                ("spawnstack", ""),
                ("missiletype", "0"),
                ("qntwarning", "0"),
                ("wclass", "2ht"),
                ("2handedwclass", "2ht"),
                ("hit class", "2ht"),
                ("1or2handed", ""),
                ("2handed", "1"),
                ("2handmindam", orig_mindam),
                ("2handmaxdam", orig_maxdam),
                ("mindam", ""),
                ("maxdam", ""),
                ("minmisdam", ""),
                ("maxmisdam", ""),
                ("dropsound", "item_staff"),
                ("usesound", "item_staff"),
            ]:
                row_cells += set_cell(r, col, val)
            ajav_codes.append(code)

        if row_cells:
            changed_rows += 1
            changed_cells += row_cells

    if changed_cells:
        write_tsv(p_weapons, h, rows, nl)

    report.append(
        f"[amazon-forward] CLASS-SPECIFIC STABLE PROFILE: bow={','.join(sorted(bow_codes)) or '<none>'} "
        f"spear={','.join(sorted(spear_codes)) or '<none>'} ajav_stackless={','.join(sorted(ajav_codes)) or '<none>'} "
        f"rows_changed={changed_rows} cells_changed={changed_cells}"
    )

    # Unique cleanup: quantity-only props are unsafe/noisy after AJAV becomes stackless.
    if not p_unique.exists():
        return
    uh, urows, unl = read_tsv(p_unique)
    u_code_k = find_column_by_name(uh, "code")
    if not u_code_k:
        report.append("[amazon-forward] uniqueitems.txt missing code column; unique quantity cleanup skipped")
        return

    def clear_unique_prop(row: dict[str, str], n: str) -> int:
        cells = 0
        for prefix in ("prop", "par", "min", "max"):
            k = find_column_by_name(uh, f"{prefix}{n}")
            if k and (row.get(k) or "") != "":
                row[k] = ""
                cells += 1
        return cells

    idx_k = find_column_by_name(uh, "index") or find_column_by_name(uh, "*index") or (uh[0] if uh else None)
    unique_rows_changed = 0
    unique_cells_changed = 0
    unique_names: list[str] = []
    for r in urows:
        code = (r.get(u_code_k) or "").strip().lower()
        if code not in AMAZON_SPECIFIC_BASE_CODES:
            continue
        row_cells = 0
        for k in uh:
            nk = normalize_column_key(k)
            if not nk.startswith("prop"):
                continue
            suffix = ''.join(ch for ch in nk if ch.isdigit())
            if not suffix:
                continue
            prop = (r.get(k) or "").strip().lower()
            if prop in AMAZON_SPECIFIC_UNIQUE_SANITIZE_PROPS:
                row_cells += clear_unique_prop(r, suffix)
        if row_cells:
            unique_rows_changed += 1
            unique_cells_changed += row_cells
            unique_names.append((r.get(idx_k) or code) if idx_k else code)

    if unique_cells_changed:
        write_tsv(p_unique, uh, urows, unl)
    report.append(
        f"[amazon-forward] unique quantity cleanup: rows_changed={unique_rows_changed} "
        f"cells_changed={unique_cells_changed} names={','.join(unique_names) if unique_names else '<none>'}"
    )


def apply_amazon_specific_stability_harness(mod_root: Path, report: list[str]) -> None:
    """Force cow drops to Amazon class-specific base families for stability testing.

    This runs after the normal cow/TC systems and overwrites cow-facing routes only.
    It deliberately does not add any cube-control recipe.
    """
    if not CANON_ENABLE_AMAZON_SPECIFIC_STABILITY_HARNESS:
        report.append("[amazon-harness] disabled; normal Amazon forward pool remains active")
        return

    excel = mod_root / "data/global/excel"
    p_tc = excel / "treasureclassex.txt"
    p_mon = excel / "monstats.txt"
    if not p_tc.exists():
        report.append("[amazon-harness] treasureclassex.txt not found; skipped")
        return

    h, rows, nl = read_tsv(p_tc)
    tc_k = find_column_by_name(h, "Treasure Class") or (h[0] if h else None)
    if not tc_k:
        report.append("[amazon-harness] treasureclassex missing TC name column; skipped")
        return

    def _n(v: str) -> str:
        return normalize_column_key(v)

    item_cols = [c for c in h if _n(c).startswith("item")]
    prob_cols = [c for c in h if _n(c).startswith("prob")]

    def _suffix_num(col: str) -> int:
        import re
        m = re.search(r"(\d+)$", _n(col))
        return int(m.group(1)) if m else 0

    item_cols.sort(key=_suffix_num)
    prob_cols.sort(key=_suffix_num)
    max_slots = min(len(item_cols), len(prob_cols))
    if not item_cols or not prob_cols:
        report.append("[amazon-harness] treasureclassex missing Item/Prob columns; skipped")
        return

    picks_k = find_column_by_name(h, "Picks")
    nodrop_k = find_column_by_name(h, "NoDrop")
    group_k = find_column_by_name(h, "group")
    level_k = find_column_by_name(h, "level")
    unique_k = find_column_by_name(h, "Unique")
    set_k = find_column_by_name(h, "Set")
    rare_k = find_column_by_name(h, "Rare")
    magic_k = find_column_by_name(h, "Magic")

    by_name = {(r.get(tc_k) or "").strip(): r for r in rows if (r.get(tc_k) or "").strip()}

    def base_tc_row(name: str) -> dict[str, str]:
        r = {k: "" for k in h}
        r[tc_k] = name
        if picks_k: r[picks_k] = "1"
        if nodrop_k: r[nodrop_k] = "0"
        if group_k: r[group_k] = "0"
        if level_k: r[level_k] = "0"
        if unique_k: r[unique_k] = "0"
        if set_k: r[set_k] = "0"
        if rare_k: r[rare_k] = "0"
        if magic_k: r[magic_k] = "0"
        return r

    def set_tc_items(r: dict[str, str], items: list[str], probs: list[int]) -> int:
        changed = 0
        for k, v in [(picks_k, "1"), (nodrop_k, "0"), (unique_k, "0"), (set_k, "0"), (rare_k, "0"), (magic_k, "0")]:
            if k and (r.get(k) or "") != v:
                r[k] = v
                changed += 1
        for i in range(max_slots):
            iv = items[i] if i < len(items) else ""
            pv = str(probs[i]) if i < len(probs) else ""
            if (r.get(item_cols[i]) or "") != iv:
                r[item_cols[i]] = iv
                changed += 1
            if (r.get(prob_cols[i]) or "") != pv:
                r[prob_cols[i]] = pv
                changed += 1
        return changed

    codes = list(AMAZON_SPECIFIC_HARNESS_CODES_ORDERED)
    rows_added = 0
    rows_changed = 0
    cells_changed = 0

    chunk_names: list[str] = []
    chunk_sizes: list[int] = []
    for idx in range(0, len(codes), max_slots):
        chunk = codes[idx:idx + max_slots]
        cname = f"zz_amazon_specific_harness_{(idx // max_slots) + 1:02d}"
        chunk_names.append(cname)
        chunk_sizes.append(len(chunk))
        r = by_name.get(cname)
        if r is None:
            r = base_tc_row(cname)
            rows.append(r)
            by_name[cname] = r
            rows_added += 1
        c = set_tc_items(r, chunk, [1] * len(chunk))
        if c:
            rows_changed += 1
            cells_changed += c

    wrapper_name = "zz_amazon_specific_harness_all"
    wrapper = by_name.get(wrapper_name)
    if wrapper is None:
        wrapper = base_tc_row(wrapper_name)
        rows.append(wrapper)
        by_name[wrapper_name] = wrapper
        rows_added += 1
    c = set_tc_items(wrapper, chunk_names, chunk_sizes)
    if c:
        rows_changed += 1
        cells_changed += c

    cow_targets = {
        "Cow", "Cow (N)", "Cow (H)",
        "Act 4 Champ B", "Act 4 (N) Champ B", "Act 4 (H) Champ B",
        "Act 4 Unique B", "Act 4 (N) Unique B", "Act 4 (H) Unique B",
        "Act 4 (H) Champ B Desecrated", "Act 4 (H) Unique B Desecrated",
    }
    cow_rows_changed = 0
    cow_cells_changed = 0
    for r in rows:
        name = (r.get(tc_k) or "").strip()
        if name not in cow_targets:
            continue
        c = set_tc_items(r, [wrapper_name], [1])
        if c:
            cow_rows_changed += 1
            cow_cells_changed += c

    write_tsv(p_tc, h, rows, nl)

    mon_rows_changed = 0
    mon_cells_changed = 0
    mon_cols = 0
    if p_mon.exists():
        mh, mrows, mnl = read_tsv(p_mon)
        id_k = find_column_by_name(mh, "Id") or find_column_by_name(mh, "id")
        tc_cols = [c for c in mh if normalize_column_key(c).startswith("treasureclass")]
        mon_cols = len(tc_cols)
        for mr in mrows:
            mid = (mr.get(id_k) or "").strip().lower() if id_k else ""
            if mid not in {"hellbovine", "cowking"}:
                continue
            row_changed = False
            for ccol in tc_cols:
                if (mr.get(ccol) or "").strip() != wrapper_name:
                    mr[ccol] = wrapper_name
                    mon_cells_changed += 1
                    row_changed = True
            if row_changed:
                mon_rows_changed += 1
        if mon_cells_changed:
            write_tsv(p_mon, mh, mrows, mnl)

    verify_parts = []
    for name in ["Cow", "Cow (N)", "Cow (H)", wrapper_name] + chunk_names:
        r = by_name.get(name)
        if not r:
            verify_parts.append(f"{name}:<missing>")
            continue
        first = (r.get(item_cols[0]) or "").strip() if item_cols else ""
        prob = (r.get(prob_cols[0]) or "").strip() if prob_cols else ""
        verify_parts.append(f"{name}:{first}/{prob}")

    report.append(
        f"[amazon-harness] AMAZON-SPECIFIC STABILITY HARNESS active: forced cow drops to Amazon class-specific families; "
        f"codes={','.join(codes)} wrapper={wrapper_name} chunk_rows={len(chunk_names)} "
        f"rows_added={rows_added} rows_changed={rows_changed} cells_changed={cells_changed} "
        f"cow_rows_changed={cow_rows_changed} cow_cells_changed={cow_cells_changed} "
        f"monstats_rows_changed={mon_rows_changed} monstats_cells_changed={mon_cells_changed} monstats_tc_cols={mon_cols} "
        f"verify={';'.join(verify_parts)}"
    )

# === Stage-4 true expansion drop/cow farm systems restored from working branch ===

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
    orig_tc_len = len(tc_rows)
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

    
    # --- Restrict cow base sampler to forge-enabled bases only ---
    # We only want bases that actually have at least one Classic-enabled unique/set mapping (so they can be forged).
    # This avoids accidentally enabling Expansion-only miscellany (e.g., jewels) in Classic.
    forge_enabled_base_codes = set()

    def _collect_base_codes_from_mappings(path: Path, report_tag: str):
        if not path.exists():
            report.append(f"[cow-all-bases] {report_tag}: {path.name} not found (skipped)")
            return
        hh, rows, _ = read_tsv(path)

        code_k = find_column_by_name(hh, "code")
        item_k = find_column_by_name(hh, "item")  # setitems.txt typically uses 'item' for base code
        ver_k  = find_column_by_name(hh, "version")
        en_k   = find_column_by_name(hh, "enabled")

        base_k = code_k or item_k
        if not base_k:
            report.append(f"[cow-all-bases] {report_tag}: {path.name} missing code/item column (skipped)")
            return

        def _is_classic_enabled(r):
            v = (r.get(ver_k) or "").strip() if ver_k else ""
            e = (r.get(en_k) or "").strip() if en_k else ""
            is_classic = (v == "" or v == "0")
            is_enabled = (e == "" or e == "1")  # some tables leave enabled blank
            return is_classic and is_enabled

        n = 0
        for r in rows:
            if not _is_classic_enabled(r):
                continue
            c = (r.get(base_k) or "").strip().lower()
            if c:
                # Quest-only bases can appear in unique/set mappings, but R200 never allowed
                # them into the generated cow pool.
                if is_r200_blocked_base(c):
                    continue
                forge_enabled_base_codes.add(c)
                n += 1

        report.append(f"[cow-all-bases] {report_tag}: collected {len(forge_enabled_base_codes)} forge-enabled base code(s) so far (rows scanned: {len(rows)}, rows used: {n})")

    _collect_base_codes_from_mappings(excel / "uniqueitems.txt", "uniqueitems")
    _collect_base_codes_from_mappings(excel / "setitems.txt", "setitems")

    if CANON_ENABLE_JAVE_STACKLESS_SPEAR_BRANCH:
        before_jave = len(forge_enabled_base_codes)
        forge_enabled_base_codes.update(JAVE_STACKLESS_BASE_CODES)
        report.append(
            f"[cow-all-bases] JAVE forward: injected {len(forge_enabled_base_codes) - before_jave} "
            f"stackless JAVE base code(s) into cow sampler eligibility"
        )

    if CANON_ENABLE_AMAZON_SPECIFIC_BRANCH:
        before_amazon = len(forge_enabled_base_codes)
        forge_enabled_base_codes.update(AMAZON_SPECIFIC_BASE_CODES)
        report.append(
            f"[cow-all-bases] Amazon forward: injected {len(forge_enabled_base_codes) - before_amazon} "
            f"Amazon-specific base code(s) into cow sampler eligibility"
        )

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
            if forge_enabled_base_codes and c not in forge_enabled_base_codes:
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
            if is_r200_blocked_base(c, t1, t2):
                continue
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

    # --- Safety/diagnostics: avoid item-code collisions with existing TreasureClass names ---
    existing_names_lc = set(n.lower() for n in existing_names if n)
    collisions = sorted([c for c in all_codes if c.lower() in existing_names_lc])
    if collisions:
        sample = ", ".join(collisions[:12]) + ("..." if len(collisions) > 12 else "")
        report.append(f"[cow-all-bases] WARNING: {len(collisions)} base code(s) collide with existing TC names; skipping to avoid TC recursion/ambiguity. sample={sample}")
        all_codes = [c for c in all_codes if c.lower() not in existing_names_lc]

    # Optional allowlist to isolate crashes: place cow_all_bases_allowlist.txt next to patcher.py (one code per line).
    try:
        allow_path = Path(__file__).resolve().parent / "cow_all_bases_allowlist.txt"
        if allow_path.exists():
            allow = []
            for ln in allow_path.read_text(encoding="utf-8").splitlines():
                s = ln.strip().lower()
                if not s or s.startswith("#"):
                    continue
                allow.append(s)
            allow_set = set(allow)
            before = len(all_codes)
            all_codes = [c for c in all_codes if c.lower() in allow_set]
            report.append(f"[cow-all-bases] allowlist active: {len(all_codes)}/{before} code(s) kept from {allow_path.name}")
    except Exception as e:
        report.append(f"[cow-all-bases] allowlist check failed (ignored): {e}")

    
    # R200 safety: exclude quest-only / quest-bound bases from the mixed cow pool.
    before_quest = len(all_codes)
    all_codes = [c for c in all_codes if not is_r200_blocked_base(c, *base_codes.get(c, ("", "")))]
    removed_quest = before_quest - len(all_codes)
    if removed_quest:
        report.append(f"[cow-all-bases] filtered {removed_quest} quest/restricted base code(s) from mixed pool")

    # Extra safety: exclude classic-unsafe misc categories even if they have unique/set mappings (e.g., charms, runes, jewels).
    # These can crash Classic when enabled via expansion drops.
    banned_types = {"jewl", "jewel", "charm", "rune"}
    before_unsafe = len(all_codes)
    all_codes = [c for c in all_codes if (base_codes.get(c, ("",""))[0] or "").strip().lower() not in banned_types
                               and (base_codes.get(c, ("",""))[1] or "").strip().lower() not in banned_types]
    removed_unsafe = before_unsafe - len(all_codes)
    if removed_unsafe:
        report.append(f"[cow-all-bases] filtered {removed_unsafe} classic-unsafe base code(s) by type (banned={sorted(banned_types)})")

    # Re-split tiers after filtering
    normal_codes = [c for c in all_codes if tier(c)=="normal"]
    excep_codes  = [c for c in all_codes if tier(c)=="exceptional"]
    elite_codes  = [c for c in all_codes if tier(c)=="elite"]
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

    # Build tier roots using deterministic pool partitioning to keep canon output reproducible.
    # The previous working Stage4 run logged seed=1782137524; this is now baked as canon.
    cow_seed = int(CANON_COW_ALLBASES_SEED)
    pool_size = int(CANON_COW_ALLBASES_POOL_SIZE)
    if pool_size < max_slots:
        pool_size = max_slots
    if pool_size > 200:
        pool_size = 200

    rng = random.Random(cow_seed)

    def partition_pools(codes: list[str]) -> list[list[str]]:
        if not codes:
            return []
        _codes = codes[:]
        rng.shuffle(_codes)
        return [_codes[i:i+pool_size] for i in range(0, len(_codes), pool_size)]

    roots = {}  # tier -> root tc name
    pools_per_tier = {}

    for tag, codes in (("norm", normal_codes), ("excep", excep_codes), ("elite", elite_codes)):
        if not codes:
            continue
        pools = partition_pools(codes)
        pools_per_tier[tag] = len(pools)
        pool_roots = []
        for pi, pcodes in enumerate(pools, start=1):
            chunks = build_tc_chunks(f"zz_cow_allbases_{tag}_p{pi}", pcodes)
            pool_root = build_tc_tree(f"zz_cow_allbases_{tag}_p{pi}", chunks)
            pool_roots.append(pool_root)

        # tier root selects ONE pool uniformly (tree if needed)
        roots[tag] = build_tc_tree(f"zz_cow_allbases_{tag}_poolsel", pool_roots)

    report.append(
        f"[cow-all-bases] Pools deterministic: seed={cow_seed} pool_size={pool_size} "
        f"pools={{norm:{pools_per_tier.get('norm',0)} excep:{pools_per_tier.get('excep',0)} elite:{pools_per_tier.get('elite',0)}}}"
    )

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

    def build_tc_tree_weighted(prefix: str, child_names: list[str], child_weights: list[int]) -> str:
        """Build a TC tree that preserves equal probability per leaf item.

        Each child weight is the number of base codes beneath that child. This avoids the
        old tier/pool-selection bias where smaller pools or smaller tiers could become
        overrepresented.
        """
        current = list(zip(child_names, child_weights))
        level = 1
        while len(current) > max_slots:
            new_current = []
            for i in range(0, len(current), max_slots):
                group = current[i:i+max_slots]
                names = [n for n, _w in group]
                weights = [max(1, int(_w)) for _n, _w in group]
                nname = unique_name(f"{prefix}_node{level}_{(i//max_slots)+1}")
                tc_rows.append(make_tc_row(nname, names, weights))
                new_current.append((nname, sum(weights)))
            current = new_current
            level += 1
        root = unique_name(f"{prefix}_root")
        tc_rows.append(make_tc_row(root, [n for n, _w in current], [max(1, int(_w)) for _n, _w in current]))
        return root

    def build_flat_mixed_wrapper() -> str:
        # One deterministic, fully mixed pool over all eligible base codes.
        # This makes Normal/Nightmare/Hell cows pull from the exact same source, and every
        # eligible base code receives equal weight regardless of normal/exceptional/elite tier.
        flat_codes = all_codes[:]
        random.Random(cow_seed).shuffle(flat_codes)
        chunk_names = []
        chunk_weights = []
        for i in range(0, len(flat_codes), max_slots):
            chunk = flat_codes[i:i+max_slots]
            cname = unique_name(f"zz_cow_allbases_flat_{(i//max_slots)+1}")
            tc_rows.append(make_tc_row(cname, chunk, [1] * len(chunk)))
            chunk_names.append(cname)
            chunk_weights.append(len(chunk))
        root = build_tc_tree_weighted("zz_cow_allbases_flat_poolsel", chunk_names, chunk_weights)
        wname = unique_name("zz_cow_allbases_wrap_FLAT")
        tc_rows.append(make_tc_row(wname, [root], [1]))
        return wname

    if CANON_COW_FLAT_MIXED_POOL:
        flat_wrap = build_flat_mixed_wrapper()
        wrap_N = wrap_NM = wrap_H = flat_wrap
        report.append("[cow-all-bases] FLAT MIXED POOL: every eligible base code has equal weight; Normal/Nightmare/Hell cows use the same wrapper")
    else:
        if full_chaos:
            wN = wNM = wH = (1,1,1)
        else:
            wN  = (1024, 128, 16)
            wNM = (512, 512, 128)
            wH  = (128, 512, 1024)

        wrap_N  = add_wrapper("zz_cow_allbases_wrap_N",  *wN)
        wrap_NM = add_wrapper("zz_cow_allbases_wrap_NM", *wNM)
        wrap_H  = add_wrapper("zz_cow_allbases_wrap_H",  *wH)

    # Patch cow rows.
    # R200 direct pool mode intentionally overwrites cow TCs / cow monstats so cows visibly roll
    # the deterministic full mixed all-bases pool directly instead of merely adding the pool
    # to empty TC slots. This is not armor-focused; armor examples are just visible proof
    # that the broader random base pool is being reached.
    p_mon = excel / "monstats.txt"

    def wrapper_for_tc_name(name: str) -> str:
        n = (name or "").lower()
        # D2 naming convention: unsuffixed = Normal, (N) = Nightmare, (H) = Hell.
        if "(h)" in n or " hell" in n:
            return wrap_H
        if "(n)" in n or "nightmare" in n:
            return wrap_NM
        return wrap_N

    def wrapper_for_monstats_column(col: str) -> str:
        c = (col or "").lower()
        # Monstats column convention mirrors TC suffixes: (N)=Nightmare, (H)=Hell, no suffix=Normal.
        if "(h)" in c:
            return wrap_H
        if "(n)" in c:
            return wrap_NM
        return wrap_N

    def is_cow_king_tc_name(name: str) -> bool:
        n = (name or "").strip().lower()
        return n.startswith("cow king") or n.startswith("cowking")

    # Discover exact cow TC names from monstats so we also catch champ/unique/desecrated cow routes
    # such as Act 4 Champ B / Act 4 Unique B, which do not necessarily contain the word "cow".
    # R200 direct pool is intentionally limited to regular cow-farm routes; Cow King boss rows
    # are preserved natively so his special Picks=5 / Uitem+Melee chains are not bypassed.
    cow_tc_names: set[str] = set()
    monstats_patched = 0
    if p_mon.exists():
        mh, mrows, _ = read_tsv(p_mon)
        def _norm2(s: str) -> str:
            return (s or "").strip().lstrip("\ufeff").lower().replace(" ", "")
        id_col = next((c for c in mh if _norm2(c) in ("id", "name", "monstats", "monstat")), None)
        tc_cols = [c for c in mh if _norm2(c).startswith("treasureclass")]
        if id_col and tc_cols:
            for mr in mrows:
                mid = (mr.get(id_col) or "").strip().lower()
                if mid not in ("hellbovine", "cowking"):
                    continue
                # Keep Cow King native in canon. His actual boss loot is represented by
                # Cow King TreasureClassEx rows / superunique routes, not the regular cow
                # all-bases sampler. Still allow old behavior if the guard is disabled.
                if mid == "cowking" and CANON_PRESERVE_COW_KING_NATIVE_DROPS:
                    continue
                for c in tc_cols:
                    v = (mr.get(c) or "").strip()
                    if v:
                        cow_tc_names.add(v)
                if CANON_COW_DIRECT_POOL:
                    for c in tc_cols:
                        mr[c] = wrapper_for_monstats_column(c)
                    monstats_patched += 1
            if CANON_COW_DIRECT_POOL and monstats_patched:
                write_tsv(p_mon, mh, mrows)

    cow_rows = []
    for _r in tc_rows:
        _name = (_r.get(tc_key) or "").strip()
        _nl = _name.lower()
        # Only patch ORIGINAL cow-related TCs. Exclude zz_* helper TCs to avoid self-references.
        if _nl.startswith("zz_") or "zz_cow_allbases" in _nl:
            continue
        # Preserve Cow King boss TC rows. They carry their own Picks/quality chains and
        # should not be flattened into the regular cow all-bases wrapper.
        if CANON_PRESERVE_COW_KING_NATIVE_DROPS and is_cow_king_tc_name(_name):
            continue
        if _name in cow_tc_names or "cow" in _nl:
            cow_rows.append(_r)

    if not cow_rows and not (CANON_COW_DIRECT_POOL and monstats_patched):
        report.append("[cow-all-bases] No Cow TCs found; skipped")
        return

    wrap_prob = int(CANON_COW_ALLBASES_WRAP_PROB)
    if wrap_prob < 1: wrap_prob = 1
    if wrap_prob > 32767: wrap_prob = 32767

    injected = 0
    direct_rows = 0
    for r in cow_rows:
        name = (r.get(tc_key) or "")
        wrapper = wrapper_for_tc_name(name)

        if CANON_COW_DIRECT_POOL:
            # Direct mode: cows roll the all-bases wrapper directly. Clear old slots so vanilla TC weights
            # cannot drown out the random pool.
            if picks_key: r[picks_key] = "1"
            if nodrop_key: r[nodrop_key] = "0"
            for ic, pc in zip(item_cols, prob_cols):
                r[ic] = ""
                r[pc] = "0"
            r[item_cols[0]] = wrapper
            r[prob_cols[0]] = "1"
            injected += 1
            direct_rows += 1
        else:
            # Additive mode: place wrapper into first empty slot only.
            for ic, pc in zip(item_cols, prob_cols):
                if (r.get(ic) or "").strip() != "":
                    continue
                r[ic] = wrapper
                r[pc] = str(wrap_prob)
                injected += 1
                break

    cowking_rows_seen = 0
    cowking_nodrop_fixed = 0
    cowking_picks_fixed = 0
    if CANON_PRESERVE_COW_KING_NATIVE_DROPS:
        for kr in tc_rows:
            kname = (kr.get(tc_key) or "").strip()
            if not is_cow_king_tc_name(kname):
                continue
            cowking_rows_seen += 1
            if nodrop_key and (kr.get(nodrop_key) or "").strip() != "0":
                kr[nodrop_key] = "0"
                cowking_nodrop_fixed += 1
            # Preserve native positive and negative Picks values. Only repair blank/zero rows.
            if picks_key:
                rawp = (kr.get(picks_key) or "").strip()
                try:
                    pv = int(rawp or "0")
                except Exception:
                    pv = 0
                if pv == 0:
                    kr[picks_key] = "5"
                    cowking_picks_fixed += 1

    write_tsv(p_tc, th, tc_rows)

    pool_preview = ",".join(all_codes[:40]) + ("..." if len(all_codes) > 40 else "")
    report.append(f"[cow-all-bases] {'FULL CHAOS' if full_chaos else 'Scaled'}: codes={len(all_codes)} (norm={len(normal_codes)} excep={len(excep_codes)} elite={len(elite_codes)})")
    report.append(
        f"[cow-all-bases] Added TC rows: {len(tc_rows)-orig_tc_len} new row(s); "
        f"cow rows patched={injected} mode={'direct' if CANON_COW_DIRECT_POOL else 'empty-slot'} "
        f"(wrapper prob={wrap_prob}; monstats_patched={monstats_patched})"
    )
    if CANON_PRESERVE_COW_KING_NATIVE_DROPS:
        report.append(
            f"[cow-king] preserved native Cow King boss treasure classes: rows={cowking_rows_seen} "
            f"nodrop_fixed={cowking_nodrop_fixed} picks_fixed={cowking_picks_fixed}"
        )
    if CANON_COW_FLAT_MIXED_POOL:
        report.append(f"[cow-all-bases] Wrapper: ALL_DIFFICULTIES={wrap_N}")
        report.append(
            "[cow-all-bases] Parity check: "
            f"AncientArmor(aar)={'present' if 'aar' in set(all_codes) else 'missing'}; "
            f"SacredArmor(uar)={'present' if 'uar' in set(all_codes) else 'missing'}; "
            "per-code-weight=equal"
        )
    else:
        report.append(f"[cow-all-bases] Wrappers: N={wrap_N} NM={wrap_NM} H={wrap_H}")
    report.append(f"[cow-all-bases] Mixed pool preview: {pool_preview}")

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
        if is_r200_blocked_base(c):
            continue
        if is_restricted_base(c):
            continue
        seen.add(c)
        eligible.append(c)

    if not eligible:
        report.append("[tc-enrichment] No eligible base codes found; skipped")
        return

    # Exclude classic-unsafe misc categories even if Classic-enabled (e.g., unique jewel rows).
    banned_misc_types = {"jewl", "jewel", "charm", "rune"}

    # Stage-1 stable Classic port allowlist:
    # Only port expansion uniques/sets whose *base item type* is known stable under the harness.
    # Explicitly exclude problematic categories (jave, thro) and non-ports (hamm, club) plus gems/jewels/runes/charms.
    stable_type_codes = STAGE1_STABLE_TYPE_CODES
    excluded_type_codes = STAGE1_EXCLUDED_TYPE_CODES
    before_unsafe = len(eligible)
    eligible2 = []
    for c in eligible:
        if is_restricted_base(c):
            continue
        bi = base_index.get(c)
        if not bi:
            continue
        tname, ridx = bi
        _p, _h, rows_b, _col_code, col_type, col_type2 = base_tables[tname]
        br = rows_b[ridx]
        t1 = (br.get(col_type) or "").strip().lower() if col_type else ""
        t2 = (br.get(col_type2) or "").strip().lower() if col_type2 else ""
        if is_r200_blocked_base(c, t1, t2):
            continue
        if t1 in banned_misc_types or t2 in banned_misc_types:
            continue
        eligible2.append(c)
    eligible = eligible2
    removed_unsafe = before_unsafe - len(eligible)
    if removed_unsafe:
        report.append(f"[tc-enrichment] filtered {removed_unsafe} classic-unsafe base code(s) by type (banned={sorted(banned_misc_types)})")

    # Deterministic shuffle
    rng = random.Random(20260221)
    eligible_sorted = sorted(eligible)
    rng.shuffle(eligible_sorted)

    # Inject into high-level TCs only, empty slots only.
    # Branch canon uses flat/no-focus enrichment. Older R200 experiments front-loaded
    # uar/uap/etc. with higher probability, which made Sacred Armor visibly overrepresented
    # compared with normal-tier bases like Ancient Armor. Keep everything schema-safe but
    # avoid any hand-picked focus boost.
    MIN_LEVEL = 70
    MAX_PER_TC = 2
    PROB = "1"      # conservative equal weight comparable to existing high-level probs
    PROB_FOCUS = "1"

    if CANON_TC_ENRICHMENT_FLAT_NO_FOCUS:
        focus = []
        stream = eligible_sorted[:]
    else:
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
        report.append(f"[tc-enrichment] SAFE injected {injected} base entries into {tcs_touched} high-level TCs (level>={MIN_LEVEL}, empty slots only, prob={PROB}).")
        if CANON_TC_ENRICHMENT_FLAT_NO_FOCUS:
            report.append("[tc-enrichment] FLAT NO-FOCUS: no hand-picked focus bases; all injected base entries use equal prob=1")
        else:
            report.append(f"[tc-enrichment] Focus: {','.join(focus) if focus else '(none)'}")
        preview = ",".join(stream[:40]) + ("..." if len(stream)>40 else "")
        report.append(f"[tc-enrichment] Stream preview: {preview}")
    else:
        report.append("[tc-enrichment] No empty slots found on eligible TCs; no changes made.")

# === End restored Stage-4 systems ===

def apply_no_low_quality_items(mod_root, report, enabled: bool):
    """Stage 0 (optional): Disable low-quality (cracked/crude/damaged) item drops.

    Implementation: patch data/global/excel/itemratio.txt so Normal is forced to 1 and NormalDivisor to 1024
    (and NormalMin if present). Since low quality is the fallback when all other quality checks fail,
    forcing the Normal check to effectively always succeed ensures the engine falls back to Normal instead of Low Quality (including cases where monster level is below base qlvl).

    This is table-only and save-safe.
    """
    if not enabled:
        report.append("[stage0-itemratio] Disabled (flag off); skipped")
        return

    p = mod_root / "data/global/excel/itemratio.txt"
    if not p.exists():
        report.append("[stage0-itemratio] Missing itemratio.txt; skipped")
        return

    import csv

    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        report.append("[stage0-itemratio] itemratio.txt empty; skipped")
        return

    try:
        rows = list(csv.reader(lines, delimiter="\t"))
        header = rows[0]
        # Find columns (D2R typically has Normal + NormalDivisor; some schemas also have NormalMin)
        def col(name: str):
            name = name.strip().lower()
            for i, h in enumerate(header):
                if h.strip().lower() == name:
                    return i
            return None

        c_normal = col("Normal")
        c_normaldiv = col("NormalDivisor")
        c_normalmin = col("NormalMin")

        if c_normal is None or c_normaldiv is None:
            report.append("[stage0-itemratio] itemratio missing Normal/NormalDivisor columns; skipped")
            return

        patched = 0
        for r in rows[1:]:
            if len(r) < len(header):
                r.extend([""] * (len(header) - len(r)))
            if not r or not r[0].strip():
                continue
            changed = False
            if r[c_normal] != "1":
                r[c_normal] = "1"
                changed = True
            if r[c_normaldiv] != "1024":
                r[c_normaldiv] = "1024"
                changed = True
            if c_normalmin is not None and r[c_normalmin] != "1":
                r[c_normalmin] = "1"
                changed = True
            if changed:
                patched += 1

        out = ["\t".join(r) for r in rows]
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        report.append(f"[stage0-itemratio] NOLOWQUALITY_V4 NormalDivisor=1024 patched_rows={patched}")
    except Exception as e:
        report.append(f"[stage0-itemratio] ERROR while patching itemratio: {e}")
        # Do not raise; keep patcher stable.
        return


def apply_cow_always_drop(mod_root: Path, report: list[str], enabled: bool) -> None:
    """Stage-4 cow tweak: ensure each Hell Bovine (and optionally Cow King) kill yields at least one TC pick.

    Implementation (Approach B):
      - Reads monstats.txt to find the TreasureClass rows used by 'hellbovine' and 'cowking' (all difficulties).
      - Patches TreasureClassEx.txt for those TC names: set NoDrop=0; ensure Picks>=1.
    This is intentionally narrow to avoid affecting other monsters/TCs.
    """
    if not enabled:
        report.append("[stage4-cow] Disabled (flag off); skipped")
        return

    excel = mod_root / "data/global/excel"
    p_ms = excel / "monstats.txt"
    p_tc = excel / "TreasureClassEx.txt"
    if not p_ms.exists() or not p_tc.exists():
        report.append("[stage4-cow] Missing monstats.txt or TreasureClassEx.txt; skipped")
        return

    th_ms, ms_rows, ms_nl = read_tsv(p_ms)
    # Identify columns
    def _norm(s: str) -> str:
        return (s or "").strip().lstrip("\ufeff").lower().replace(" ", "")
    col_id = next((c for c in th_ms if _norm(c) in ("id","name","monstats","monstat")), None)
    # In D2R tables, it is typically 'Id'
    if col_id is None:
        col_id = next((c for c in th_ms if _norm(c) == "id"), None)

    tc_cols = [c for c in th_ms if _norm(c).startswith("treasureclass")]
    if col_id is None or not tc_cols:
        report.append("[stage4-cow] Could not locate Id/TreasureClass columns in monstats.txt; skipped")
        return

    targets = {"hellbovine", "cowking"}
    tc_names = set()
    for r in ms_rows:
        mid = (r.get(col_id) or "").strip().lower()
        if mid in targets:
            for c in tc_cols:
                v = (r.get(c) or "").strip()
                if v:
                    tc_names.add(v)

    if not tc_names:
        report.append("[stage4-cow] No cow TC names discovered from monstats (hellbovine/cowking); skipped")
        return

    th_tc, tc_rows, tc_nl = read_tsv(p_tc)
    col_tc = next((c for c in th_tc if _norm(c) in ("treasureclass","treasureclass1","treasureclassname")), None)
    if col_tc is None:
        # In D2R it's usually 'Treasure Class'
        col_tc = next((c for c in th_tc if "treasure" in _norm(c) and "class" in _norm(c)), None)
    col_nodrop = next((c for c in th_tc if _norm(c) == "nodrop"), None)
    col_picks = next((c for c in th_tc if _norm(c) == "picks"), None)

    if col_tc is None or col_nodrop is None:
        report.append("[stage4-cow] Could not locate TreasureClass/NoDrop columns in TreasureClassEx.txt; skipped")
        return

    patched = 0
    for r in tc_rows:
        name = (r.get(col_tc) or "").strip()
        if name and name in tc_names:
            old_nd = (r.get(col_nodrop) or "").strip()
            if old_nd != "0":
                r[col_nodrop] = "0"
            # Ensure picks >= 1 if the column exists
            if col_picks is not None:
                try:
                    p = int((r.get(col_picks) or "0").strip() or "0")
                except Exception:
                    p = 0
                if p <= 0:
                    r[col_picks] = "1"
            patched += 1
            report.append(f"[stage4-cow] force drop: TC={name} NoDrop {old_nd or '(blank)'}->0" + (f" Picks={r.get(col_picks)}" if col_picks else ""))

    if patched == 0:
        report.append("[stage4-cow] No matching TC rows found to patch (unexpected); skipped")
        return

    write_tsv(p_tc, th_tc, tc_rows, newline=tc_nl)
    report.append(f"[stage4-cow] Patched {patched} cow TreasureClassEx row(s) (NoDrop=0)")

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
        vanilla_ids = [(r.get("*ID") or "").strip() for r in vrows]
        mod_ids = [(r.get("*ID") or "").strip() for r in mrows]

        if vanilla_ids != mod_ids:
            examples = []
            for i, (v_id, m_id) in enumerate(zip(vanilla_ids, mod_ids), start=1):
                if v_id != m_id:
                    examples.append((i, v_id, m_id))
                    if len(examples) >= 10:
                        break
            raise RuntimeError(
                "PATCHER ASSERTION FAILED: uniqueitems.txt *ID row order drift detected "
                f"(examples row,vanilla,mod={examples})."
            )

        # Uniqueness gate applies to non-empty IDs only (vanilla includes marker rows with empty *ID)
        nn = [x for x in mod_ids if x != ""]
        if len(set(nn)) != len(nn):
            # report first few duplicates for diagnostics
            seen = {}
            dups = []
            for i, x in enumerate(mod_ids, start=1):
                if not x:
                    continue
                if x in seen:
                    dups.append((x, seen[x], i))
                    if len(dups) >= 10:
                        break
                else:
                    seen[x] = i
            raise RuntimeError(f"PATCHER ASSERTION FAILED: uniqueitems.txt duplicate *ID detected (examples={dups}).")


    report.append("[uniqueitems-guard] OK: header/rowcount/*ID uniqueness/order match vanilla.")
    return True


def assert_no_lod_ports_when_disabled(vanilla_root: Path, mod_root: Path, report: list[str]) -> None:
    """
    Guardrail: when Expansion-drops-in-Classic is disabled (or stage=0), ensure we are not silently
    porting LoD uniques into Classic by flipping uniqueitems.txt version gates from vanilla LoD (100) to Classic (0/blank).

    This catches accidental always-on remaps baked into static_mod or other unconditional patches.
    """
    v_path = vanilla_root / "data" / "global" / "excel" / "uniqueitems.txt"
    o_path = mod_root / "data" / "global" / "excel" / "uniqueitems.txt"

    if not v_path.exists():
        report.append(f"[guard] vanilla uniqueitems.txt missing at {v_path} (skipped)")
        return
    if not o_path.exists():
        report.append(f"[guard] output uniqueitems.txt missing at {o_path} (skipped)")
        return

    v_h, v_rows, _ = read_tsv(v_path)
    o_h, o_rows, _ = read_tsv(o_path)

    def norm(k: str) -> str:
        return (k or "").strip().lstrip("\ufeff").lower().replace(" ", "")

    def find_col(header, name):
        want = norm(name)
        for k in header:
            if norm(k) == want:
                return k
        return None

    key_col_v = find_col(v_h, "index") or find_col(v_h, "Id") or find_col(v_h, "id")
    key_col_o = find_col(o_h, "index") or find_col(o_h, "Id") or find_col(o_h, "id")
    ver_col_v = find_col(v_h, "version")
    ver_col_o = find_col(o_h, "version")

    if not (key_col_v and key_col_o and ver_col_v and ver_col_o):
        report.append("[guard] uniqueitems.txt missing required columns (index/version); skipped")
        return

    v_map = {}
    for r in v_rows:
        k = (r.get(key_col_v) or "").strip()
        if k:
            v_map[k.lower()] = r

    violations = []
    for r in o_rows:
        k = (r.get(key_col_o) or "").strip()
        if not k:
            continue
        vr = v_map.get(k.lower())
        if not vr:
            continue
        v_ver = (vr.get(ver_col_v) or "").strip()
        o_ver = (r.get(ver_col_o) or "").strip()

        # If vanilla is LoD-gated (100), output must remain LoD-gated unless exp-drops is enabled.
        if v_ver == "100" and (o_ver == "" or o_ver == "0"):
            violations.append(k)

    if violations:
        sample = ", ".join(violations[:20])
        more = "" if len(violations) <= 20 else f" (+{len(violations)-20} more)"
        raise SystemExit(
            "[guard] Detected silent LoD->Classic port(s) in uniqueitems.txt while exp-drops disabled/stage=0. "
            f"Rows with vanilla version=100 but output version=0/blank: {sample}{more}\n"
            "Fix: remove always-on remaps from static_mod (uniqueitems.txt) and rely on ENABLE_EXPANSION_DROPS_IN_CLASSIC + EXP_DROPS_STAGE."
        )
    report.append("[guard] OK: no silent LoD->Classic version flips in uniqueitems.txt (exp-drops disabled/stage=0)")


def apply_classic_unique_port_layer(mod_root: Path, report: list[str], strict: bool=False) -> None:
    """Classic port layer: Port (forge-enable) *all* uniques into Classic, canonically, IN PLACE.

    Rules:
      - uniqueitems.txt: keep canonical base 'code' (no remaps), set version=0, enabled=1.
      - Enable corresponding base items (armor/weapons/misc) for Classic: version=0 and spawnable=1 when present.
      - Skip Assassin- and Druid-class restricted bases/uniques (Classic original classes only).
      - No structural changes: no row add/remove/reorder. uniqueitems-guard must pass.

    If strict=True, missing base codes become a hard error.
    """
    # Stage-1 stable Classic port allowlist (derived from harness results).
    # Only port expansion uniques/sets whose *base item type* is known stable under the harness.
    # Explicitly exclude problematic categories (jave, thro) and non-ports (hamm, club), plus jewels/runes/charms.
    stable_type_codes = STAGE1_STABLE_TYPE_CODES
    excluded_type_codes = STAGE1_EXCLUDED_TYPE_CODES
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

    # Exclude known-problematic/unsupported type groups from the Classic LoD port layer
    # (engine/inventory issues or deliberately excluded from the harness/port plan)
    excluded_type_codes = set(["jave", "thro", "jewl", "rune", "scha", "mcha", "lcha", "gcha"]) 

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

    # Skill tab IDs for LoD-only classes (Druid/Assassin). These are used by the
    # item_addskilltab property (Properties func=10: ItemModsSetTabSkills).
    # D2R Data Guide indicates:
    #   15-17 = Druid (Shape Shifting / Elemental / Summoning)
    #   18-20 = Assassin (Traps / Martial Arts / Shadow Disciplines)
    # We exclude these so LoD-class tab-skill uniques (e.g., Earthshaker's +Elemental)
    # do NOT get ported/enabled in Classic++ until Phase 2.
    excluded_skilltab_ids = {"15", "16", "17", "18", "19", "20", "21", "22"}
    report.append("[classic-port] guard tag: ASSDRU_V2_SKILLTAB")

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
    skipped_unsafe_misc = 0
    skipped_quest = 0
    skipped_superseded = 0
    skipped_bases = set()
    skipped_by_prop = []  # list[(unique_index, token)]
    missing_bases = set()
    touched_base_codes = set()

    # Exclude expansion-only misc categories in Classic (even if a unique exists): jewels, charms, runes.
    banned_misc_types = {"jewl", "jewel", "charm", "rune"}

    for r in rows_u:
        idx = (r.get(col_u_idx) or "").strip()
        code_item = (r.get(col_u_code) or "").strip()
        if not idx or not code_item:
            continue
        if is_r200_superseded_unique(idx, code_item):
            skipped_superseded += 1
            skipped_by_prop.append((idx, "superseded"))
            continue
        if is_r200_superseded_base_code(code_item):
            skipped_superseded += 1
            skipped_by_prop.append((idx, "superseded"))
            continue
        if is_r200_quest_restricted_base(code_item):
            skipped_quest += 1
            skipped_by_prop.append((idx, "quest"))
            continue

        # Base lookup + stable allowlist gate (prevents Classic-unsafe ports).
        base = base_index.get(code_item)
        if not base:
            missing_bases.add(code_item)
            continue

        # base_index stores (filename, row_index) into one of our base tables
        base_fname, base_ridx = base
        p_base, h_base, rows_base, col_base_code, col_base_version, col_base_spawn, col_base_type, col_base_type2 = base_tables[base_fname]
        base_row = rows_base[base_ridx]
        base_type = ((base_row.get(col_base_type) or "").strip().lower())

        # Option B: quest-bound or otherwise restricted types should never be enabled in Classic port layer
        if base_type in excluded_type_codes:
            continue

        # Enforce harness allowlist (stable_type_codes)
        if base_type and base_type not in stable_type_codes:
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

        # Third, tab-skill property (propN=item_addskilltab, parN = skilltab id).
        # This is how many uniques encode "+X to <Skill Tree>" bonuses.
        if hit_token is None:
            for n, pc in prop_num_to_col.items():
                tok = (r.get(pc) or "").strip().lower()
                # Guard: block Assassin/Druid-specific uniques on any base
                if tok in {"ass", "dru"}:
                    hit_token = tok
                    break
                if tok not in {"item_addskilltab", "skilltab"}:
                    continue
                parc = par_num_to_col.get(n)
                parv = (r.get(parc) or "").strip() if parc else ""
                if parv in excluded_skilltab_ids:
                    # Map to class for reporting only
                    hit_token = "dru" if parv in {"15", "16", "17", "18"} else "ass"
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

        # Exclude classic-unsafe misc types (jewel/charm/rune) even if present in uniqueitems (e.g., Rainbow Facet).
        fname, ridx = rec
        p_b, h_b, rows_b, col_code_b, col_ver_b, col_spawn_b, col_type_b, col_type2_b = base_tables[fname]
        br_b = rows_b[ridx]
        t1_b = (br_b.get(col_type_b) or "").strip().lower() if col_type_b else ""
        t2_b = (br_b.get(col_type2_b) or "").strip().lower() if col_type2_b else ""
        if is_r200_superseded_unique(idx, code_item):
            skipped_superseded += 1
            skipped_by_prop.append((idx, "superseded"))
            continue
        if is_r200_superseded_base_code(code_item):
            skipped_superseded += 1
            skipped_by_prop.append((idx, "superseded"))
            continue
        if is_r200_quest_restricted_base(code_item, t1_b, t2_b):
            skipped_quest += 1
            skipped_by_prop.append((idx, "quest"))
            continue
        if t1_b in banned_misc_types or t2_b in banned_misc_types:
            skipped_unsafe_misc += 1
            skipped_by_prop.append((idx, t1_b if t1_b in banned_misc_types else t2_b))
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

    report.append(f"[classic-port] enabled/ported uniques (non-ass/dru)={enabled_uniques}, base rows enabled/updated={enabled_bases}, skipped ass={skipped_ass}, skipped dru={skipped_dru}, skipped unsafe misc={skipped_unsafe_misc}, skipped quest={skipped_quest}, skipped superseded={skipped_superseded}")
    if skipped_ass_prop or skipped_dru_prop:
        report.append(f"[classic-port] skipped_by_prop: ass={skipped_ass_prop}, dru={skipped_dru_prop} (class-skill uniques on shared bases)")
        sample = ", ".join([f"{u}({t})" for (u,t) in sorted(skipped_by_prop)[:40]])
        report.append(f"[classic-port] skipped_by_prop(sample): {sample}")
    if skipped_bases:
        report.append(f"[classic-port] skipped_bases_class_locked(sample): {', '.join(sorted(list(skipped_bases))[:25])}")

def apply_stage1_cow_harness(mod_root: Path, vanilla_root: Path, report: list[str], preset: str) -> None:
    """Stage-1 Cow Harness (process-of-elimination)

    Deterministic BASE-CODE pools (matches your verified Sacred Armor / Shako approach) and
    forces ALL cow variants (normal + champ + unique + desecrated) to use the Stage-1 pool.

    Also patches monstats for HellBovine / Cow King to point directly at the Stage-1 TC rows,
    eliminating any ambiguity from duplicate TC names or indirection.

    Presets (base table + type):
      Armor:   TORS, HELM, GLOV, BOOT, BELT, SHLD, HEAD -> armor.txt (type=...)
      Weapons: SWOR, AXE, MACE, WAND, SCEP, STAF, SPEA, JAVE, DAGG, POLE, THRO, BOW, XBOW, ORB -> weapons.txt
      Misc:    RING, AMUL, GEM -> misc.txt (type=...)  (runes/jewels/charms intentionally excluded in Stage1)

    Filters:
      - spawnable==1
      - class==""   (excludes Assassin/Druid class-specific bases)
    """
    preset = (preset or "").strip().upper()
    if not preset:
        report.append("[stage1-cow] Disabled; skipped")
        return

    preset_to_type = {
        # Armor / wearable
        "TORS": "tors",
        "HELM": "helm",
        "GLOV": "glov",
        "BOOT": "boot",
        "BELT": "belt",
        "SHLD": "shie",
        "HEAD": "head",

        # Weapons
        "SWOR": "swor",
        "AXE":  "axe",
        "MACE": "mace",
        "HAMM": "hamm",   # missing previously
        "CLUB": "club",   # missing previously
        "WAND": "wand",
        "SCEP": "scep",
        "STAF": "staf",
        "SPEA": "spea",
        "JAVE": "jave",   # known special-case in Classic; revisit if drops don't resolve
        "DAGG": "knif",   # DAGG == itemtype 'knif' (stable)
        "POLE": "pole",
        "THRO": "thro",
        "BOW":  "bow",
        "XBOW": "xbow",
        "ORB":  "orb",

        # Jewelry / misc
        "RING": "ring",
        "AMUL": "amul",
        "GEM":  "item_gem",    # gems only (we intentionally do NOT include runes/jewels/charms in Stage1)
    }
    want_type = preset_to_type.get(preset, "")
    if not want_type:
        report.append(f"[stage1-cow] Unknown preset '{preset}'; skipped")
        return

    excel_mod = mod_root / "data/global/excel"
    excel_van = vanilla_root / "data/global/excel"
    p_tc   = excel_mod / "treasureclassex.txt"
    p_mon  = excel_mod / "monstats.txt"
    # Primary pool source: vanilla tables (as extracted).
    # Fallback pool source: *mod* tables, filtered to Classic (version==0), so Stage-1
    # always has a sane Classic-only pool even when the expansion/port layer has no
    # matching candidates for a given type (e.g. some LoD-only unique base coverage).
    p_arm  = excel_van / "armor.txt"
    p_weap = excel_van / "weapons.txt"
    p_misc = excel_van / "misc.txt"
    p_arm_mod  = excel_mod / "armor.txt"
    p_weap_mod = excel_mod / "weapons.txt"
    p_misc_mod = excel_mod / "misc.txt"
    if not p_tc.exists():
        report.append("[stage1-cow] Missing treasureclassex.txt; skipped")
        return

    th, tc_rows, _ = read_tsv(p_tc)

    col_name  = find_column_by_name(th, "Treasure Class") or find_column_by_name(th, "TreasureClass")
    col_picks = find_column_by_name(th, "Picks")
    col_nodrop = find_column_by_name(th, "NoDrop")

    item_cols = []
    prob_cols = []
    for i in range(1, 11):
        ic = find_column_by_name(th, f"Item{i}")
        pc = find_column_by_name(th, f"Prob{i}")
        if ic and pc:
            item_cols.append(ic)
            prob_cols.append(pc)

    if not (col_name and col_picks and col_nodrop and len(item_cols) == 10):
        report.append("[stage1-cow] treasureclassex header missing expected columns; skipped")
        return

    def load_pool_from_table(path: Path, want_type: str, *, classic_only: bool = False) -> list[str]:
        if not path.exists():
            return []
        h, rows, _ = read_tsv(path)
        code_col = find_column_by_name(h, "code") or "code"
        type_col = find_column_by_name(h, "type") or "type"
        class_col = find_column_by_name(h, "class") or "class"
        spawn_col = find_column_by_name(h, "spawnable") or "spawnable"
        ver_col = find_column_by_name(h, "version") or "version"

        out_codes = []
        for r in rows:
            if classic_only and ver_col in h:
                v = (r.get(ver_col) or "").strip()
                # Classic bases are version==0; expansion bases are usually 100.
                if v not in ("0", ""):
                    continue
            code = (r.get(code_col) or "").strip()
            if not code:
                continue
            if spawn_col in h and (r.get(spawn_col) or "").strip() != "1":
                continue
            typev = (r.get(type_col) or "").strip().lower()
            if want_type.lower() == "item_gem":
                if not typev.startswith("gem"):
                    continue
            else:
                if typev != want_type.lower():
                    continue
            _cls = (r.get(class_col) or "").strip().lower() if class_col in h else ""
            if _cls in ("ass", "dru"):
                continue
            out_codes.append(code)

        seen=set()
        uniq=[]
        for c in out_codes:
            cl=c.lower()
            if cl in seen:
                continue
            seen.add(cl)
            uniq.append(c)
        return sorted(uniq, key=lambda s: s.lower())

    is_armor = preset in ("TORS","HELM","GLOV","BOOT","BELT","SHLD","HEAD")
    is_weap  = preset in ("SWOR","AXE","MACE","HAMM","CLUB","WAND","SCEP","STAF","SPEA","JAVE","DAGG","POLE","THRO","BOW","XBOW","ORB")

    # Primary pool from vanilla tables
    if is_armor:
        pool = load_pool_from_table(p_arm, want_type)
    elif is_weap:
        pool = load_pool_from_table(p_weap, want_type)
    else:
        pool = load_pool_from_table(p_misc, want_type)

    # Fallback pool from *mod* tables, Classic-only (version==0)
    if not pool:
        if is_armor:
            pool = load_pool_from_table(p_arm_mod, want_type, classic_only=True)
        elif is_weap:
            pool = load_pool_from_table(p_weap_mod, want_type, classic_only=True)
        else:
            pool = load_pool_from_table(p_misc_mod, want_type, classic_only=True)
        if pool:
            report.append(f"[stage1-cow] Preset {preset}: primary pool empty; using Classic-only fallback pool (n={len(pool)})")

    if not pool:
        report.append(f"[stage1-cow] Preset {preset}: no base codes found (after filters + Classic fallback); skipped")
        return

    main_tc = f"zz_stage1_cow_{preset}"

    # Remove any existing stage1 rows for this preset (idempotent rebuild)
    tc_rows = [r for r in tc_rows if not (r.get(col_name) or "").strip().lower().startswith(main_tc.lower())]

    def new_tc_row(name: str) -> dict:
        r = {k:"" for k in th}
        r[col_name]=name
        r[col_picks]="1"
        r[col_nodrop]="0"
        for ic,pc in zip(item_cols, prob_cols):
            r[ic]=""
            r[pc]="0"
        return r

    # chunk pool into sub TCs
    chunks=[pool[i:i+10] for i in range(0,len(pool),10)]
    sub_names=[]
    for idx, ch in enumerate(chunks, start=1):
        sub=f"{main_tc}_{idx:02d}"
        sub_names.append(sub)
        r=new_tc_row(sub)
        for i, code in enumerate(ch):
            r[item_cols[i]]=code
            r[prob_cols[i]]="1"
        tc_rows.append(r)

    main=new_tc_row(main_tc)
    for i, sub in enumerate(sub_names[:10]):
        main[item_cols[i]]=sub
        main[prob_cols[i]]="1"
    tc_rows.append(main)

    # Overwrite ALL matching TC rows (handles duplicates)
    targets = [
        "Cow","Cow (N)","Cow (H)",
        "Act 4 Champ B","Act 4 Unique B",
        "Act 4 (N) Champ B","Act 4 (N) Unique B",
        "Act 4 (H) Champ B","Act 4 (H) Unique B",
        "Act 4 (H) Champ B Desecrated","Act 4 (H) Unique B Desecrated",
        "Act 4 (N) Champ B Desecrated","Act 4 (N) Unique B Desecrated",
    ]

    def overwrite_all(name: str) -> int:
        c=0
        for r in tc_rows:
            if (r.get(col_name) or "").strip().lower() == name.lower():
                r[col_picks]="1"
                r[col_nodrop]="0"
                for ic,pc in zip(item_cols, prob_cols):
                    r[ic]=""
                    r[pc]="0"
                r[item_cols[0]]=main_tc
                r[prob_cols[0]]="1"
                c += 1
        return c

    changed=0
    missing=[]
    for n in targets:
        hits=overwrite_all(n)
        if hits:
            changed += hits
        else:
            missing.append(n)

    # Create missing core cow rows (single row each)
    for n in ["Cow","Cow (N)","Cow (H)"]:
        if n in missing:
            r=new_tc_row(n)
            r[item_cols[0]]=main_tc
            r[prob_cols[0]]="1"
            tc_rows.append(r)
            changed += 1
            missing.remove(n)

    write_tsv(p_tc, th, tc_rows)

    # Patch monstats HellBovine + Cow King to point directly at our stage TCs for all variants
    mon_changed = 0
    if p_mon.exists():
        mh, mrows, _ = read_tsv(p_mon)
        id_col = find_column_by_name(mh, "Id") or "Id"
        tc_cols = [
            "TreasureClass","TreasureClassChamp","TreasureClassUnique",
            "TreasureClassDesecrated","TreasureClassDesecratedChamp","TreasureClassDesecratedUnique",
            "TreasureClass(N)","TreasureClassChamp(N)","TreasureClassUnique(N)",
            "TreasureClassDesecrated(N)","TreasureClassDesecratedChamp(N)","TreasureClassDesecratedUnique(N)",
            "TreasureClass(H)","TreasureClassChamp(H)","TreasureClassUnique(H)",
            "TreasureClassDesecrated(H)","TreasureClassDesecratedChamp(H)","TreasureClassDesecratedUnique(H)",
        ]
        # only keep cols that exist
        tc_cols = [c for c in tc_cols if find_column_by_name(mh, c)]
        for r in mrows:
            rid = (r.get(id_col) or "").strip().lower()
            if rid in ("hellbovine","cowking"):
                # normal/champ/unique/desecrated all point at main_tc for the relevant difficulty columns
                # We set every tc col that exists to main_tc so every cow variant uses our pool.
                for c in tc_cols:
                    r[c] = main_tc
                mon_changed += 1
        if mon_changed:
            write_tsv(p_mon, mh, mrows)

    report.append(f"[stage1-cow] Enabled preset={preset} pool={len(pool)} chunks={len(chunks)} tc_overwrite_hits={changed} missing_TCs={len(missing)} monstats_patched={mon_changed}")


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


def purge_static_excel_txt(static_root: Path, report: list[str]) -> None:
    """Remove any stray gameplay .txt under static_mod/.../data/global/excel created by prior runs."""
    if not static_root.exists():
        return
    try:
        mod_subroot = find_mod_subroot(static_root)
    except Exception:
        return
    excel = static_root / mod_subroot / "data" / "global" / "excel"
    if not excel.exists():
        return
    offenders = [p for p in excel.glob("*.txt") if p.is_file()]
    if not offenders:
        return
    for p in offenders:
        try:
            p.unlink()
        except Exception:
            pass
    report.append(f"[guard] purged {len(offenders)} stray excel .txt from static_mod")

def guard_no_gameplay_txt_in_static_mod(static_root: Path) -> None:
    """Fail build if static_mod contains gameplay .txt under data/global/excel."""
    if not static_root.exists():
        return
    mod_subroot = find_mod_subroot(static_root)
    excel = static_root / mod_subroot / "data" / "global" / "excel"
    if not excel.exists():
        return
    offenders = [p for p in excel.glob("*.txt") if p.is_file()]
    if offenders:
        names = ", ".join(p.name for p in offenders[:20])
        more = "" if len(offenders) <= 20 else f" (+{len(offenders)-20} more)"
        raise SystemExit(
            "[guard] static_mod contains gameplay .txt under data/global/excel (forbidden). "
            f"Offenders: {names}{more}"
        )


# === Stage 5: LoD-style large stash for Classic (generator, vanilla-derived) ===

def _load_relaxed_json(path: Path) -> dict:
    """Load JSON with tolerance for trailing commas."""
    s = path.read_text(encoding="utf-8", errors="strict")
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return json.loads(s)

def _find_child(children: list[dict], name: str) -> dict | None:
    for c in children:
        if c.get("name") == name:
            return c
    return None

def apply_stage5_stash_lodish(mod_root: Path, vanilla_root: Path, report: list[str]) -> None:
    """
    Stage 5 (additive): port LoD stash geometry into Classic safely.

    - Generates standalone Classic bankoriginal layouts from vanilla bankexpansion deltas (normal + HD)
    - Patches inventory.txt bank backing store so stash grid size matches UI (Bank Page 1 -> Big Bank Page 1)
    """
    # ---- Layout JSON merge ----
    v_layout_dir = vanilla_root / "data" / "global" / "ui" / "layouts"
    src_pairs = [
        ("bankoriginallayout.json", "bankexpansionlayout.json"),
        ("bankoriginallayouthd.json", "bankexpansionlayouthd.json"),
    ]

    out_layout_dir = mod_root / "data" / "global" / "ui" / "layouts"
    out_layout_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for orig_name, exp_name in src_pairs:
        orig_path = v_layout_dir / orig_name
        exp_path = v_layout_dir / exp_name
        if not orig_path.exists() or not exp_path.exists():
            report.append(f"[stage5-stash] SKIP: missing vanilla layout(s): {orig_name if not orig_path.exists() else ''} {exp_name if not exp_path.exists() else ''}".strip())
            continue

        orig = _load_relaxed_json(orig_path)
        exp = _load_relaxed_json(exp_path)

        # Make standalone
        orig.pop("basedOn", None)

        children = list(orig.get("children", []))

        # Background filename from expansion
        bg_exp = _find_child(exp.get("children", []), "background")
        if bg_exp and "fields" in bg_exp and "filename" in bg_exp["fields"]:
            for i, c in enumerate(children):
                if c.get("name") == "background":
                    cc = dict(c)
                    fields = dict(cc.get("fields", {}))
                    fields["filename"] = bg_exp["fields"]["filename"]
                    cc["fields"] = fields
                    children[i] = cc
                    break

        # Grid rect + cellCount from expansion
        grid_exp = _find_child(exp.get("children", []), "grid")
        if grid_exp and "fields" in grid_exp:
            expf = grid_exp["fields"]
        else:
            expf = {}

        # Compute delta between original grid rect and expansion grid rect.
        # We use this to keep certain UI widgets (gold/deposit/withdraw) aligned
        # relative to the grid when porting expansion geometry.
        def _rect_xy(rect: dict):
            if not isinstance(rect, dict):
                return None
            for kx, ky in (("x", "y"), ("left", "top")):
                if kx in rect and ky in rect:
                    try:
                        return int(float(rect[kx])), int(float(rect[ky]))
                    except Exception:
                        return None
            return None

        def _shift_rect(rect: dict, dx: int, dy: int) -> dict:
            if not isinstance(rect, dict) or (dx == 0 and dy == 0):
                return rect
            r = dict(rect)
            if "x" in r and "y" in r:
                r["x"] = str(int(float(r["x"])) + dx)
                r["y"] = str(int(float(r["y"])) + dy)
            elif "left" in r and "top" in r:
                r["left"] = str(int(float(r["left"])) + dx)
                r["top"] = str(int(float(r["top"])) + dy)
            return r

        orig_grid_rect = None
        for c in children:
            if c.get("name") == "grid":
                f0 = c.get("fields", {})
                orig_grid_rect = f0.get("rect")
                break
        exp_grid_rect = expf.get("rect") if isinstance(expf, dict) else None
        dxy = None
        if orig_grid_rect is not None and exp_grid_rect is not None:
            o = _rect_xy(orig_grid_rect)
            e = _rect_xy(exp_grid_rect)
            if o and e:
                dxy = (e[0] - o[0], e[1] - o[1])

        for i, c in enumerate(children):
            if c.get("name") == "grid":
                cc = dict(c)
                fields = dict(cc.get("fields", {}))
                if "rect" in expf:
                    fields["rect"] = expf["rect"]
                if "cellCount" in expf:
                    fields["cellCount"] = expf["cellCount"]
                else:
                    fields["cellCount"] = {"x": "10", "y": "10"}
                cc["fields"] = fields
                children[i] = cc
                break

        # Nudge rects to expansion-friendly positions (safe no-op if missing)
        def _set_rect(child_name: str, rect: dict):
            for j, c0 in enumerate(children):
                if c0.get("name") == child_name:
                    cc = dict(c0)
                    f = dict(cc.get("fields", {}))
                    f["rect"] = rect
                    cc["fields"] = f
                    children[j] = cc
                    return

        # Keep explicit gold UI copying only for the SD/legacy-facing layout.
        # HD/remastered should remain unpinned so the base layout keeps it flush.
        if orig_name == "bankoriginallayout.json":
            gold_like = {"gold_max", "gold_amount", "gold_withdraw", "gold_deposit"}
            exp_children = {str(c.get("name", "")): c for c in exp.get("children", [])}
            gold_copied = 0
            for j, c0 in enumerate(children):
                name = str(c0.get("name", ""))
                if name not in gold_like:
                    continue
                src = exp_children.get(name)
                if not src:
                    continue
                cc = dict(c0)
                srcf = dict(src.get("fields", {}))
                dstf = dict(cc.get("fields", {}))
                if "rect" in srcf:
                    dstf["rect"] = srcf["rect"]
                    cc["fields"] = dstf
                    children[j] = cc
                    gold_copied += 1
            if gold_copied:
                report.append(f"[stage5-stash] gold-ui-sd: copied expansion positions for {gold_copied} widget(s) in {orig_name}")

        # Safe initial implementation: no tabs
        children = [c for c in children if c.get("name") not in ("BankTabs", "PreviousSeasonToggleDisplay", "PreviousLadderSeasonBankTabs")]
        orig["children"] = children

        out_path = out_layout_dir / orig_name
        out_path.write_text(json.dumps(orig, indent=4), encoding="utf-8")
        generated += 1

    # ---- Inventory bank backing store patch ----
    out_inv = mod_root / "data" / "global" / "excel" / "inventory.txt"
    if out_inv.exists():
        header, rows, newline = read_tsv(out_inv)
        idx = {r.get("class", ""): r for r in rows}
        src = idx.get("Big Bank Page 1")
        dst = idx.get("Bank Page 1")
        if src and dst:
            for k in header:
                if k == "class":
                    continue
                dst[k] = src.get(k, dst.get(k, ""))
            write_tsv(out_inv, header, rows, newline=newline)
            report.append("[stage5-stash] inventory.txt: patched 'Bank Page 1' to match 'Big Bank Page 1' (10x10)")
        else:
            report.append("[stage5-stash] inventory.txt: SKIP (missing Bank Page 1 or Big Bank Page 1)")
    else:
        report.append("[stage5-stash] inventory.txt: SKIP (output inventory.txt not found)")

    
    # ---- Controller layouts (HD-only in some vanilla dumps) ----
    controller_generated = 0
    v_ctl_dir = vanilla_root / "data" / "global" / "ui" / "layouts" / "controller"
    if v_ctl_dir.exists():
        ctl_pairs = [
            ("bankoriginallayouthd.json", "bankexpansionlayouthd.json"),
            # non-HD controller layouts often don't exist; only generate if present
            ("bankoriginallayout.json", "bankexpansionlayout.json"),
        ]
        out_ctl_dir = out_layout_dir / "controller"
        out_ctl_dir.mkdir(parents=True, exist_ok=True)

        for orig_name, exp_name in ctl_pairs:
            orig_path = v_ctl_dir / orig_name
            exp_path = v_ctl_dir / exp_name
            if not orig_path.exists() or not exp_path.exists():
                continue

            orig = _load_relaxed_json(orig_path)
            exp = _load_relaxed_json(exp_path)

            # Standalone (no inheritance)
            orig.pop("basedOn", None)

            children = list(orig.get("children", []))

            # Background filename from expansion if present
            bg_exp = _find_child(exp.get("children", []), "background")
            for i, c in enumerate(children):
                if c.get("name") == "background":
                    cc = dict(c)
                    fields = dict(cc.get("fields", {}))
                    if bg_exp and "fields" in bg_exp and "filename" in bg_exp["fields"]:
                        fields["filename"] = bg_exp["fields"]["filename"]
                    cc["fields"] = fields
                    children[i] = cc
                    break

            # Grid: rect + 10x10 cellCount from expansion
            grid_exp = _find_child(exp.get("children", []), "grid")
            for i, c in enumerate(children):
                if c.get("name") == "grid":
                    cc = dict(c)
                    fields = dict(cc.get("fields", {}))
                    if grid_exp and "fields" in grid_exp:
                        expf = grid_exp["fields"]
                        if "rect" in expf:
                            fields["rect"] = expf["rect"]
                        if "cellCount" in expf:
                            fields["cellCount"] = expf["cellCount"]
                    # ensure 10x10 at minimum
                    fields.setdefault("cellCount", {"x": 10, "y": 10})
                    fields.setdefault("cellSize", "$ItemCellSize")
                    cc["fields"] = fields
                    children[i] = cc
                    break

            # Remove tab widgets for stability (Stage 5/5.1 policy)
            children = [c for c in children if c.get("name") not in ("BankTabs", "PreviousSeasonToggleDisplay", "PreviousLadderSeasonBankTabs")]
            orig["children"] = children

            (out_ctl_dir / orig_name).write_text(json.dumps(orig, indent=4), encoding="utf-8", newline="\n")
            controller_generated += 1

        if controller_generated > 0:
            report.append(f"[stage5-stash-controller] APPLIED: generated controller bank layouts (files={controller_generated})")
        else:
            # Not an error: many vanilla dumps ship controller stash as HD-only, or not at all in extracted trees.
            report.append("[stage5-stash-controller] SKIP: no controller bank layout pairs present in vanilla")
    else:
        report.append("[stage5-stash-controller] SKIP: vanilla layouts/controller folder not present")

    report.append(f"[stage5-stash] APPLIED: generated bankoriginallayout(.hd).json from vanilla bankexpansion deltas (files={generated}, controller_files={controller_generated})")


def patch_skills_flatten_holy_aura_damage(mod_root: Path, report: list[str]) -> None:
    """
    Flat-max the elemental aura pulse damage for Holy Fire / Holy Freeze / Holy Shock
    by copying EMax progression into EMin progression.
    """
    p = mod_root / "data" / "global" / "excel" / "skills.txt"
    if not p.exists():
        report.append("[skills-flat-holy] SKIP: skills.txt missing")
        return

    h, rows, nl = read_tsv(p)
    skill_col = find_column_by_name(h, "skill") or "skill"
    targets = {"Holy Fire", "Holy Freeze", "Holy Shock"}

    pairs = [
        ("EMin", "EMax"),
        ("EMinLev1", "EMaxLev1"),
        ("EMinLev2", "EMaxLev2"),
        ("EMinLev3", "EMaxLev3"),
        ("EMinLev4", "EMaxLev4"),
        ("EMinLev5", "EMaxLev5"),
    ]
    pairs = [(a, b) for (a, b) in pairs if a in h and b in h]

    changed_rows = 0
    changed_cells = 0
    for r in rows:
        name = (r.get(skill_col) or "").strip()
        if name not in targets:
            continue
        row_changed = False
        for min_col, max_col in pairs:
            max_val = (r.get(max_col) or "")
            if max_val != "" and (r.get(min_col) or "") != max_val:
                r[min_col] = max_val
                changed_cells += 1
                row_changed = True
        if row_changed:
            changed_rows += 1

    if changed_rows:
        write_tsv(p, h, rows, nl)

    report.append(f"[skills-flat-holy] APPLIED: rows_changed={changed_rows} cells_changed={changed_cells} targets=Holy Fire,Holy Freeze,Holy Shock")


def patch_skills_flatten_holy_aura_actual_damage(mod_root: Path, report: list[str]) -> None:
    """
    Flat-max the passive added weapon damage for Holy Fire / Holy Freeze / Holy Shock
    by copying passive max stat/calc into passive min stat/calc.
    """
    p = mod_root / "data" / "global" / "excel" / "skills.txt"
    if not p.exists():
        report.append("[skills-flat-holy-actual] SKIP: skills.txt missing")
        return

    h, rows, nl = read_tsv(p)
    skill_col = find_column_by_name(h, "skill") or "skill"
    targets = {"Holy Fire", "Holy Freeze", "Holy Shock"}

    needed = {"passivestat1", "passivestat2", "passivecalc1", "passivecalc2"}
    if not needed.issubset(set(h)):
        report.append("[skills-flat-holy-actual] SKIP: passive columns missing")
        return

    changed_rows = 0
    changed_cells = 0
    for r in rows:
        name = (r.get(skill_col) or "").strip()
        if name not in targets:
            continue
        row_changed = False
        pstat2 = (r.get("passivestat2") or "")
        pcalc2 = (r.get("passivecalc2") or "")

        if pstat2 != "" and (r.get("passivestat1") or "") != pstat2:
            r["passivestat1"] = pstat2
            changed_cells += 1
            row_changed = True
        if pcalc2 != "" and (r.get("passivecalc1") or "") != pcalc2:
            r["passivecalc1"] = pcalc2
            changed_cells += 1
            row_changed = True

        if row_changed:
            changed_rows += 1

    if changed_rows:
        write_tsv(p, h, rows, nl)

    report.append(f"[skills-flat-holy-actual] APPLIED: rows_changed={changed_rows} cells_changed={changed_cells} targets=Holy Fire,Holy Freeze,Holy Shock")


def patch_skilldesc_holy_aura_direct_single_value(mod_root: Path, report: list[str]) -> None:
    """
    Holy aura tooltip cleanup:
    - one visible numeric damage line only
    - true-max display formula: exma*((100+par6)/100)
    - duplicate numeric line removed
    """
    p = mod_root / "data" / "global" / "excel" / "skilldesc.txt"
    if not p.exists():
        report.append("[skilldesc-holy-direct] SKIP: skilldesc.txt missing")
        return

    h, rows, nl = read_tsv(p)
    skilldesc_col = find_column_by_name(h, "skilldesc") or "skilldesc"

    changed_rows = 0
    changed_cells = 0

    def setv(r, col, val):
        nonlocal changed_cells
        if col in h and (r.get(col) or "") != val:
            r[col] = val
            changed_cells += 1
            return True
        return False

    max_formula = "exma*((100+par6)/100)"

    for r in rows:
        name = (r.get(skilldesc_col) or "").strip().lower()
        if name not in {"holy fire", "holy freeze", "holy shock"}:
            continue

        row_changed = False
        row_changed |= setv(r, "descdam", "")
        row_changed |= setv(r, "ddam calc1", "")
        row_changed |= setv(r, "ddam calc2", "")

        for col in ("desctextb2","desctextb3","desctextb4","desccalcb2","desccalcb3","desccalcb4"):
            row_changed |= setv(r, col, "")

        if name == "holy fire":
            row_changed |= setv(r, "descline2", "74")
            row_changed |= setv(r, "desctexta2", "StrSkillDamageFlat")
            row_changed |= setv(r, "desccalca2", max_formula)

            for col in ("descline3","desctexta3","desctextb3","desccalca3","desccalcb3"):
                row_changed |= setv(r, col, "")

        elif name == "holy freeze":
            row_changed |= setv(r, "descline3", "74")
            row_changed |= setv(r, "desctexta3", "StrSkillDamageFlat")
            row_changed |= setv(r, "desccalca3", max_formula)

            for col in ("descline4","desctexta4","desctextb4","desccalca4","desccalcb4"):
                row_changed |= setv(r, col, "")

        elif name == "holy shock":
            row_changed |= setv(r, "descline2", "74")
            row_changed |= setv(r, "desctexta2", "StrSkillDamageFlat")
            row_changed |= setv(r, "desccalca2", max_formula)

            for col in ("descline3","desctexta3","desctextb3","desccalca3","desccalcb3"):
                row_changed |= setv(r, col, "")

        if row_changed:
            changed_rows += 1

    if changed_rows:
        write_tsv(p, h, rows, nl)

    report.append(f"[skilldesc-holy-direct] APPLIED: rows_changed={changed_rows} cells_changed={changed_cells} targets=holy fire,holy freeze,holy shock mode=single_true_max_no_dupe")

def main():
    # Zero-flag canon runner.
    # Run this file directly:
    #     python patcher.py
    #
    # Expected layout beside patcher.py:
    #     vanilla/
    #     static_mod/
    #     patch_sources/
    #
    # Output:
    #     output/
    #
    # These values mirror the previous canonical batch defaults.
    args = argparse.Namespace(
        vanilla=str(SCRIPT_DIR / "vanilla"),
        out=str(SCRIPT_DIR / "output"),
        patch_sources=str(SCRIPT_DIR / "patch_sources"),
        cow_all_bases=CANON_COWALLBASES,
        cow_all_bases_full=(CANON_COWALLBASES and CANON_COWCHAOS),
        enable_ui=CANON_UITOGGLE,
        no_low_quality=CANON_NOLOWQUALITY,
        cow_always_drop=CANON_COWALWAYSDROP,
        enable_expansion_drops_in_classic=CANON_ENABLE_EXPANSION_DROPS_IN_CLASSIC,
        stash_lodish=CANON_LODSTASH,
    )

    # Force the canonical stage internally so no external flags/env are needed.
    os.environ["EXP_DROPS_STAGE"] = str(CANON_EXP_DROPS_STAGE)
    # Canonicalize vanilla root path for Stage-1 harness pool reads
    vanilla_root = Path(args.vanilla).resolve()
    report = []
    _validate_stage1_type_lists(report)
    # --- Stage-1 Cow Harness (env toggles) ---
    stage1_preset = ""
    _stage1_keys = [
        # Armor / wearable
        "TORS", "HELM", "GLOV", "BOOT", "BELT", "SHLD", "HEAD",
        # Weapons
        "SWOR", "AXE", "MACE", "HAMM", "CLUB", "WAND", "SCEP", "STAF", "SPEA", "JAVE", "DAGG", "POLE", "THRO", "BOW", "XBOW", "ORB",
        # Jewelry / misc (no runes/jewels/charms in Stage1)
        "RING", "AMUL", "GEM",
    ]
    # Canon run keeps Stage-1 harness disabled unless the constant above is changed.
    _stage1_on = [CANON_STAGE1_PRESET] if CANON_STAGE1_PRESET else []
    if len(_stage1_on) == 1:
        stage1_preset = _stage1_on[0]
        report.append(f"[stage1-cow] env preset={stage1_preset}")
    elif len(_stage1_on) > 1:
        raise SystemExit(f"Stage-1 Cow Harness: set EXACTLY ONE STAGE1_* env toggle to 1 (got {', '.join(_stage1_on)})")

    # --- Expansion Drops in Classic: staged enablement ladder ---
    # Canon stage 4 means:
    #   stage 1 -> LoD port layer
    #   stage 2 -> stage 1 + cow all-bases farm
    #   stage 3 -> stage 2 + TC enrichment
    #   stage 4 -> stage 3 + chaos/full cow mode
    exp_stage = 0
    exp_port = False
    exp_cowfarm = False
    exp_tce = False
    exp_chaos = False
    if args.enable_expansion_drops_in_classic:
        try:
            exp_stage = int((os.environ.get("EXP_DROPS_STAGE", "2") or "2").strip() or "2")
        except Exception:
            exp_stage = 2
        exp_stage = max(0, min(4, exp_stage))
        exp_port = (exp_stage >= 1)
        exp_cowfarm = (exp_stage >= 2)
        exp_tce = (exp_stage >= 3)
        exp_chaos = (exp_stage >= 4)
        report.append(f"[exp-drops] stage={exp_stage} (port={int(exp_port)} cowfarm={int(exp_cowfarm)} tce={int(exp_tce)} chaos={int(exp_chaos)})")

    script_dir = Path(__file__).resolve().parent
    static_root = script_dir / "static_mod"

    # Guardrail: static_mod may contain assets, but gameplay Excel .txt files must be generated
    # from the current vanilla dump. Purge stale contamination first, then hard-fail if any remain.
    purge_static_excel_txt(static_root, report)
    guard_no_gameplay_txt_in_static_mod(static_root)

    report.append(
        "[canon-profile] ENABLE_EXPANSION_DROPS_IN_CLASSIC=1 "
        f"COWALLBASES={int(args.cow_all_bases)} "
        f"COWCHAOS={int(args.cow_all_bases_full)} "
        f"EXP_DROPS_STAGE={exp_stage} "
        f"UITOGGLE={int(args.enable_ui)} "
        f"LODSTASH={int(args.stash_lodish)} "
        f"COWALWAYSDROP={int(args.cow_always_drop)} "
        f"NOLOWQUALITY={int(args.no_low_quality)} "
        f"COW_ALLBASES_SEED={CANON_COW_ALLBASES_SEED} "
        f"COW_ALLBASES_POOL_SIZE={CANON_COW_ALLBASES_POOL_SIZE} "
        f"COW_ALLBASES_WRAP_PROB={CANON_COW_ALLBASES_WRAP_PROB} "
        f"COW_DIRECT_POOL={int(CANON_COW_DIRECT_POOL)} "
        f"COWKING_NATIVE={int(CANON_PRESERVE_COW_KING_NATIVE_DROPS)} "
        f"COW_FLAT_MIXED_POOL={int(CANON_COW_FLAT_MIXED_POOL)} "
        f"TC_ENRICH_FLAT_NO_FOCUS={int(CANON_TC_ENRICHMENT_FLAT_NO_FOCUS)} "
        f"QUEST_FILTER={int(CANON_FILTER_QUEST_BASES)} "
        f"PREFER_LOD_AZUREWRATH={int(CANON_PREFER_LOD_AZUREWRATH)} "
        f"JAVE_STACKLESS_SPEAR={int(CANON_ENABLE_JAVE_STACKLESS_SPEAR_BRANCH)} "
        f"AMAZON_SPECIFIC={int(CANON_ENABLE_AMAZON_SPECIFIC_BRANCH)} "
        f"AMAZON_SPECIFIC_HARNESS={int(CANON_ENABLE_AMAZON_SPECIFIC_STABILITY_HARNESS)}"
    )

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

    # report initialized earlier
    # 1) Copy full static mod tree (mods/<modname>/<modname>.mpq/... including modinfo.json)
    mod_subroot = find_mod_subroot(static_root)
    copy_static_payload(static_root, out, mod_subroot, report)

    # Resolve the mod root inside output
    mod_root = out / mod_subroot
    mod_root.mkdir(parents=True, exist_ok=True)

    # Restore UITOGGLE support from the old batch profile. With UITOGGLE=0, files are copied
    # as disable* names so they remain available without becoming active.
    copy_ui_overrides(mod_root, patch_sources, report, enable_ui=getattr(args, 'enable_ui', False))

    # 2) Seed patched txt targets from vanilla (source of truth) into the mod tree
    #    We copy the entire vanilla excel folder to avoid schema drift and keep new content.
    v_excel = vanilla / "data" / "global" / "excel"
    o_excel = mod_root / "data" / "global" / "excel"
    o_excel.mkdir(parents=True, exist_ok=True)

    # Copy all vanilla excel .txt into mod tree (overwrite any static versions)
    for p in v_excel.glob("*.txt"):
        shutil.copy2(p, o_excel / p.name)
    report.append(f"[vanilla] seeded excel txt from {v_excel} into {o_excel}")

    patch_prefer_lod_azurewrath(mod_root, report)

    patch_charstats_from_reference(mod_root, patch_sources, report)

    apply_qol_baseline(mod_root, patch_sources, report)
    patch_skills_flatten_holy_aura_damage(mod_root, report)
    patch_skills_flatten_holy_aura_actual_damage(mod_root, report)
    patch_skilldesc_holy_aura_direct_single_value(mod_root, report)

    # Stage 0 optional: disable low quality item drops (cracked/crude/damaged) by forcing Normal success in itemratio.
    try:
        apply_no_low_quality_items(mod_root, report, enabled=getattr(args, 'no_low_quality', False))
    except Exception as e:
        report.append(f"[stage0-itemratio] ERROR: {e}")

    # 3) Apply locked patches to the mod root (vanilla schema already seeded)
    patch_monstats_cow_xp_boost(mod_root, report, mult=COW_XP_MULTIPLIER)

    # Stage 4 optional: force cows to always drop at least one item.
    # Keep this extremely defensive: if a user's vanilla dump has unexpected TC naming/columns,
    # we log and continue rather than crashing the patcher.
    try:
        apply_cow_always_drop(mod_root, report, enabled=getattr(args, 'cow_always_drop', False))
    except Exception as e:
        report.append(f"[stage4-cow] ERROR: {type(e).__name__}: {e}")


    # Canon stackless branch: apply v7-proven JAVE/Amazon profiles before the port layer
    # so regular JAVE uniques/bases can be evaluated through the normal Stage4 machinery.
    apply_jave_stackless_spear_forward_branch(mod_root, report)
    apply_amazon_specific_forward_branch(mod_root, report)

    # Classic port layer: Port ALL non-assassin/druid uniques + enable their canonical bases for Classic (forge-only).
    # Expansion Drops in Classic: LoD unique/set port layer (staged)
    if args.enable_expansion_drops_in_classic and exp_port:
        report.append("[exp-drops] applying LoD unique/set port layer")
        apply_classic_unique_port_layer(mod_root, report)
    else:
        report.append("[exp-drops] skipping LoD unique/set port layer")

    # True Stage-4 ladder restored from the old canon batch profile.
    # These run AFTER the LoD port layer so cow/TC pools can see the newly Classic-enabled bases.
    if args.enable_expansion_drops_in_classic and exp_cowfarm:
        apply_cow_all_bases(
            mod_root,
            report,
            enabled=getattr(args, "cow_all_bases", False),
            full_chaos=(getattr(args, "cow_all_bases_full", False) and exp_chaos),
        )
    elif args.enable_expansion_drops_in_classic:
        report.append("[cow-all-bases] Disabled by EXP_DROPS_STAGE/profile; skipped")

    if args.enable_expansion_drops_in_classic and exp_tce:
        apply_tc_enrichment_highlevel_bases(mod_root, report, enabled=True)
    elif args.enable_expansion_drops_in_classic:
        report.append("[tc-enrichment] Disabled by EXP_DROPS_STAGE/profile; skipped")

    apply_amazon_specific_stability_harness(mod_root, report)

    patch_relax_item_requirements(mod_root, report)
    apply_all_item_rolls_max(mod_root, report)


    apply_remove_unique_level_requirements(mod_root, report)

    apply_remove_set_level_requirements(mod_root, report)


    validate_uniqueitems_invariants(mod_root, report)

    # Guardrail: if exp-drops is disabled (or stage=0), ensure no LoD->Classic version remaps slipped in.
    if not (args.enable_expansion_drops_in_classic and exp_port):
        assert_no_lod_ports_when_disabled(vanilla_root, mod_root, report)

    apply_stage1_cow_harness(mod_root, vanilla_root, report, stage1_preset)

    # 4) Write run log
    if getattr(args, "stash_lodish", False):
        apply_stage5_stash_lodish(mod_root, vanilla_root, report)

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

# === End helpers ===

if __name__ == "__main__":
    main()
