from scripts import AppConfig, Wahapedia40kProcessor


def print_header(header: str):
    print("="*50)
    print(header)
    print("="*50)

def run_sync():
    print_header("Starting Data Conversion Pipeline".upper())

    for game in AppConfig.GAMES:
        print(f"\n>>> GAME: {game.name}")
        print(f"    Context: {game.source_url}")
        # worker: Downloader = Downloader(game, AppConfig.TEMP_DIR)
        # worker.download()
        processor: Wahapedia40kProcessor = Wahapedia40kProcessor(game, AppConfig.TEMP_DIR, AppConfig.DATA_DIR)
        remote_timestamp: str = processor.process_last_update()
        if remote_timestamp and remote_timestamp == processor.manifest["wahapedia_version"]:
            print("    Wahapedia Version matched Manifest Wahapedia Version. Skipping update")
        else:
            processor.process_files()
            processor.manifest["wahapedia_version"] = remote_timestamp
            processor.save_manifest()
        print(f">>> COMPLETED: {game.name}")

    # if AppConfig.TEMP_DIR.exists():
    #     print(f"\n>>> REMOVING: {AppConfig.TEMP_DIR}")
    #     shutil.rmtree(AppConfig.TEMP_DIR)
    #     print(f"    [OK] {AppConfig.TEMP_DIR}")

    print_header("Completed Data Conversion")

if __name__ == "__main__":
    run_sync()