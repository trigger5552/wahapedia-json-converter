import csv
import json
from datetime import datetime
from pathlib import Path
from typing import cast, final
from loguru import logger
import polars as pl

from scripts.config import GameConfig

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

@logger.catch
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

@logger.catch
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

@logger.catch
def scrub_html_from_columns(df: pl.DataFrame, text_columns: list[str]) -> pl.DataFrame:
    valid_cols: list[str] = [c for c in text_columns if c in df.columns]
    """
    Vectorized scrubbing of HTML tags and comprehensive translation
    of punctuation artifacts using Polars expressions.
    """
    return df.with_columns([
        pl.col(col)
        .fill_null("")
        # 1. Handle HTML unescaping artifacts natively
        .str.replace_all(r"&amp;", "&")
        .str.replace_all(r"&nbsp;", " ")
        .str.replace_all(r"&quot;", '"')
        .str.replace_all(r"&apos;", "'")
        .str.replace_all(r"&#39;", "'")
        # 2. Replicate <br> -> \n replacement (case-insensitive)
        .str.replace_all(r"(?i)<br\s*/?>", "\n")
        # 3. Strip all remaining HTML tags
        .str.replace_all(r"<[^>]+>", "")

        # 4. Map punctuation translation dictionary
        .str.replace_many(
            list(str(chr(k)) for k in _PUNCT_TRANSLATION.keys()),
            list(str(v) for v in _PUNCT_TRANSLATION.values())
        )
        # 5. ASCII Fold: Strip non-ASCII characters / accents
        # (Replicates your _to_ascii_plaintext step)
        .str.replace_all(r"[^\x00-\x7F]+", "")

        # 6. Normalize all newline variations (\r\n or \r -> \n)
        .str.replace_all(r"\r\n|\r", "\n")

        # 7. Collapse trailing whitespace before/after newlines
        .str.replace_all(r"[ \t]+\n", "\n")
        .str.replace_all(r"\n[ \t]+", "\n")

        # 8. Collapse 3+ consecutive newlines down to 2 (\n\n)
        .str.replace_all(r"\n{3,}", "\n\n")

        # 9. Final strip of outer whitespace edges
        .str.strip_chars()
        .alias(col)
        for col in valid_cols
    ])


