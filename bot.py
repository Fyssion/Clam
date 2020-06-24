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
import os

from config import Config
from cogs.utils import db
from cogs.utils.context import Context
from cogs.utils.errors import PrivateCog, Blacklisted


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


def get_prefix(bot, message):

    prefixes = ["c."]

    # Get prefixes from prefixes.json if the message is in a guild
    if not isinstance(message.channel, discord.DMChannel) and message.guild:
        if str(message.guild.id) in bot.guild_prefixes.keys():
            prefixes = bot.guild_prefixes[str(message.guild.id)]

    # Add ! to prefixes in DMs for easier use
    elif isinstance(message.channel, discord.DMChannel):
        prefixes.append("!")

    return commands.when_mentioned_or(*prefixes)(bot, message)


def dev_prefix(bot, message):

    prefixes = ["dev "]

    if not isinstance(message.channel, discord.DMChannel):
        if str(message.guild.id) in bot.guild_prefixes.keys():
            prefixes = bot.guild_prefixes[str(message.guild.id)]

    return prefixes


initial_extensions = [
    "cogs.admin",
    "cogs.events",
    "cogs.fun",
    "cogs.games",
    "cogs.internet",
    "cogs.mathematics",
    "cogs.meta",
    "cogs.moderation",
    "cogs.music",
    "cogs.reddit",
    "cogs.stats",
    "cogs.tags",
    "cogs.timers",
    "cogs.todo",
    "cogs.tools",
]


class Clam(commands.Bot):
    def __init__(self):
        self.config = Config("config.yml")

        command_prefix = get_prefix
        self.debug = False

        if self.config.debug:
            command_prefix = dev_prefix
            self.debug = True

        super().__init__(
            command_prefix=command_prefix,
            description="A multi-purpose Discord bot. Likes to hide in it's shell.",
            owner_id=224513210471022592,
            case_insensitive=True,
        )
        self.log = logging.getLogger(__name__)
        coloredlogs.install(
            level="DEBUG",
            logger=self.log,
            fmt="(%(asctime)s) %(levelname)s %(message)s",
            datefmt="%m/%d/%y - %H:%M:%S %Z",
        )

        with open("prefixes.json", "r") as f:
            self.guild_prefixes = json.load(f)

        if not os.path.isfile("blacklist.json"):
            with open("blacklist.json", "w") as f:
                json.dump([], f)

        with open("blacklist.json", "r") as f:
            self.blacklist = json.load(f)

        self.reddit_id = self.config.reddit_id
        self.reddit_secret = self.config.reddit_secret
        self.prefixes = ["`c.`", "or when mentioned"]
        self.default_prefix = "c."
        self.dev = self.get_user(224513210471022592)
        self.error_cache = collections.deque(maxlen=100)
        self.console = None
        self.startup_time = None
        self.session = None
        self.loop.create_task(self.prepare_bot())

        # user_id: spam_amount
        self.spammers = {}
        self._cd = commands.CooldownMapping.from_cooldown(
            10.0, 15.0, commands.BucketType.user
        )

        self.cogs_to_load = initial_extensions

        self.add_check(self.private_cog_check)

        self.load_extension("jishaku")

        for cog in initial_extensions:
            self.load_extension(cog)

        self.ordered_cogs = [c for c in self.cogs.keys()]

    async def prepare_bot(self):
        self.console = self.get_channel(711952122132037722)
        self.pool = await db.Table.create_pool(self.config.database_uri)
        self.session = aiohttp.ClientSession(loop=self.loop)
        self._adapter = discord.AsyncWebhookAdapter(self.session)
        self.status_hook = discord.Webhook.from_url(self.config.status_hook, adapter=self._adapter)

    def add_to_blacklist(self, user):
        self.blacklist.append(str(user.id))

        with open("blacklist.json", "w") as f:
            json.dump(self.blacklist, f)

        self.log.info(f"Added {user} to the blacklist.")

    def remove_from_blacklist(self, user_id):
        try:
            self.blacklist.pop(self.blacklist.index(str(user_id)))
        except ValueError:
            pass

        with open("blacklist.json", "w") as f:
            json.dump(self.blacklist, f)

        self.log.info(f"Removed {user_id} from the blacklist.")

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

    async def process_commands(self, message):
        if message.author.bot:
            return

        ctx = await self.get_context(message)

        if ctx.command is None:
            return

        if str(ctx.author.id) in self.blacklist:
            return

        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        spammers = self.spammers
        if retry_after and ctx.author.id != self.owner_id:
            if ctx.author.id in spammers:
                spammers[ctx.author.id] += 1
            else:
                spammers[ctx.author.id] = 1
            if spammers[ctx.author.id] > 10:
                self.add_to_blacklist(ctx.author)
                del spammers[ctx.author.id]
                raise Blacklisted("You are blacklisted.")
            return await ctx.send(
                f"**You are on cooldown.** Try again after {int(retry_after)} seconds."
            )
        else:
            try:
                del spammers[ctx.author.id]
            except KeyError:
                pass

        await self.invoke(ctx)

    async def on_message(self, message):
        if self.debug and message.guild.id not in [
            454469821376102410,
            621123303343652867,
        ]:
            return
        await self.process_commands(message)

    async def on_ready(self):
        if self.startup_time is None:
            self.startup_time = d.now()

        self.log.info(f"Logged in as {self.user.name} - {self.user.id}")
        await self.status_hook.send("Received READY event")

    async def on_connect(self):
        await self.status_hook.send("Connected to Discord")

    async def on_disconnect(self):
        if not self.session.closed:
            await self.status_hook.send("Disconnected from Discord")

    async def logout(self):
        await super().logout()
        await self.pool.close()

    def run(self):
        super().run(self.config.bot_token)


if __name__ == "__main__":
    bot = Clam()
    bot.run()
