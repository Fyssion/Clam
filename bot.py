import discord
from discord.ext import commands
import coloredlogs, logging
import yaml
from datetime import datetime as d

# Colored logs install
l = logging.getLogger(__name__)
coloredlogs.install(level='DEBUG', logger=l, fmt='(%(asctime)s) %(levelname)s %(message)s', datefmt='%m/%d/%y - %H:%M:%S %Z')

#Config.yml load
with open("config.yml", 'r') as config:
    try:
        data = yaml.safe_load(config)

    except yaml.YAMLError as exc:
        l.critical("Could not load config.yml")
        print(exc)
        import sys
        sys.exit()

TOKEN = data['bot-token']
description = """
General purpose Discord bot.
"""
startup = None

def get_prefix(client, message):
    
    prefixes = ['robo.', 'r.', 'Robo.', 'R.']

    return commands.when_mentioned_or(*prefixes)(client, message)

bot = commands.Bot(                         # Create a new bot
    command_prefix=get_prefix,              # Set the prefix
    description='A general purpose bot.',  # Set a description for the bot
    owner_id=224513210471022592,            # Your unique User ID
    case_insensitive=True,                   # Make the commands case insensitive
    activity = discord.Activity(name="for robo.help", type = 3)
)

cogs = ['cogs.meta']

@bot.event
async def on_ready():
    global startup

    l.info(f'Logged in as {bot.user.name} - {bot.user.id}')

    startup = d.now()

    bot.remove_command('help')

    for cog in cogs:
        bot.load_extension(cog)
    
    return

# Finally, login the bot
bot.run(TOKEN, bot=True, reconnect=True)
