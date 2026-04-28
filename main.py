import shutil
from scripts import AppConfig, Downloader, Wahapedia40kProcessor


def print_header(header: str):
    print("="*50)
    print(header)
    print("="*50)

def run_sync():
    print_header("Starting Data Conversion Pipeline".upper())

    for game in AppConfig.GAMES:
        print(f"\n>>> GAME: {game.name}")
        print(f"    Context: {game.source_url}")
        worker = Downloader(game, AppConfig.TEMP_DIR)
        worker.download()
        processor = Wahapedia40kProcessor(game, AppConfig.TEMP_DIR, AppConfig.DATA_DIR)
        processor.process_files()
        print(f">>> COMPLETED: {game.name}")

    # if AppConfig.TEMP_DIR.exists():
    #     print(f"\n>>> REMOVING: {AppConfig.TEMP_DIR}")
    #     shutil.rmtree(AppConfig.TEMP_DIR)
    #     print(f"    [OK] {AppConfig.TEMP_DIR}")

    print_header("Completed Data Conversion".upper())

if __name__ == "__main__":
    run_sync()