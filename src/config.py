import os
from pathlib import Path
from dotenv import load_dotenv


def load_configuration_from_env() -> None:
    # Load from current working directory first, allowing .env to override
    load_dotenv(override=True)
    # Also try project root relative to this file (../.env)
    project_root_env = Path(__file__).resolve().parent.parent / ".env"
    if project_root_env.exists():
        load_dotenv(dotenv_path=project_root_env, override=True)


load_configuration_from_env()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD_ID: int = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
DISCORD_CHANNEL_ID: int = int(os.getenv("DISCORD_CHANNEL_ID", "0") or 0)

# Platform: common-gen5 (PS5/Xbox Series), common-gen4 (PS4/Xbox One), pc
PLATFORM: str = os.getenv("PLATFORM", "common-gen5").lower()
CLUB_ID: str = os.getenv("CLUB_ID", "")
REGION: str = os.getenv("REGION", "us").lower()

