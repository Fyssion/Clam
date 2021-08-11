import collections
import datetime
import json
import logging
import os.path

import aiohttp
import async_cse
import discord
import wolframalpha
from cleverbot import async_ as cleverbot
from discord.ext import commands

from config import Config
from cogs.utils import db
from cogs.utils.context import Context
from cogs.utils.errors import PrivateCog
from cogs.utils.prefixes import Prefixes


log = logging.getLogger("clam")


def get_command_prefix(bot, message):
    prefixes = [bot.default_prefix]

    if message.guild:
        prefixes = bot.prefixes.get(message.guild.id)

    # Add ! and ? to prefixes in DMs for easier use
    else:
        prefixes.extend(["!", "?", "! ", "? "])

    return commands.when_mentioned_or(*prefixes)(bot, message)


initial_extensions = [
    "cogs.admin",
    "cogs.among",
    "cogs.ccs",
    "cogs.events",
    "cogs.fun",
    "cogs.games",
    "cogs.highlight",
    "cogs.internet",
    "cogs.log",
    "cogs.mathematics",
    "cogs.meta",
    "cogs.moderation",
    "cogs.music",
    "cogs.selfroles",
    "cogs.settings",
    "cogs.stars",
    "cogs.stats",
    "cogs.tags",
    "cogs.timers",
    "cogs.todo",
    "cogs.tools",
]


class Clam(commands.Bot):
    def __init__(self):
        log.info("Loading config...")
        self.config = Config("config.yml")

        self.debug = self.config.debug

        self.default_prefix = "c." if not self.debug else "dev "

        intents = discord.Intents.all()
        intents.presences = False

        debug_notice = f" in DEBUG mode {self.debug}" if self.debug else ""
        log.info(f"Starting bot{debug_notice}...")

        super().__init__(
            command_prefix=get_command_prefix,
            description="A multi-purpose Discord bot. Likes to hide in its shell.",
            owner_id=224513210471022592,
            case_insensitive=True,
            intents=intents,
        )
        self.log = log

        log.info("Loading prefixes...")
        self.prefixes = Prefixes(self)
        self.prefixes._load()

        log.info("Loading blacklist...")
        if not os.path.isfile("blacklist.json"):
            with open("blacklist.json", "w") as f:
                json.dump([], f)

        with open("blacklist.json", "r") as f:
            self.blacklist = json.load(f)

        self.reddit_id = self.config.reddit_id
        self.reddit_secret = self.config.reddit_secret
        self.dev = self.get_user(224513210471022592)
        self.error_cache = collections.deque(maxlen=100)
        self.console = None
        self.uptime = None
        self.session = None
        self.highlight_words = []
        self.cleverbot = None
        self.wolfram = wolframalpha.Client(self.config.wolfram_api_key)
        self.loop.create_task(self.prepare_bot())

        # user_id: spam_amount
        self.spammers = {}
        self._cd = commands.CooldownMapping.from_cooldown(
            10.0, 12.0, commands.BucketType.user
        )

        self.cogs_to_load = initial_extensions

        self.add_check(self.private_cog_check)

        log.info("Loading extension 'jishaku'")
        self.load_extension("jishaku")

        for cog in initial_extensions:
            log.info(f"Loading extension '{cog}'")
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
        log.info("Preparing async features...")
        self.pool = await db.Table.create_pool(self.config.database_uri)
        self.google_client = async_cse.Search(self.config.google_api_key)
        self.session = aiohttp.ClientSession(loop=self.loop)
        self._adapter = discord.AsyncWebhookAdapter(self.session)
        self.cleverbot = cleverbot.Cleverbot(self.config.cleverbot_api_key, tweak1=0, tweak2=100, tweak3=100)

        log.info("Preparing status hook...")
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
        self.blacklist.pop(self.blacklist.index(str(user_id)))

        with open("blacklist.json", "w") as f:
            json.dump(self.blacklist, f)

        self.log.info(f"Removed {user_id} from the blacklist.")

    async def get_guild_log(self, guild_id):
        log_cog = self.get_cog("Log")
        if not log_cog:
            return None

        return await log_cog.get_guild_log(guild_id)

    def guild_prefix(self, guild):
        if not guild:
            return self.default_prefix

        return self.prefixes.get(guild.id)[0]

    def get_guild_prefixes(self, guild):
        return self.prefixes.get(guild.id)

    def private_cog_check(self, ctx):
        # TODO: make this configurable?
        global_guild_overrides = [454469821376102410, 621123303343652867]
        global_user_overrides = [self.owner_id, 224513210471022592, 224513210471022592]

        cog = ctx.cog

        if not getattr(cog, "private", False):
            return True

        cog_guild_overrides = getattr(cog, "private_guild_overrides", [])
        cog_user_overrides = getattr(cog, "private_user_overrides", [])

        if ctx.author.id in global_user_overrides or ctx.author.id in cog_user_overrides:
            return True

        if ctx.guild and ctx.guild.id in global_guild_overrides or ctx.guild.id in cog_guild_overrides:
            return True

        raise PrivateCog("This is a private cog.")

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
        em.set_thumbnail(url=user.avatar.url)
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
            if spammers[ctx.author.id] >= 5:
                await self.log_spammer(ctx, bucket, retry_after, blacklisted=True)
                self.add_to_blacklist(ctx.author)
                del spammers[ctx.author.id]
                return await ctx.send(
                    f"You have been permanently blacklisted for spamming.\n"
                    "If you wish appeal, please contact the owner of the bot, "
                    "who can be found here: <https://www.discord.gg/eHxvStNJb7>"
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
        if self.debug.full and message.guild.id not in [
            454469821376102410,
            621123303343652867,
        ]:
            return
        await self.process_commands(message)

    async def on_ready(self):
        if self.uptime is None:
            self.uptime = datetime.datetime.utcnow()
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

    async def close(self):
        await self.pool.close()
        await self.google_client.close()
        await self.cleverbot.close()

        music = self.get_cog("Music")
        if music:
            await music.stop_all_players()

        if not self.session.closed:
            await self.session.close()

        await super().close()

    def run(self):
        super().run(self.config.bot_token)


if __name__ == "__main__":
    launcher_log = logging.getLogger("clam.launcher")

    launcher_log.info("Starting...")
    bot = Clam()
    bot.run()
    launcher_log.info("All done")
