import csv
import html
import io
import json
import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Hashable, cast, final, override

import pandas as pd
from pandas.core.frame import DataFrame

from scripts.config import GameConfig
from scripts.constants import AbilityType, IdPrefix

_PUNCT_TRANSLATION: dict[int, str] = str.maketrans(
    {
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
    }
)


class _HTMLToPlainText(HTMLParser):
    # Collect visible text; treat <br> as newlines (pipe chars in text are untouched).

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self._parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def plain_text(self) -> str:
        return "".join(self._parts)


def _to_ascii_plaintext(non_ascii_text: str) -> str:
    # Fold to 7-bit ASCII: compatibility punctuation, strip accents, drop remaining non-ASCII.
    normalized_text: str = unicodedata.normalize("NFKD", non_ascii_text)
    fixed_text: str = "".join(
        ch for ch in normalized_text if not unicodedata.combining(ch)
    )
    return fixed_text.encode(encoding="ascii", errors="ignore").decode(encoding="ascii")


def clean_wahapedia_text(value: str) -> str:
    # Strip Wahapedia HTML to ASCII plaintext (no curly quotes or other Unicode punctuation).
    if isinstance(value, float) and pd.isna(value):
        return ""
    if not value:
        return value

    unescaped_s: str = html.unescape(s=value)
    # Normalize line breaks from tags before feeding the parser (handles odd casing / spacing).
    sub_s: str = re.sub(r"<br\s*/?>", "\n", unescaped_s, flags=re.IGNORECASE)
    first_parser: _HTMLToPlainText = _HTMLToPlainText()
    second_parser: _HTMLToPlainText = _HTMLToPlainText()
    useSecondParser: bool = False
    try:
        first_parser.feed(data=sub_s)
        first_parser.close()
    except Exception:
        useSecondParser = True
        s_fallback: str = re.sub(r"<[^>]+>", "", sub_s)
        second_parser.feed(data=html.unescape(s=s_fallback))
        second_parser.close()
    parser_plain_text: str = (
        first_parser.plain_text() if not useSecondParser else second_parser.plain_text()
    )

    normalized_text: str = unicodedata.normalize("NFKC", parser_plain_text).translate(
        _PUNCT_TRANSLATION
    )
    ascii_plain_text: str = _to_ascii_plaintext(non_ascii_text=normalized_text)
    replaced_text: str = ascii_plain_text.replace("\r\n", "\n").replace("\r", "\n")
    newline_sub_text: str = re.sub(r"[ \t]+\n", "\n", replaced_text)
    tab_sub_text: str = re.sub(r"\n[ \t]+", "\n", newline_sub_text)
    finalized_text: str = re.sub(r"\n{3,}", "\n\n", tab_sub_text)
    return finalized_text.strip()