@final
class Wahapedia40kProcessor:
    def __init__(self, game: GameConfig, temp_dir: Path, data_dir: Path) -> None:
        self._game: GameConfig = game
        self._input_dir: Path = temp_dir / self._game.folder_name
        self._output_dir: Path = data_dir / self._game.folder_name
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_filename: str = "manifest.json"
        self.manifest: dict[str, str] = self._load_manifest()
        self._results: dict[str, pl.LazyFrame] = {}

    def _load_manifest(self) -> dict[str, str]:
        manifest_output: Path = self._output_dir / self._manifest_filename
        if manifest_output.exists():
            try:
                with open(
                    file=self._output_dir / self._manifest_filename, mode="r"
                ) as f:
                    return cast(dict[str, str], json.load(fp=f))
            except Exception as e:
                logger.error(f"Manifest Load Failed: {e}")
        logger.info("Created New Manifest")
        return {"wahapedia_version": "0000-00-00 00:00:00", "last_sync_run": ""}

    def save_manifest(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["last_sync_run"] = datetime.now().strftime(
            format="%Y-%m-%d %H:%M:%S"
        )
        self._save_json(json_entries=self.manifest, filename="manifest.json")
        logger.info(f"Saved manifest.json ({len(self.manifest)})")

    def process_last_update(self) -> str:
        last_update_file: Path = self._input_dir / "Last_update.csv"
        if last_update_file.exists():
            with open(file=last_update_file, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter="|")
                _an: list[str] = next(reader)
                row: list[str] = next(reader)
                return row[0].strip()
        logger.info("Last_update.csv loaded")
        return ""

    def _load_file(self, filename: str) -> pl.DataFrame:
        path: Path = self._input_dir / filename
        if not path.exists():
            logger.warning(f"{filename} not found.")
            return pl.DataFrame()
        try:
            raw_text: str = path.read_text(encoding="utf-8")
            repaired_text: str = repair_wahapedia_pipe_csv(raw=raw_text)
            cleaned_lines: list[str] = []
            for line in repaired_text.splitlines():
                stripped = line.rstrip()
                if stripped.endswith("|"):
                    stripped = stripped[:-1]
                cleaned_lines.append(stripped)

            normalized_csv_text = "\n".join(cleaned_lines)
            
            df: pl.DataFrame = pl.read_csv(
                normalized_csv_text.encode("utf-8"),
                has_header=True,
                quote_char=None,
                separator="|",
                infer_schema=False,
                truncate_ragged_lines=True
            )
            cleaned_df: pl.DataFrame = scrub_html_from_columns(df=df, text_columns=df.columns)
            logger.info(f"{filename} loaded.")
            return cleaned_df
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
            return pl.DataFrame()

    @logger.catch
    def _save_json(
        self, json_entries: list[dict[str, str]] | dict[str, str], filename: str
    ) -> None:
        output_path: Path = self._output_dir / filename
        with open(file=output_path, mode="w", encoding="utf-8") as f:
            json.dump(obj=json_entries, fp=f, indent=2)

    def _handle_files(self) -> None:
        try:
            files_w_columns: dict[str, list[str]] = self._game.files
            files: list[str] = list(files_w_columns.keys())
            for f in files:
                try:
                    df: pl.DataFrame = self._load_file(filename=f"{f}.csv")
                    if not df.is_empty(): 
                        self._results[f.lower()] = df.select(files_w_columns[f]).lazy()
                    else:
                        logger.warning("Empty Dataframe, file could not be loaded")
                except Exception as e:
                    logger.error(f"Could not load {f}: {e}")
        except Exception as e:
            logger.error(f"Exception occurred: {e}")

    @logger.catch
    def _create_abilties_file(self) -> None:
        abilities_df: pl.LazyFrame = self._results["abilities"]
        datasheet_abil_df: pl.LazyFrame = self._results["datasheets_abilities"]

        processed_abilitiy_df = (
            abilities_df
            .group_by(["id", "name", "description"])
            .agg(pl.col("faction_id").alias("faction_ids"))
        )

        non_id_abilities_df = (
            datasheet_abil_df
            .filter(pl.col("ability_id").is_null())
            .select(
                pl.col("name"), 
                pl.col("type"),
                pl.col("description"), 
                pl.col("datasheet_id")
            )
        )
        gen_id_grouped_df = (
            non_id_abilities_df
            .select(["name", "description", "type", "datasheet_id"])
            .group_by(["name", "description", "type"])
            .agg(pl.col("datasheet_id").alias("datasheet_ids"))
            .with_columns([
                (pl.int_range(111000001, 111000001 + pl.len()).cast(pl.String).alias("id")),
                pl.lit([], dtype=pl.List(pl.String)).alias("faction_ids")
            ])
        )

        id_abilities_df = (
            datasheet_abil_df
            .select(
                pl.col("ability_id"), 
                pl.col("type"), 
                pl.col("parameter"), 
                pl.col("datasheet_id")
            ).drop_nulls("ability_id")
        )
        ability_types_df = id_abilities_df.select(["ability_id", "type"])
        master_ability_df = pl.concat([
            processed_abilitiy_df
            .join(ability_types_df, left_on="id", right_on="ability_id", how="inner"),
            gen_id_grouped_df
            .select(["id", "name", "description", "faction_ids", "type"])
        ]).collect()

        records = (
            master_ability_df
            .select(["id", "name", "description", "type", "faction_ids"])
            .unique(subset=["id"])
            .to_dicts()
        )
        self._save_json(json_entries=records, filename="abilities.json")
        logger.info("Saved abilities.json with {} rows", len(records))
        

    def _create_faction_file(self) -> None:
        logger.warning("Not implemented yet!")

    def _create_detachments_file(self) -> None:
        logger.warning("Not implemented yet!")

    def _create_enhancements_file(self) -> None:
        logger.warning("Not implemented yet!")

    def _create_keywords_file(self) -> None:
        logger.warning("Not implemented yet!")

    def _create_units_file(self) -> None:
        logger.warning("Not implemented yet!")
        
    def process_files(self) -> None:
        self._handle_files()
        self._create_abilties_file()
        self._create_enhancements_file()
        self._create_detachments_file()
        self._create_keywords_file()
        self._create_faction_file()
        self._create_units_file()
        
        