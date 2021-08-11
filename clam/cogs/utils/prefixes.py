import json
import logging
import os


log = logging.getLogger("clam")


class Prefixes:
    def __init__(self, bot, filename="prefixes.json"):
        self.bot = bot
        self.filename = filename
        self._prefixes = {}

    def _load(self):
        if not os.path.isfile("prefixes.json"):
            log.info("Prefixes file not found, creating...")
            with open(self.filename, "w") as f:
                json.dump({}, f)

        with open(self.filename, "r") as f:
            self._prefixes = json.load(f)

    async def load(self):
        """Loads the prefixes stored in the file into memory."""
        await self.bot.loop.run_in_executor(None, self._save)

    def _save(self):
        with open(self.filename, "w") as f:
            json.dump(self._prefixes, f, sort_keys=True)

    async def save(self):
        """Saves the cached prefixes to the file."""
        await self.bot.loop.run_in_executor(None, self._save)

    def get(self, guild_id):
        """Gets the prefixes for a guild."""
        return self._prefixes.get(str(guild_id), [self.bot.default_prefix])

    async def set(self, guild_id, prefixes):
        """Sets the prefixes for a guild."""
        self._prefixes[str(guild_id)] = prefixes
        await self.save()

    async def add(self, guild_id, prefix):
        """Adds a prefix to a guild."""
        prefixes = self.get(guild_id)
        prefixes.append(prefix)
        await self.set(guild_id, prefixes)

    async def remove(self, guild_id, prefix_index):
        """Removes a prefix from a guild."""
        prefixes = self.get(guild_id)
        prefix = prefixes.pop(prefix_index)
        await self.set(guild_id, prefixes)
        return prefix

    async def set_default(self, guild_id, prefix):
        """Sets the default prefix for a guild."""
        prefixes = self.get(guild_id)

        if prefix in prefixes:
            prefixes.pop(prefixes.index(prefix))

        prefixes.insert(0, prefix)
        await self.set(guild_id, prefixes)

    async def clear(self, guild_id):
        """Clears the prefixes for a guild."""
        self._prefixes.pop(str(guild_id))
        await self.save()
