import discord
from discord.ext import commands
import coloredlogs, logging
import yaml
from datetime import datetime as d
import aiohttp



def get_prefix(client, message):
    
    prefixes = ['robo.', 'r.', 'Robo.', 'R.']

    return commands.when_mentioned_or(*prefixes)(client, message)

class RoboClam(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            description="Robotic Clam: A general purpose Discord bot.",
            owner_id=224513210471022592,
            case_insensitive=True,
            # activity = discord.Activity(name="for robo.help", type = 3)
        )
        # self.session = aiohttp.ClientSession(loop=self.loop)

        self.add_listener(self.my_message, 'on_message')

        self.log = logging.getLogger(__name__)
        coloredlogs.install(level='DEBUG', logger=self.log, fmt='(%(asctime)s) %(levelname)s %(message)s', datefmt='%m/%d/%y - %H:%M:%S %Z')

        #Config.yml load
        with open("config.yml", 'r') as config:
            try:
                self.data = yaml.safe_load(config)

            except yaml.YAMLError as exc:
                self.log.critical("Could not load config.yml")
                print(exc)
                import sys
                sys.exit()

        self.reddit_id = self.data['reddit-id']
        self.reddit_secret = self.data['reddit-secret']
        self.prefixes = ", ".join(['`r.`', '`R.`', '`robo.`', '`Robo.`', 'or when mentioned'])
        self.defaultPrefix = "r."

        self.cogsToLoad = ['cogs.meta', 'cogs.tools', 'cogs.reddit', 'cogs.fun']
    
    async def my_message(self, message):
        if message.content == f"<@{self.user.id}>":
            await message.channel.send(f"Hey there! I'm a bot. :robot:\nTo find out more about me, type: `{self.defaultPrefix}help`")

    async def on_ready(self):

        self.log.info(f'Logged in as {self.user.name} - {self.user.id}')
            
        self.startup_time = d.now()

        self.remove_command('help')

        for cog in self.cogsToLoad:
            self.load_extension(cog)
        self.load_extension("jishaku")

        self.session = aiohttp.ClientSession(loop=self.loop)
    
    def run(self):
        super().run(self.data['bot-token'], reconnect=True, bot=True)
    

bot = RoboClam()
bot.run()