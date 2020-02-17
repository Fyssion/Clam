import discord
from discord.ext import commands

import coloredlogs
import logging
import yaml
from datetime import datetime as d
import aiohttp
import traceback
import json

from cogs.utils import backup

def get_prefix(client, message):

    prefixes = ['c.']

    if str(message.guild.id) in client.guild_prefixes.keys():
        prefixes = client.guild_prefixes[str(message.guild.id)]

    return commands.when_mentioned_or(*prefixes)(client, message)


class RobotClam(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            description="Fyssion's personal Discord bot. Does bot things.",
            owner_id=224513210471022592,
            case_insensitive=True,
            # activity = discord.Activity(name="for robo.help", type = discord.ActivityType.playing)
        )
        # self.session = aiohttp.ClientSession(loop=self.loop)

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

        with open("prefixes.json", "r") as f:
            self.guild_prefixes = json.load(f)

        self.reddit_id = self.config['reddit-id']
        self.reddit_secret = self.config['reddit-secret']
        self.prefixes = ['`c.`', 'or when mentioned']
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

    def guild_prefix(self, guild):
        if str(guild) in self.guild_prefixes:
            return self.guild_prefixes[str(guild)][0]
        return "c."

    async def on_ready(self):

        self.log.info(f'Logged in as {self.user.name} - {self.user.id}')

        self.startup_time = d.now()

        self.ordered_cogs = [c for c in self.cogs.keys()]

        self.session = aiohttp.ClientSession(loop=self.loop)

    def run(self):
        super().run(self.config['bot-token'], reconnect=True, bot=True)


bot = RobotClam()
bot.run()
