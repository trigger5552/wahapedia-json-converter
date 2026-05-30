from scripts import AppConfig, Wahapedia40kProcessor
from loguru import logger

@logger.catch
def run_sync():
    logger.info("{}Starting Data Conversion Pipeline{}".upper(), "="*12, "="*12)

    for game in AppConfig.GAMES:
        logger.info(f"GAME: {game.name}")
        logger.info(f"\tContext: {game.source_url}")
        # worker: Downloader = Downloader(game, AppConfig.TEMP_DIR)
        # worker.download()
        processor: Wahapedia40kProcessor = Wahapedia40kProcessor(game, AppConfig.TEMP_DIR, AppConfig.DATA_DIR)
        # remote_timestamp: str = processor.process_last_update()
        # if remote_timestamp and remote_timestamp == processor.manifest["wahapedia_version"]:
        #     print("    Wahapedia Version matched Manifest Wahapedia Version. Skipping update")
        # else:
        processor.process_files()
        # processor.manifest["wahapedia_version"] = remote_timestamp
        # processor.save_manifest()
        logger.info(f"COMPLETED: {game.name}")

    # if AppConfig.TEMP_DIR.exists():
    #     print(f"\n>>> REMOVING: {AppConfig.TEMP_DIR}")
    #     shutil.rmtree(AppConfig.TEMP_DIR)
    #     print(f"    [OK] {AppConfig.TEMP_DIR}")

    logger.info("{}Completed Data Conversion{}".upper(), "="*12, "="*12)


if __name__ == "__main__":
    run_sync()