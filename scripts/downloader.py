import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .config import GameConfig


class Downloader:
    def __init__(self, game_config: GameConfig, base_temp_dir: Path):
        self.game = game_config
        self.target_dir = base_temp_dir / self.game.folder_name
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {'User-Agent': 'Mozilla/5.0'}

    def _download_file(self, filename):
        url = f"{self.game.source_url}/{filename}"
        file_path = self.target_dir / filename
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            file_path.write_bytes(response.content)
            print(f"    [OK] {filename}")
        except Exception as e:
            print(f"    [ERROR] {filename}: {e}")

    def download(self):
        file_list = self.game.files
        print(f"    Downloading {len(file_list)} files to {self.target_dir}")
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(self._download_file, file_list)
