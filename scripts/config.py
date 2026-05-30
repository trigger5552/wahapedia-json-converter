from pathlib import Path
from typing import final

ROOT_DIR: Path = Path(__file__).resolve().parent.parent


@final
class GameConfig:
    def __init__(self, name: str, base_url: str, context_path: str, folder_name: str, files: dict[str, list[str]]) -> None:
        self.name: str = name
        self.base_url: str = base_url
        self.context_path: str = context_path
        self.folder_name: str = folder_name
        self.files: dict[str, list[str]] = files

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
            files={
                "Factions": ["id", "name", "link"],
                "Source": ["id", "type", "name", "edition", "version", "errata_date", "errata_link"],
                "Stratagems": ["id", "name", "type", "cp_cost", "turn", "phase", "detachment_id", "description"],
                "Abilities": ["id", "name", "description", "faction_id"],
                "Enhancements": ["id", "name", "cost", "detachment_id", "description"],
                "Detachments": ["id", "faction_id", "name"],
                "Detachment_abilities": ["id", "detachment_id", "name", "description"],
                "Datasheets": ["id", "name", "faction_id", "source_id", "role", "loadout", "transport", "leader_footer", "damaged_w", "damaged_description", "link"],
                "Datasheets_abilities": ["datasheet_id", "line", "ability_id", "model", "name", "description", "type", "parameter"],
                "Datasheets_keywords": ["datasheet_id", "keyword", "model", "is_faction_keyword"],
                "Datasheets_models": ["datasheet_id", "line", "name", "M", "T", "Sv", "inv_sv", "inv_sv_descr", "W", "Ld", "OC", "base_size", "base_size_descr"],
                "Datasheets_wargear": ["datasheet_id", "line", "line_in_wargear", "dice", "name", "description", "range", "type", "A", "BS_WS", "S", "AP", "D"],
                "Datasheets_unit_composition": ["datasheet_id", "line", "description"],
                "Datasheets_models_cost": ["datasheet_id", "line", "description", "cost"],
                "Datasheets_stratagems": ["datasheet_id", "stratagem_id"],
                "Datasheets_enhancements": ["datasheet_id", "enhancement_id"],
                "Datasheets_detachment_abilities": ["datasheet_id", "detachment_ability_id"],
                "Datasheets_leader": ["leader_id", "attached_id"]
            },
        )
    ]
