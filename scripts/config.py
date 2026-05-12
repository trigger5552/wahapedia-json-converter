from pathlib import Path
from typing import final

ROOT_DIR: Path = Path(__file__).resolve().parent.parent


@final
class GameConfig:
    def __init__(self, name: str, base_url: str, context_path: str, folder_name: str, files: list[str]) -> None:
        self.name: str = name
        self.base_url: str = base_url
        self.context_path: str = context_path
        self.folder_name: str = folder_name
        self.files: list[str] = files

    @property
    def source_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.context_path.rstrip('/')}"


@final
class AppConfig:
    TEMP_DIR: Path = ROOT_DIR / "temp"
    DATA_DIR: Path = ROOT_DIR / "data"

    GAMES: list[GameConfig] = [
        GameConfig(
            name="Warhammer 40k 10th Edition",
            base_url="https://wahapedia.ru/",
            context_path="wh40k10ed",
            folder_name="wh40k",
            files=[
                "Last_update.csv",
                "Factions.csv",
                "Source.csv",
                "Stratagems.csv",
                "Abilities.csv",
                "Enhancements.csv",
                "Detachments.csv",
                "Detachment_abilities.csv",
                "Datasheets.csv",
                "Datasheets_abilities.csv",
                "Datasheets_keywords.csv",
                "Datasheets_models.csv",
                "Datasheets_options.csv",
                "Datasheets_wargear.csv",
                "Datasheets_unit_composition.csv",
                "Datasheets_models_cost.csv",
                "Datasheets_stratagems.csv",
                "Datasheets_enhancements.csv",
                "Datasheets_detachment_abilities.csv",
                "Datasheets_leader.csv",
            ],
        )
    ]
