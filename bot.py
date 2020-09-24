import discord
from discord.ext import commands

import logging
import yaml
from datetime import datetime as d
import aiohttp
import traceback
import json
import async_cse
import collections
import os

from config import Config
from cogs.utils import db
from cogs.utils.context import Context
from cogs.utils.errors import PrivateCog, Blacklisted


formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

file_logger = logging.getLogger("discord")
file_logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(filename="clam.log", encoding="utf-8", mode="w")
file_handler.setFormatter(formatter)
file_logger.addHandler(file_handler)

sh = logging.StreamHandler()
sh.setFormatter(formatter)

logger = logging.getLogger("discord")
logger.setLevel(logging.INFO)
logger.addHandler(sh)

log = logging.getLogger("clam")
log.setLevel(logging.INFO)
log.addHandler(sh)
log.addHandler(file_handler)


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
    "cogs.among",
    "cogs.ccs",
    "cogs.events",
    "cogs.fun",
    "cogs.games",
    "cogs.internet",
    "cogs.mathematics",
    "cogs.meta",
    "cogs.moderation",
    "cogs.music",
    # "cogs.reddit",
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

        # intents = discord.Intents()
        # intents.members = True

        super().__init__(
            command_prefix=command_prefix,
            description="A multi-purpose Discord bot. Likes to hide in it's shell.",
            owner_id=224513210471022592,
            case_insensitive=True,
            # intents=intents,
        )
        self.log = log

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

    def dispatch(self, event, *args, **kwargs):
        # we override dispatch to block any events from
        # firing if the user is blacklisted.
        # this is the ultimate block because the bot ignores
        # everything from the blacklisted user

        def is_blacklisted(user_id):
            return str(user_id) in self.blacklist and user_id != self.owner_id

        if event in ["message", "message_delete", "message_edit"]:
            message = args[0]
            if is_blacklisted(message.author.id):
                return

        elif event == "reaction_add":
            user = args[1]
            if is_blacklisted(user.id):
                return

        elif event in ["raw_reaction_add", "raw_reaction_remove"]:
            payload = args[0]
            if is_blacklisted(payload.user_id):
                return

        super().dispatch(event, *args, **kwargs)

    async def prepare_bot(self):
        self.pool = await db.Table.create_pool(self.config.database_uri)
        self.google_client = async_cse.Search(self.config.google_api_key)
        self.session = aiohttp.ClientSession(loop=self.loop)
        self._adapter = discord.AsyncWebhookAdapter(self.session)

        if self.config.status_hook:
            self.status_hook = discord.Webhook.from_url(
                self.config.status_hook, adapter=self._adapter
            )
            await self.status_hook.send("Starting Clam...")

        else:
            self.status_hook = None

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
            and ctx.author.id not in [self.owner_id, 224513210471022592]
        ):
            if (
                hasattr(ctx.command.cog, "private_user_overrides")
                and ctx.author.id in ctx.command.cog.private_user_overrides
            ):
                return True

            if (
                hasattr(ctx.command.cog, "private_guild_overrides")
                and ctx.guild.id in ctx.command.cog.private_guild_overrides
            ):
                return True

            raise PrivateCog("This is a private cog.")

        return True

    async def get_context(self, message, *, cls=None):
        return await super().get_context(message, cls=cls or Context)

    async def log_spammer(self, ctx, bucket, retry_after, *, blacklisted=False):
        message = ctx.message
        guild_name = ctx.guild.name if ctx.guild else "DMs"
        guild_id = ctx.guild.id if ctx.guild else "None"
        user = ctx.author

        fmt = "User %s (ID: %s) in guild %s (ID: %s) was spamming. Retry after: %.2fs."
        self.log.warning(fmt, user, user.id, guild_name, guild_id, retry_after)

        if not blacklisted:
            return

        em = discord.Embed(title="User Auto-Blacklisted", color=discord.Color.red())
        em.set_thumbnail(url=user.avatar_url)
        em.add_field(name="User", value=f"{user} (ID: {user.id})", inline=False)
        em.add_field(name="Guild", value=f"{guild_name} (ID: {guild_id})", inline=False)
        em.add_field(
            name="Channel",
            value=f"{message.channel} (ID: {message.channel.id})",
            inline=False,
        )

        await self.console.send(embed=em)

    async def process_commands(self, message):
        if message.author.bot:
            return

        ctx = await self.get_context(message)

        if ctx.command is None:
            return

        is_owner = ctx.author.id == self.owner_id

        if str(ctx.author.id) in self.blacklist and not is_owner:
            return

        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        spammers = self.spammers
        if retry_after and not is_owner:
            if ctx.author.id in spammers:
                spammers[ctx.author.id] += 1
            else:
                spammers[ctx.author.id] = 1
            if spammers[ctx.author.id] > 5:
                await self.log_spammer(ctx, bucket, retry_after, blacklisted=True)
                self.add_to_blacklist(ctx.author)
                del spammers[ctx.author.id]
                return await ctx.send(
                    f"You have been permanently blacklisted for spamming.\n"
                    "If you wish appeal, please contact the owner of the bot, "
                    "who can be found here: <https://www.discord.gg/wfCGTrp>"
                )
            await self.log_spammer(ctx, bucket, retry_after)
            return await ctx.send(
                f"You are on global cooldown. Try again after {int(retry_after)} seconds."
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
        if self.console is None:
            self.console = self.get_channel(711952122132037722)

        self.log.info(f"Logged in as {self.user.name} - {self.user.id}")

        if self.config.status_hook:
            await self.status_hook.send("Received READY event")

    async def on_connect(self):
        if self.config.status_hook:
            await self.status_hook.send("Connected to Discord")

    async def on_resumed(self):
        if self.config.status_hook:
            await self.status_hook.send("Resumed connection with Discord")

    async def on_disconnect(self):
        if not self.session.closed and self.config.status_hook:
            await self.status_hook.send("Disconnected from Discord")

    async def logout(self):
        await super().logout()
        await self.pool.close()
        await self.google_client.close()
        if not self.session.closed:
            await self.session.close()

        music = self.get_cog("Music")
        if music:
            await music.stop_all_players()

    def run(self):
        super().run(self.config.bot_token)


if __name__ == "__main__":
    bot = Clam()
    bot.run()
