from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

class GameConfig:
    def __init__(self, name, base_url, context_path, folder_name, files=None):
        self.name = name
        self.base_url = base_url
        self.context_path = context_path
        self.folder_name = folder_name
        self.files = files

    @property
    def source_url(self):
        return f"{self.base_url.rstrip('/')}/{self.context_path.rstrip('/')}"

class AppConfig:
    TEMP_DIR = ROOT_DIR / "temp"
    DATA_DIR = ROOT_DIR / "data"

    GAMES = [
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
                "Datasheets_leader.csv"
            ]
        )
    ]