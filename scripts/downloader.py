from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import final
from loguru import logger

import requests

from .config import GameConfig

@final
class Downloader:
    def __init__(self, game_config: GameConfig, base_temp_dir: Path) -> None:
        self.game: GameConfig = game_config
        self.target_dir: Path = base_temp_dir / self.game.folder_name
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.headers: dict[str, str] = {"User-Agent": "Mozilla/5.0"}

    def _download_file(self, filename: str) -> None:
        url: str = f"{self.game.source_url}/{filename}.csv"
        file_path: Path = self.target_dir / f"{filename}.csv"
        try:
            response: requests.Response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            _an: int = file_path.write_bytes(response.content)
            logger.info(f"Successfully Downloaded: {filename}")
        except Exception as e:
            logger.error(f"Failed to Download: {filename}: {e}")

    @logger.catch
    def download(self) -> None:
        file_list: list[str] = list(self.game.files.keys())
        logger.info(f"Downloading {len(file_list)} files to {self.target_dir}")
        with ThreadPoolExecutor(max_workers=5) as executor:
            _an: Iterator[None] = executor.map(self._download_file, file_list)
