import csv
from typing import cast
import pandas as pd
import json
from pathlib import Path
from scripts import GameConfig
from scripts.constants import IdPrefix


class Wahapedia40kProcessor:
    def __init__(self, game: GameConfig, temp_dir: Path, data_dir: Path):
        self._game = game
        self._input_dir = temp_dir / self._game.folder_name
        self._output_dir = data_dir / self._game.folder_name
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _load_file(self, filename: str) -> pd.DataFrame:
        path = self._input_dir / filename
        if not path.exists():
            print(f"    [ERROR] {filename} not found.")
            return pd.DataFrame()
        try:
            # noinspection PyArgumentList
            df = pd.read_csv(
                path,
                sep='|',
                encoding="utf-8",
                keep_default_na=False
            )
            print(f"    [OK] {filename} loaded.")
            return cast(pd.DataFrame, df)
        except Exception as e:
            print(f"    [ERROR] Error loading {filename}: {e}")
            return pd.DataFrame()

    def _process_factions(self):
        print(f"\n  BUILDING factions.json")
        try:
            faction_df = self._load_file("Factions.csv")
            stratagem_df = self._load_file("Stratagems.csv")
            abilities_df = self._load_file("Abilities.csv")
            detachments_df = self._load_file("Detachments.csv")

            if faction_df.empty:
                print("    [WARNING] Factions.csv is missing or empty. Skipping.")
                return

            faction_list = []

            for _, row in faction_df.iterrows():
                raw_id = str(row['id'])
                faction_id = f"{IdPrefix.FACTION}{raw_id}"

                # 1. Filter IDs belonging to this faction
                strat_ids = [f"{IdPrefix.STRATAGEM}{i}" for i in stratagem_df.loc[stratagem_df['faction_id'] == raw_id, 'id'].unique()]
                abil_ids = [f"{IdPrefix.ABILITY}{i}" for i in abilities_df.loc[abilities_df['faction_id'] == raw_id, 'id'].unique()]
                det_ids = [f"{IdPrefix.DETACHMENT}{i}" for i in detachments_df.loc[detachments_df['faction_id'] == raw_id, 'id'].unique()]

                faction_obj = {
                    "id": faction_id,
                    "name": row['name'],
                    "stratagems": list(set(strat_ids)),
                    "abilities": list(set(abil_ids)),
                    "detachments": list(set(det_ids))
                }
                faction_list.append(faction_obj)

                # Save to data/wh40k/factions.json
            with open(self._output_dir / "factions.json", 'w') as f:
                json.dump(faction_list, f, indent=4)
            print(f"    [OK] factions.json ({len(faction_list)})")
        except Exception as e:
            print(f"    [ERROR] factions.json: {e}")

    def process_files(self):
        self._process_factions()