def _scrub_dataframe_cells(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out: pd.DataFrame = df.copy()
    for col in out.columns:
        out[col] = out[col].map(clean_wahapedia_text)
    return out


def _infer_expected_pipe_count(lines: list[str]) -> int:
    # Expected number of '|' delimiters per complete data row.
    # Wahapedia pipe exports use a trailing '|' on every line, including the header, so
    # the raw header and body normally share the same delimiter count. If you strip the
    # trailing delimiter from the header only (e.g. when loading into DuckDB), body rows
    # still have one more '|' than that shortened header—we treat counts in {header, header+1}
    # as valid shape hints.
    # Only lines that end with '|' and fall in that set are used, so short tail fragments
    # of a split row are ignored.
    header_delims: int = lines[0].count("|")
    allowed: set[int] = {header_delims, header_delims + 1}
    candidates: list[int] = []
    for line in lines[1:]:
        if not line.rstrip().endswith("|"):
            continue
        n: int = line.count("|")
        if n in allowed:
            candidates.append(n)
    if candidates:
        return max(candidates)
    return header_delims


def repair_wahapedia_pipe_csv(raw: str) -> str:
    # Join physical lines that belong to one logical pipe-delimited row.
    # Wahapedia sometimes inserts a stray newline inside a field (e.g. long Stratagem
    # descriptions). Exports use a trailing '|' on every line; complete logical rows end
    # with '|' and match the file's usual delimiter count (see `_infer_expected_pipe_count`).
    lines: list[str] = raw.splitlines()
    if len(lines) < 2:
        return raw

    expected: int = _infer_expected_pipe_count(lines)
    out_lines: list[str] = [lines[0]]
    i = 1
    while i < len(lines):
        buf: str = lines[i]
        while i + 1 < len(lines):
            if buf.count("|") > expected:
                break
            if buf.rstrip().endswith("|") and buf.count("|") >= expected:
                break
            i += 1
            buf += lines[i]
        out_lines.append(buf)
        i += 1

    trailing_nl: bool = raw.endswith("\n") or raw.endswith("\r\n")
    fixed: str = "\n".join(out_lines)
    if trailing_nl and not fixed.endswith("\n"):
        fixed += "\n"
    return fixed


@final
class Wahapedia40kProcessor:
    def __init__(self, game: GameConfig, temp_dir: Path, data_dir: Path) -> None:
        self._game: GameConfig = game
        self._input_dir: Path = temp_dir / self._game.folder_name
        self._output_dir: Path = data_dir / self._game.folder_name
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_filename: str = "manifest.json"
        self.manifest: dict[str, str] = self._load_manifest()

    def _load_manifest(self) -> dict[str, str]:
        manifest_output: Path = self._output_dir / self._manifest_filename
        if manifest_output.exists():
            try:
                with open(
                    file=self._output_dir / self._manifest_filename, mode="r"
                ) as f:
                    return cast(dict[str, str], json.load(fp=f))
            except Exception as e:
                print(f"    [ERROR] Manifest Load Failed: {e}")
        print("    [OK] Created New Manifest")
        return {"wahapedia_version": "0000-00-00 00:00:00", "last_sync_run": ""}

    def save_manifest(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["last_sync_run"] = datetime.now().strftime(
            format="%Y-%m-%d %H:%M:%S"
        )
        self._save_json(json_entries=self.manifest, filename="manifest.json")
        print(f"    [OK] manifest.json ({len(self.manifest)})")

    def process_last_update(self) -> str:
        last_update_file: Path = self._input_dir / "Last_update.csv"
        if last_update_file.exists():
            with open(file=last_update_file, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter="|")
                _an: list[str] = next(reader)
                row: list[str] = next(reader)
                print("    [OK] Last_update.csv loaded")
                return row[0].strip()
        return ""

    def _load_file(self, filename: str) -> pd.DataFrame:
        path: Path = self._input_dir / filename
        if not path.exists():
            print(f"    [ERROR] {filename} not found.")
            return pd.DataFrame()
        try:
            raw_text: str = path.read_text(encoding="utf-8")
            repaired_text: str = repair_wahapedia_pipe_csv(raw=raw_text)

            initial_df: pd.DataFrame = pd.read_csv(
                io.StringIO(initial_value=repaired_text),
                sep="|",
                encoding="utf-8",
                dtype=str,
            )
            scrubbed_df: pd.DataFrame = _scrub_dataframe_cells(initial_df)
            print(f"        [OK] {filename} loaded.")
            return scrubbed_df
        except Exception as e:
            print(f"        [ERROR] Error loading {filename}: {e}")
            return pd.DataFrame()

    def _save_json(
        self, json_entries: list[dict[Hashable, str]] | dict[str, str], filename: str
    ) -> None:
        output_path: Path = self._output_dir / filename
        with open(file=output_path, mode="w", encoding="utf-8") as f:
            json.dump(obj=json_entries, fp=f, indent=2)

    def _handle_series_list(self, series: list[str]) -> list[str]:
        return series if series and len(series) > 0 else []

    def _handle_series_to_dict(self, series: pd.Series[str]) -> dict[Hashable, str]:
        return series.to_dict()

    def _handle_get_ui_category(self, series: pd.Series[str | list[str]]) -> str:
        return self._get_ui_category(
            role=cast(str, series["role"]), keywords=cast(list[str], series["keywords"])
        )

    def _process_factions(self) -> None:
        print("\n    BUILDING factions.json")
        try:
            faction_df: pd.DataFrame = self._load_file(filename="Factions.csv")
            if faction_df.empty:
                print("    [WARNING] Factions.csv is missing or empty. Skipping.")
                return

            # Abilities grouped per faction
            abilities_df: pd.DataFrame = self._load_file(filename="Abilities.csv")
            if abilities_df.empty:
                grouped_abilities_df: pd.Series = pd.Series(dtype=object)
            else:
                # Treat blank-like faction ids as 'no faction'
                faction_mask: pd.Series[bool] = ~abilities_df["faction_id"].isin(
                    values=["", "0", "nan", "None"]
                )
                grouped_abilities_df = (
                    abilities_df.loc[faction_mask]
                    .assign(id=lambda df: IdPrefix.ABILITY + df["id"])
                    .groupby("faction_id")["id"]
                    .agg(list)
                )

            # Units grouped per faction and ui_category
            keywords_df: pd.DataFrame = self._load_file(
                filename="Datasheets_keywords.csv"
            )
            units_df: pd.DataFrame = self._load_file(filename="Datasheets.csv")

            if keywords_df.empty or units_df.empty:
                grouped_units_df: pd.Series = pd.Series(dtype=object)
            else:
                kw_lookup: dict[Hashable, str] = (
                    keywords_df.groupby("datasheet_id")["keyword"]
                    .apply(func=list)
                    .to_dict()
                )

                units_df = units_df.copy()
                units_df["keywords"] = (
                    units_df["id"].map(kw_lookup).apply(self._handle_series_list)
                )
                units_df["ui_category"] = units_df.apply(
                    self._handle_get_ui_category,
                    axis=1,
                )

                # build {ui_category: [unit_ids]} per faction_id
                units_df["unit_id"] = IdPrefix.UNIT + units_df["id"]
                grouped_units_df = (
                    units_df.groupby(["faction_id", "ui_category"])["unit_id"]
                    .apply(func=list)
                    .unstack(fill_value="")
                    .apply(self._handle_series_to_dict, axis=1)
                )

            processed_detachment_df: pd.DataFrame = self._process_detachments()
            if processed_detachment_df.empty:
                grouped_detachments_df: pd.Series = pd.Series(dtype=object)
            else:
                grouped_detachments_df = processed_detachment_df.groupby(
                    "faction_id"
                ).apply(lambda df: df.to_dict("records"))

            processed_factions_df: pd.DataFrame = pd.DataFrame()
            processed_factions_df["id"] = IdPrefix.FACTION + faction_df["id"]
            processed_factions_df["name"] = faction_df["name"]
            processed_factions_df["abilities"] = (
                faction_df["id"]
                .map(grouped_abilities_df)
                .apply(self._handle_series_list)
            )
            processed_factions_df["detachments"] = (
                faction_df["id"]
                .map(grouped_detachments_df)
                .apply(self._handle_series_list)
            )
            processed_factions_df["units"] = (
                faction_df["id"].map(grouped_units_df).apply(self._handle_series_list)
            )
            records: list[dict[Hashable, str]] = processed_factions_df.to_dict(
                "records"
            )
            self._save_json(json_entries=records, filename="factions.json")
            print(f"    [OK] factions.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] factions.json: {e}")

    def _process_abilities(self) -> None:
        print("\n    BUILDING abilities.json")
        try:
            processed_ability_dfs: list[pd.DataFrame] = []
            # --- 1. Process Core/Faction Abilities ---
            abilities_df: pd.DataFrame = self._load_file(filename="Abilities.csv")
            if abilities_df.empty:
                print("    [WARNING] Abilities.csv is missing or empty. Skipping.")
            else:
                unique_abilities_df: pd.DataFrame = abilities_df.drop_duplicates(
                    subset=["id"]
                )
                processed_ability_df: pd.DataFrame = unique_abilities_df[
                    ["id", "name", "description", "faction_id"]
                ].copy()
                processed_ability_df["id"] = (
                    IdPrefix.ABILITY + processed_ability_df["id"]
                )

                # Wahapedia uses blank-like strings to indicate 'global' (no faction)
                blank_like_faction_ids: set[str] = {"", "0", "nan", "None"}
                has_faction: pd.Series[bool] = ~processed_ability_df["faction_id"].isin(
                    values=blank_like_faction_ids
                )
                processed_ability_df["type"] = has_faction.map(
                    lambda v: AbilityType.FACTION if v else AbilityType.GLOBAL
                )

                processed_ability_df = processed_ability_df.drop(columns=["faction_id"])
                processed_ability_df = processed_ability_df[
                    ["id", "name", "description", "type"]
                ]
                processed_ability_dfs.append(processed_ability_df)

            data_abilities_df: pd.DataFrame = self._load_file(
                filename="Datasheets_abilities.csv"
            )
            if data_abilities_df.empty:
                print(
                    "    [WARNING] Datasheets_abilities.csv is missing or empty. Skipping."
                )
            else:
                blank_like_ability_ids: set[str] = {"", "0", "nan", "None"}
                ability_id_series: pd.Series = data_abilities_df["ability_id"].fillna(
                    value=""
                )
                custom_mask: pd.Series[bool] = ability_id_series.isin(
                    values=blank_like_ability_ids
                )

                custom_df: pd.DataFrame = data_abilities_df.loc[
                    custom_mask, ["name", "description"]
                ]
                unique_ds_abilities_df: pd.DataFrame = custom_df.drop_duplicates(
                    subset=["name", "description"]
                )

                if not unique_ds_abilities_df.empty:
                    start_id = 100000001
                    count: int = len(unique_ds_abilities_df)
                    ids: list[str] = [
                        f"{IdPrefix.ABILITY}{i}"
                        for i in range(start_id, start_id + count)
                    ]
                    processed_data_abilities_df: pd.DataFrame = pd.DataFrame(
                        {
                            # Keep column order consistent with the previous implementation
                            "id": ids,
                            "type": AbilityType.UNIT,
                            "name": unique_ds_abilities_df["name"].values,
                            "description": unique_ds_abilities_df["description"].values,
                        }
                    )[["id", "type", "name", "description"]]
                    processed_ability_dfs.append(processed_data_abilities_df)

            if processed_ability_dfs and len(processed_ability_dfs) > 0:
                final_abilities_df: pd.DataFrame = pd.concat(
                    processed_ability_dfs, ignore_index=True
                )
                records: list[dict[Hashable, str]] = final_abilities_df.to_dict(
                    orient="records"
                )
                self._save_json(json_entries=records, filename="abilities.json")
                print(f"    [OK] abilities.json ({len(records)})")
            else:
                print(
                    "    [ERROR] Could not load any abilities. Unable to generate abilities.json"
                )
        except Exception as e:
            print(f"    [ERROR] abilities.json: {e}")

    def _process_detachments(self) -> pd.DataFrame:
        print("\n    BUILDING Detachments List")
        try:
            detachments_df: pd.DataFrame = self._load_file(filename="Detachments.csv")
            detachment_abilities_df: pd.DataFrame = self._load_file(
                filename="Detachment_abilities.csv"
            )
            enhancements_df: pd.DataFrame = self._load_file(filename="Enhancements.csv")
            stratagems_df: pd.DataFrame = self._load_file(filename="Stratagems.csv")

            if detachments_df.empty:
                print("    [WARNING] Detachments.csv is missing or empty. Skipping.")
                return pd.DataFrame()
            # Group attached data by detachment
            if detachment_abilities_df.empty:
                grouped_abilities: pd.Series = pd.Series(dtype=object)
            else:
                grouped_abilities = detachment_abilities_df.groupby(
                    "detachment_id"
                ).apply(lambda x: x[["name", "description"]].to_dict("records"))

            if stratagems_df.empty:
                grouped_strats: pd.Series = pd.Series(dtype=object)
            else:
                grouped_strats = stratagems_df.groupby("detachment_id").apply(
                    lambda df: df[
                        ["name", "type", "cp_cost", "turn", "phase", "description"]
                    ].to_dict("records")
                )

            if enhancements_df.empty:
                grouped_enhancements: pd.Series = pd.Series(dtype=object)
            else:
                grouped_enhancements = enhancements_df.groupby("detachment_id")[
                    "id"
                ].apply(
                    func=lambda x: [
                        f"{IdPrefix.ENHANCEMENT}{i}" for i in cast(pd.Series[str], x)
                    ]
                )

            processed_detachments_df: pd.DataFrame = pd.DataFrame()
            processed_detachments_df["faction_id"] = detachments_df["faction_id"]
            processed_detachments_df["id"] = IdPrefix.DETACHMENT + detachments_df["id"]
            processed_detachments_df["name"] = detachments_df["name"]
            processed_detachments_df["type"] = detachments_df["type"]

            processed_detachments_df["abilities"] = (
                detachments_df["id"]
                .map(grouped_abilities)
                .apply(self._handle_series_list)
            )
            processed_detachments_df["stratagems"] = (
                detachments_df["id"].map(grouped_strats).apply(self._handle_series_list)
            )
            processed_detachments_df["enhancements"] = (
                detachments_df["id"]
                .map(grouped_enhancements)
                .apply(self._handle_series_list)
            )
            return processed_detachments_df
        except Exception as e:
            print(f"    [ERROR] Detachment List: {e}")
            return pd.DataFrame()

    def _process_core_stratagems(self) -> None:
        print("\n    BUILDING core_stratagems.json")
        try:
            core_stratagems_df: pd.DataFrame = self._load_file(
                filename="Stratagems.csv"
            )
            if core_stratagems_df.empty:
                print("    [WARNING] Stratagems.csv is missing or empty. Skipping.")
                return
            no_detachment_mask: pd.Series[bool] = core_stratagems_df[
                "detachment_id"
            ].isin(values=["", "0", "nan", "None"])

            type_mask: pd.Series[bool] = (
                core_stratagems_df["type"]
                .fillna(value="")
                .str.contains(pat="Core -", case=False)
            )

            only_core_stratagems_df: pd.DataFrame = core_stratagems_df[
                no_detachment_mask & type_mask
            ].copy()
            if only_core_stratagems_df.empty:
                print(
                    "    [WARNING] No core stratagems found (all have detachment_id). Skipping."
                )
                return

            processed_core_stratagems_df: pd.DataFrame = only_core_stratagems_df[
                ["id", "name", "type", "cp_cost", "turn", "phase", "description"]
            ].copy()
            processed_core_stratagems_df["id"] = (
                IdPrefix.STRATAGEM + processed_core_stratagems_df["id"]
            )

            records: list[dict[Hashable, str]] = processed_core_stratagems_df.to_dict(
                orient="records"
            )
            self._save_json(json_entries=records, filename="core_stratagems.json")
            print(f"    [OK] core_stratagems.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] core_stratagems.json: {e}")

    def _process_enhancements(self) -> None:
        print("\n    BUILDING enhancements.json")
        try:
            enhancements_df: pd.DataFrame = self._load_file(filename="Enhancements.csv")
            if enhancements_df.empty:
                print("    [WARNING] Enhancements.csv is missing or empty. Skipping.")
                return
            processed_enhancements_df: pd.DataFrame = enhancements_df[
                ["id", "name", "cost", "description"]
            ].copy()
            processed_enhancements_df["id"] = (
                IdPrefix.ENHANCEMENT + processed_enhancements_df["id"]
            )

            records: list[dict[Hashable, str]] = processed_enhancements_df.to_dict(
                "records"
            )
            self._save_json(json_entries=records, filename="enhancements.json")
            print(f"    [OK] enhancements.json ({len(records)})")
        except Exception as e:
            print(f"    [ERROR] enhancements.json: {e}")

    def _process_keywords(self) -> pd.DataFrame:
        print("\n    BUILDING keywords.json")
        try:
            keywords_df: pd.DataFrame = self._load_file(
                filename="Datasheets_keywords.csv"
            )
            if keywords_df.empty:
                print(
                    "    [WARNING] Datasheets_keywords.csv is missing or empty. Skipping."
                )
                return pd.DataFrame()

            unique_keywords_df: pd.DataFrame = keywords_df.drop_duplicates(
                subset=["keyword"]
            )
            if unique_keywords_df.empty:
                print(
                    "    [ERROR] Could not find unique keywords. Unable to generate keywords.json"
                )
                return pd.DataFrame()

            start_id = 200000001
            count: int = len(unique_keywords_df)
            ids: list[str] = [
                f"{IdPrefix.KEYWORD}{i}" for i in range(start_id, start_id + count)
            ]

            processed_keywords_df: pd.DataFrame = pd.DataFrame(
                {
                    "id": ids,
                    "name": unique_keywords_df["keyword"].values,
                    "is_faction": unique_keywords_df["is_faction_keyword"].values,
                }
            )[["id", "name", "is_faction"]]

            records: list[dict[Hashable, str]] = processed_keywords_df.to_dict(
                "records"
            )
            self._save_json(json_entries=records, filename="keywords.json")
            print(f"    [OK] keywords.json ({len(records)})")
            return processed_keywords_df
        except Exception as e:
            print(f"    [ERROR] keywords.json: {e}")
            return pd.DataFrame()

    def _process_datasheets(self) -> None:
        print("\n   Building units.json")
        try:
            unit_df: DataFrame = pd.DataFrame()
            datasheets_df = self._load_file(filename="Datasheets.csv")

            unit_df["id"] = IdPrefix.UNIT + datasheets_df["id"]
            unit_df["name"] = datasheets_df["name"]
            unit_df["transport"] = datasheets_df["transport"]
            unit_df["damaged_w"] = datasheets_df["damaged_w"]
            unit_df["damaged_description"] = datasheets_df["damaged_description"]

            keywords_df: DataFrame = self._process_keywords()
        except Exception as e:
            print(f"    [ERROR] units.json: {e}")

    def _get_ui_category(self, role: str, keywords: list[str]) -> str:
        if role == "Characters" and "Epic Hero" in keywords:
            return "Epic Hero"
        elif role == "Characters":
            return "Character"
        elif role == "Battleline":
            return "Battleline"
        elif role == "Dedicated Transports":
            return "Dedicated Transport"
        elif role == "Fortifications":
            return "Fortification"
        elif role == "Other":
            other_types: list[str] = ["Monster", "Vehicle", "Mounted", "Infantry"]
            for keyword in keywords:
                if keyword in other_types:
                    return keyword
        return "Other"

    def process_files(self) -> None:
        self._process_factions()
        self._process_abilities()
        self._process_core_stratagems()
        self._process_enhancements()
        self._process_datasheets()
