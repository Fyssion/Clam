import discord
from discord.ext import tasks

import json


# TODO: Finish later
@tasks.loop(hours=24.0)
async def backup_json(bot_saves, databases):
    for db in databases:
        with open(f"{db}.json", "r") as f:
            if json.load(f) == bot_saves:
                continue
        with open(f"backups.{db}_backup.json", "w") as f:
            json.dump(db, f)