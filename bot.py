import discord
from discord.ext import commands

import coloredlogs
import logging
import yaml
from datetime import datetime as d
import aiohttp
import traceback
import json
import collections

from cogs.utils import backup, db
from cogs.utils.errors import PrivateCog


file_logger = logging.getLogger("discord")
file_logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename="clam.log", encoding="utf-8", mode="w")
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
file_logger.addHandler(handler)

logger = logging.getLogger("discord")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


class Context(commands.Context):
    @property
    def guild_prefix(self):
        return self.bot.guild_prefix(self.guild)

    @property
    def console(self):
        return self.bot.console

    @property
    def db(self):
        return self.bot.pool


def get_prefix(client, message):

    prefixes = ["c."]

    if not isinstance(message.channel, discord.DMChannel):
        if str(message.guild.id) in client.guild_prefixes.keys():
            prefixes = client.guild_prefixes[str(message.guild.id)]

    return commands.when_mentioned_or(*prefixes)(client, message)


def dev_prefix(client, message):

    prefixes = ["dev "]

    if not isinstance(message.channel, discord.DMChannel):
        if str(message.guild.id) in client.guild_prefixes.keys():
            prefixes = client.guild_prefixes[str(message.guild.id)]

    return prefixes


initial_extensions = [
    "cogs.admin",
    "cogs.fun",
    "cogs.games",
    "cogs.mathematics",
    "cogs.meta",
    "cogs.moderation",
    "cogs.music",
    "cogs.reddit",
    "cogs.stats",
    "cogs.tags",
    "cogs.tools",
]


class Clam(commands.Bot):
    def __init__(self):
        with open("config.yml", "r") as config:
            try:
                self.config = yaml.safe_load(config)

            except yaml.YAMLError as exc:
                self.log.critical("Could not load config.yml")
                print(exc)
                import sys

                sys.exit()

        command_prefix = get_prefix
        self.debug = False

        if "debug" in self.config.keys():
            if self.config["debug"]:
                command_prefix = dev_prefix
                self.debug = True

        super().__init__(
            command_prefix=command_prefix,
            description="A multi-purpose Discord bot. Likes to hide in it's shell.",
            owner_id=224513210471022592,
            case_insensitive=True,
            # activity = discord.Activity(name="for robo.help", type = discord.ActivityType.playing)
        )
        # self.session = aiohttp.ClientSession(loop=self.loop)

        self.log = logging.getLogger(__name__)
        coloredlogs.install(
            level="DEBUG",
            logger=self.log,
            fmt="(%(asctime)s) %(levelname)s %(message)s",
            datefmt="%m/%d/%y - %H:%M:%S %Z",
        )

        with open("prefixes.json", "r") as f:
            self.guild_prefixes = json.load(f)

        self.reddit_id = self.config["reddit-id"]
        self.reddit_secret = self.config["reddit-secret"]
        self.prefixes = ["`c.`", "or when mentioned"]
        self.default_prefix = "c."
        self.dev = self.get_user(224513210471022592)
        self.error_cache = collections.deque(maxlen=100)
        self.console = None
        self.startup_time = None
        self.session = None
        self.pool = None

        self.cogs_to_load = initial_extensions

        self.add_check(self.private_cog_check)

        self.load_extension("jishaku")

        for cog in initial_extensions:
            self.load_extension(cog)

        self.ordered_cogs = [c for c in self.cogs.values()]

    def guild_prefix(self, guild):
        if not guild:
            return "c."
        guild = guild.id
        if str(guild) in self.guild_prefixes:
            return self.guild_prefixes[str(guild)][0]
        return "c."

    def private_cog_check(self, ctx):
        if (
            hasattr(ctx.command.cog, "private")
            and ctx.guild.id not in [454469821376102410, 621123303343652867,]
            and ctx.author.id != self.owner_id
        ):
            raise PrivateCog("This is a private cog.")

        return True

    async def get_context(self, message, *, cls=None):
        return await super().get_context(message, cls=cls or Context)

    async def on_message(self, message):
        if self.debug and message.guild.id not in [
            454469821376102410,
            621123303343652867,
        ]:
            return
        await self.process_commands(message)

    async def on_ready(self):
        if self.console is None:
            self.console = self.get_channel(711952122132037722)
        if self.startup_time is None:
            self.startup_time = d.now()
        if self.session is None:
            self.session = aiohttp.ClientSession(loop=self.loop)
        if self.pool is None:
            self.pool = await db.Table.create_pool(self.config["database-uri"])

        self.log.info(f"Logged in as {self.user.name} - {self.user.id}")

    async def logout(self):
        await super().logout()
        await self.pool.close()

    def run(self):
        super().run(self.config["bot-token"], reconnect=True, bot=True)


if __name__ == "__main__":
    bot = Clam()
    bot.run()
