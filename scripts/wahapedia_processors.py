from typing import cast, Any
import pandas as pd
import json
from pathlib import Path
from scripts import GameConfig

class Wahapedia40kProcessor:
    def __init__(self, game: GameConfig, temp_dir: Path, data_dir: Path):
        self._game = game
        self._input_dir = temp_dir / self._game.folder_name
        self._output_dir = data_dir / self._game.folder_name
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _load_file(self, filename: str) -> pd.DataFrame:
        path = self._input_dir / filename
        if not path.exists():
            return pd.DataFrame()
        options: dict[str, Any] = {
            'filepath_or_buffer': path,
            'sep': '|',
            'keep_default_na': False,
            'encoding': 'utf-8'
        }
        df = pd.read_csv(**options)
        return cast(pd.DataFrame, df)

    def _process_factions(self):
        print(f"\n>>> BUILDING factions.json")
        try:
            faction_df = self._load_file("Factions.csv")
            stratagem_df = self._load_file("Stratagem.csv")
            abilities_df = self._load_file("Abilities.csv")
            detachments_df = self._load_file("Detachments.csv")

            if faction_df.empty:
                print("     [WARNING] Factions.csv is missing or empty. Skipping.")
                return

            faction_list = []

            for _, row in faction_df.iterrows():
                raw_id = str(row['id'])
                faction_id = f"fac_{raw_id}"

                # 1. Filter IDs belonging to this faction
                strat_ids = [f"strat_{i}" for i in stratagem_df.loc[stratagem_df['faction_id'] == raw_id, 'id'].unique()]
                abil_ids = [f"abil_{i}" for i in abilities_df.loc[abilities_df['faction_id'] == raw_id, 'id'].unique()]
                det_ids = [f"det_{i}" for i in detachments_df.loc[detachments_df['faction_id'] == raw_id, 'id'].unique()]

                faction_obj = {
                    "id": faction_id,
                    "name": row['name'],
                    "stratagems": list(set(strat_ids)),  # Set handles duplicates
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