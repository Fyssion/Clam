import discord
from discord.ext import commands

import coloredlogs
import logging
import yaml
from datetime import datetime as d
import aiohttp
import traceback


def get_prefix(client, message):

    prefixes = ['c.']

    if message.guild.id in [454469821376102410]:  # My coding server
        prefixes.append("!")

    return commands.when_mentioned_or(*prefixes)(client, message)


class RobotClam(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            description="Clam's personal Discord bot. Does bot things.",
            owner_id=224513210471022592,
            case_insensitive=True,
            # activity = discord.Activity(name="for robo.help", type = discord.ActivityType.playing)
        )
        # self.session = aiohttp.ClientSession(loop=self.loop)

        self.add_listener(self.on_mention_msg, 'on_message')

        self.log = logging.getLogger(__name__)
        coloredlogs.install(level='DEBUG', logger=self.log,
                            fmt='(%(asctime)s) %(levelname)s %(message)s',
                            datefmt='%m/%d/%y - %H:%M:%S %Z')

        # Config.yml load
        with open("config.yml", 'r') as config:
            try:
                self.config = yaml.safe_load(config)

            except yaml.YAMLError as exc:
                self.log.critical("Could not load config.yml")
                print(exc)
                import sys
                sys.exit()

        self.reddit_id = self.config['reddit-id']
        self.reddit_secret = self.config['reddit-secret']
        self.prefixes = " ".join(['`c.`', 'or when mentioned'])
        self.default_prefix = "c."
        self.dev = self.get_user(224513210471022592)
        self.previous_error = None

        self.cogs_to_load = ['cogs.meta', 'cogs.tools', 'cogs.reddit',
                             'cogs.fun', 'cogs.moderation', 'cogs.music',
                             'cogs.mathematics']

        self.remove_command('help')

        for cog in self.cogs_to_load:
            self.load_extension(cog)
        self.load_extension("jishaku")

    async def on_command_error(self, ctx, e: commands.CommandError):
        error = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
        print(error)
        self.previous_error = e
        if isinstance(e, commands.errors.CommandNotFound):
            return
        if isinstance(e, commands.errors.BadArgument):
            return await ctx.send("**:x: You provided a bad argument.** "
                                  "Make sure you are using the command correctly!")
        if isinstance(e, commands.errors.MissingRequiredArgument):
            return await ctx.send("**:x: Missing a required argument.** "
                                  "Make sure you are using the command correctly!")
        em = discord.Embed(title=":warning: Unexpected Error",
                           color=discord.Color.gold(),
                           timestamp=d.utcnow())
        description = ("An unexpected error has occured:"
                       f"```py\n{e}```\n The developer has been notified.")
        em.description = description
        em.set_footer(icon_url=self.user.avatar_url)
        await ctx.send(embed=em)
        # await self.dev.send("Error occured on one of your commands.")

    async def on_mention_msg(self, message):
        if message.content == f"<@{self.user.id}>":
            await message.channel.send("Hey there! I'm a bot. :robot:\n"
                                       "To find out more about me, type:"
                                       f" `{self.default_prefix}help`")

    async def on_ready(self):

        self.log.info(f'Logged in as {self.user.name} - {self.user.id}')

        self.startup_time = d.now()

        self.ordered_cogs = [c for c in self.cogs.keys()]

        self.session = aiohttp.ClientSession(loop=self.loop)

    def run(self):
        super().run(self.config['bot-token'], reconnect=True, bot=True)


bot = RobotClam()
bot.run()
