import csv
import html
import io
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, cast
from datetime import datetime
import pandas as pd
import json
from pathlib import Path
from scripts import GameConfig
from scripts.constants import IdPrefix, AbilityType


_PUNCT_TRANSLATION = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u00a0": " ",
    "\u2026": "...",
    "\u2022": "*",
    "\u00b7": "-",
    "\u00d7": "x",
    "\u00f7": "/",
})


class _HTMLToPlainText(HTMLParser):
    """Collect visible text; treat <br> as newlines (pipe chars in text are untouched)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def plain_text(self) -> str:
        return "".join(self._parts)


def _to_ascii_plaintext(s: str) -> str:
    """Fold to 7-bit ASCII: compatibility punctuation, strip accents, drop remaining non-ASCII."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.encode("ascii", "ignore").decode("ascii")


def clean_wahapedia_text(value: Any) -> str:
    """Strip Wahapedia HTML to ASCII plaintext (no curly quotes or other Unicode punctuation)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return value

    s = html.unescape(value)
    # Normalize line breaks from tags before feeding the parser (handles odd casing / spacing).
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    parser = _HTMLToPlainText()
    try:
        parser.feed(s)
        parser.close()
    except Exception:
        parser = _HTMLToPlainText()
        s_fallback = re.sub(r"<[^>]+>", "", s)
        parser.feed(html.unescape(s_fallback))
        parser.close()
    text = parser.plain_text()

    text = unicodedata.normalize("NFKC", text).translate(_PUNCT_TRANSLATION)
    text = _to_ascii_plaintext(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _scrub_dataframe_cells(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(clean_wahapedia_text)
    return out


def _infer_expected_pipe_count(lines: list[str]) -> int:
    """
    Expected number of '|' delimiters per complete data row.

    Wahapedia pipe exports use a trailing '|' on every line, including the header, so
    the raw header and body normally share the same delimiter count. If you strip the
    trailing delimiter from the header only (e.g. when loading into DuckDB), body rows
    still have one more '|' than that shortened header—we treat counts in {header, header+1}
    as valid shape hints.

    Only lines that end with '|' and fall in that set are used, so short tail fragments
    of a split row are ignored.
    """
    header_delims = lines[0].count("|")
    allowed = {header_delims, header_delims + 1}
    candidates: list[int] = []
    for line in lines[1:]:
        if not line.rstrip().endswith("|"):
            continue
        n = line.count("|")
        if n in allowed:
            candidates.append(n)
    if candidates:
        return max(candidates)
    return header_delims


def repair_wahapedia_pipe_csv(raw: str) -> str:
    """
    Join physical lines that belong to one logical pipe-delimited row.

    Wahapedia sometimes inserts a stray newline inside a field (e.g. long Stratagem
    descriptions). Exports use a trailing '|' on every line; complete logical rows end
    with '|' and match the file's usual delimiter count (see `_infer_expected_pipe_count`).
    """
    lines = raw.splitlines()
    if len(lines) < 2:
        return raw

    expected = _infer_expected_pipe_count(lines)
    out_lines: list[str] = [lines[0]]
    i = 1
    while i < len(lines):
        buf = lines[i]
        while i + 1 < len(lines):
            if buf.count("|") > expected:
                break
            if buf.rstrip().endswith("|") and buf.count("|") >= expected:
                break
            i += 1
            buf += lines[i]
        out_lines.append(buf)
        i += 1

    trailing_nl = raw.endswith("\n") or raw.endswith("\r\n")
    fixed = "\n".join(out_lines)
    if trailing_nl and not fixed.endswith("\n"):
        fixed += "\n"
    return fixed


class Wahapedia40kProcessor:
    def __init__(self, game: GameConfig, temp_dir: Path, data_dir: Path):
        self._game = game
        self._input_dir = temp_dir / self._game.folder_name
        self._output_dir = data_dir / self._game.folder_name
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._output_dir / "manifest.json"
        self.manifest = self._load_manifest()

    def _load_manifest(self):
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"    [ERROR] Manifest Load Failed: {e}")
        print("    [OK] Created New Manifest")
        return {"wahapedia_version": "0000-00-00 00:00:00", "last_sync_run": None}

    def save_manifest(self):
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["last_sync_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self._manifest_path, 'w') as f:
            json.dump(self.manifest, f, indent=4)
        print(f"    [OK] manifest.json ({len(self.manifest)})")

    def process_last_update(self):
        last_update_file = self._input_dir / "Last_update.csv"
        if last_update_file.exists():
            with open(last_update_file, 'r', encoding="utf-8") as f:
                reader = csv.reader(f, delimiter='|')
                next(reader)
                row = next(reader)
                print("    [OK] Last_update.csv loaded")
                return row[0].strip()
        return None

    def _load_file(self, filename: str) -> pd.DataFrame:
        path = self._input_dir / filename
        if not path.exists():
            print(f"    [ERROR] {filename} not found.")
            return pd.DataFrame()
        try:
            raw = path.read_text(encoding="utf-8")
            raw = repair_wahapedia_pipe_csv(raw)
            # noinspection PyArgumentList
            df = pd.read_csv(
                io.StringIO(raw),
                sep='|',
                encoding="utf-8",
                dtype=str
            )
            df = _scrub_dataframe_cells(df)
            print(f"        [OK] {filename} loaded.")
            return cast(pd.DataFrame, df)
        except Exception as e:
            print(f"        [ERROR] Error loading {filename}: {e}")
            return pd.DataFrame()

    def _process_factions(self):
        print("\n    BUILDING factions.json")
        try:
            faction_df = self._load_file("Factions.csv")
            if faction_df.empty:
                print("    [WARNING] Factions.csv is missing or empty. Skipping.")
                return

            # --- Abilities grouped per faction ---
            abilities_df = self._load_file("Abilities.csv")
            if abilities_df.empty:
                grouped_abilities_df = pd.Series(dtype=object)
            else:
                # treat blank-like faction ids as "no faction"
                faction_mask = ~abilities_df['faction_id'].isin(['', '0', 'nan', 'None'])
                grouped_abilities_df = (
                    abilities_df.loc[faction_mask]
                    .assign(id=lambda df: IdPrefix.ABILITY + df['id'])
                    .groupby('faction_id')['id']
                    .agg(list)
                )

            # --- Units grouped per faction and ui_category ---
            keywords_df = self._load_file("Datasheets_keywords.csv")
            units_df = self._load_file("Datasheets.csv")

            if keywords_df.empty or units_df.empty:
                grouped_units_df = pd.Series(dtype=object)
            else:
                kw_lookup = (
                    keywords_df
                    .groupby('datasheet_id')['keyword']
                    .apply(list)
                    .to_dict()
                )

                units_df = units_df.copy()
                units_df['keywords'] = (
                    units_df['id']
                    .map(kw_lookup)
                    .apply(lambda x: x if isinstance(x, list) else [])
                )
                units_df['ui_category'] = units_df.apply(
                    lambda row: self._get_ui_category(row['role'], row['keywords']),
                    axis=1,
                )

                # build {ui_category: [unit_ids]} per faction_id
                units_df['unit_id'] = IdPrefix.UNIT + units_df['id']
                grouped_units_df = (
                    units_df
                    .groupby(['faction_id', 'ui_category'])['unit_id']
                    .apply(list)
                    .unstack(fill_value=[])
                    .apply(lambda row: row.to_dict(), axis=1)
                )

            processed_detachment_df = self._process_detachments()
            if processed_detachment_df.empty:
                grouped_detachments_df = pd.Series(dtype=object)
            else:
                grouped_detachments_df = (
                    processed_detachment_df
                    .groupby('faction_id')
                    .apply(lambda df: df.to_dict('records'))
                )

            processed_factions_df = pd.DataFrame()
            processed_factions_df['id'] = IdPrefix.FACTION + faction_df['id']
            processed_factions_df['name'] = faction_df['name']
            processed_factions_df['abilities'] = faction_df['id'].map(grouped_abilities_df).apply(
                lambda x: x if isinstance(x, list) else []
            )
            processed_factions_df['detachments'] = faction_df['id'].map(grouped_detachments_df).apply(
                lambda x: x if isinstance(x, list) else []
            )
            processed_factions_df['units'] = faction_df['id'].map(grouped_units_df).apply(
                lambda x: x if isinstance(x, dict) else {}
            )
            records = processed_factions_df.to_dict('records')
            self._save_json(records, "factions.json")
            print(f"    [OK] factions.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] factions.json: {e}")

    def _process_abilities(self):
        print("\n    BUILDING abilities.json")
        try:
            processed_ability_dfs = []
            # --- 1. Process Core/Faction Abilities ---
            abilities_df = self._load_file("Abilities.csv")
            if abilities_df.empty:
                print("    [WARNING] Abilities.csv is missing or empty. Skipping.")
            else:
                unique_abilities_df = abilities_df.drop_duplicates(subset=['id'])
                processed_ability_df = unique_abilities_df[['id', 'name', 'description', 'faction_id']].copy()
                processed_ability_df['id'] = IdPrefix.ABILITY + processed_ability_df['id']

                # Wahapedia uses blank-like strings to indicate "global" (no faction)
                blank_like_faction_ids = {'', '0', 'nan', 'None'}
                has_faction = ~processed_ability_df['faction_id'].isin(blank_like_faction_ids)
                processed_ability_df['type'] = has_faction.map(
                    lambda v: AbilityType.FACTION if v else AbilityType.GLOBAL
                )

                processed_ability_df = processed_ability_df.drop(columns=['faction_id'])
                processed_ability_df = processed_ability_df[['id', 'name', 'description', 'type']]
                processed_ability_dfs.append(processed_ability_df)

            data_abilities_df = self._load_file("Datasheets_abilities.csv")
            if data_abilities_df.empty:
                print("    [WARNING] Datasheets_abilities.csv is missing or empty. Skipping.")
            else:
                blank_like_ability_ids = {'', '0', 'nan', 'None'}
                ability_id_series = data_abilities_df['ability_id'].fillna('')
                custom_mask = ability_id_series.isin(blank_like_ability_ids)

                custom_df = data_abilities_df.loc[custom_mask, ['name', 'description']]
                unique_ds_abilities_df = custom_df.drop_duplicates(subset=['name', 'description'])

                if not unique_ds_abilities_df.empty:
                    start_id = 100000001
                    count = len(unique_ds_abilities_df)
                    ids = [f"{IdPrefix.ABILITY}{i}" for i in range(start_id, start_id + count)]
                    processed_data_abilities_df = pd.DataFrame({
                        # Keep column order consistent with the previous implementation
                        'id': ids,
                        'type': AbilityType.UNIT,
                        'name': unique_ds_abilities_df['name'].values,
                        'description': unique_ds_abilities_df['description'].values,
                    })[['id', 'type', 'name', 'description']]
                    processed_ability_dfs.append(processed_data_abilities_df)

            if processed_ability_dfs and len(processed_ability_dfs) > 0:
                final_abilities_df = pd.concat(processed_ability_dfs, ignore_index=True)
                records = final_abilities_df.to_dict('records')
                self._save_json(records, "abilities.json")
                print(f"    [OK] abilities.json ({len(records)})")
            else:
                print("    [ERROR] Could not load any abilities. Unable to generate abilities.json")
        except Exception as e:
            print(f"    [ERROR] abilities.json: {e}")

    def _process_detachments(self) -> pd.DataFrame:
        print("\n    BUILDING Detachments List")
        try:
            detachments_df = self._load_file("Detachments.csv")
            detachment_abilities_df = self._load_file("Detachment_abilities.csv")
            enhancements_df = self._load_file("Enhancements.csv")
            stratagems_df = self._load_file("Stratagems.csv")

            if detachments_df.empty:
                print("    [WARNING] Detachments.csv is missing or empty. Skipping.")
                return pd.DataFrame()
            # Group attached data by detachment
            if detachment_abilities_df.empty:
                grouped_abilities = pd.Series(dtype=object)
            else:
                grouped_abilities = detachment_abilities_df.groupby('detachment_id').apply(
                    lambda x: x[['name', 'description']].to_dict('records')
                )

            if stratagems_df.empty:
                grouped_strats = pd.Series(dtype=object)
            else:
                grouped_strats = stratagems_df.groupby('detachment_id').apply(
                    lambda df: df[['name', 'type', 'cp_cost', 'turn', 'phase', 'description']].to_dict('records')
                )

            if enhancements_df.empty:
                grouped_enhancements = pd.Series(dtype=object)
            else:
                grouped_enhancements = enhancements_df.groupby('detachment_id')['id'].apply(
                    lambda x: [f"{IdPrefix.ENHANCEMENT}{i}" for i in x]
                )

            processed_detachments_df = pd.DataFrame()
            processed_detachments_df['faction_id'] = detachments_df['faction_id']
            processed_detachments_df['id'] = IdPrefix.DETACHMENT + detachments_df['id']
            processed_detachments_df['name'] = detachments_df['name']
            processed_detachments_df['type'] = detachments_df['type']

            processed_detachments_df['abilities'] = detachments_df['id'].map(grouped_abilities).apply(
                lambda x: x if isinstance(x, list) else []
            )
            processed_detachments_df['stratagems'] = detachments_df['id'].map(grouped_strats).apply(
                lambda x: x if isinstance(x, list) else []
            )
            processed_detachments_df['enhancements'] = detachments_df['id'].map(grouped_enhancements).apply(
                lambda x: x if isinstance(x, list) else []
            )
            return processed_detachments_df
        except Exception as e:
            print(f"    [ERROR] Detachment List: {e}")
            return pd.DataFrame()

    def _process_core_stratagems(self):
        print("\n    BUILDING core_stratagems.json")
        try:
            core_stratagems_df = self._load_file("Stratagems.csv")
            if core_stratagems_df.empty:
                print("    [WARNING] Stratagems.csv is missing or empty. Skipping.")
                return
            no_detachment_mask = (
                core_stratagems_df['detachment_id']
                .isin(['', '0', 'nan', 'None'])
            )

            type_mask = (
                core_stratagems_df['type']
                .fillna('')
                .str.contains('Core -', case=False)
            )

            only_core_stratagems_df = core_stratagems_df[no_detachment_mask & type_mask].copy()
            if only_core_stratagems_df.empty:
                print("    [WARNING] No core stratagems found (all have detachment_id). Skipping.")
                return

            processed_core_stratagems_df = only_core_stratagems_df[
                ['id', 'name', 'type', 'cp_cost', 'turn', 'phase', 'description']
            ].copy()
            processed_core_stratagems_df['id'] = IdPrefix.STRATAGEM + processed_core_stratagems_df['id']

            records = processed_core_stratagems_df.to_dict('records')
            self._save_json(records, "core_stratagems.json")
            print(f"    [OK] core_stratagems.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] core_stratagems.json: {e}")

    def _process_enhancements(self):
        print("\n    BUILDING enhancements.json")
        try:
            enhancements_df = self._load_file("Enhancements.csv")
            if enhancements_df is None or enhancements_df.empty:
                print("    [WARNING] Enhancements.csv is missing or empty. Skipping.")
                return
            processed_enhancements_df = enhancements_df[['id', 'name', 'cost', 'description']].copy()
            processed_enhancements_df['id'] = IdPrefix.ENHANCEMENT + processed_enhancements_df['id']

            records = processed_enhancements_df.to_dict('records')
            self._save_json(records, "enhancements.json")
            print(f"    [OK] enhancements.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] enhancements.json: {e}")

    def _process_keywords(self) -> pd.DataFrame:
        print("\n    BUILDING keywords.json")
        try:
            keywords_df = self._load_file("Datasheets_keywords.csv")
            if keywords_df is None or keywords_df.empty:
                print("    [WARNING] Datasheets_keywords.csv is missing or empty. Skipping.")
                return pd.DataFrame()

            unique_keywords_df = keywords_df.drop_duplicates(subset=['keyword'])
            if unique_keywords_df.empty:
                print("    [ERROR] Could not find unique keywords. Unable to generate keywords.json")
                return pd.DataFrame()

            start_id = 200000001
            count = len(unique_keywords_df)
            ids = [f"{IdPrefix.KEYWORD}{i}" for i in range(start_id, start_id + count)]

            processed_keywords_df = pd.DataFrame({
                'id': ids,
                'name': unique_keywords_df['keyword'].values,
                'is_faction': unique_keywords_df['is_faction_keyword'].values,
            })[['id', 'name', 'is_faction']]

            records = processed_keywords_df.to_dict('records')
            self._save_json(records, "keywords.json")
            print(f"    [OK] keywords.json ({len(records)})")
            return processed_keywords_df
        except Exception as e:
            print(f"    [ERROR] keywords.json: {e}")
            return pd.DataFrame()

    def _save_json(self, json_entries, filename):
        output_path = self._output_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_entries, f, indent=2)
    
    def _get_ui_category(self, role: str, keywords: list[str]) -> str:
        if role == 'Characters' and 'Epic Hero' in keywords:
            return 'Epic Hero'
        elif role == 'Characters':
            return 'Character'
        elif role == 'Battleline':
            return 'Battleline'
        elif role == 'Dedicated Transports':
            return 'Dedicated Transport'
        elif role == 'Fortifications':
            return 'Fortification'
        elif role == 'Other':
            other_types = ['Monster', 'Vehicle', 'Mounted', 'Infantry']
            for keyword in keywords:
                if keyword in other_types:
                    return keyword
        return 'Other'

    def process_files(self) -> None:
        self._process_factions()
        self._process_abilities()
        self._process_core_stratagems()
        self._process_enhancements()