import discord
from discord.ext import commands
import coloredlogs, logging
import yaml
from datetime import datetime as d


def get_prefix(client, message):
    
    prefixes = ['robo.', 'r.', 'Robo.', 'R.']

    return commands.when_mentioned_or(*prefixes)(client, message)

bot = commands.Bot(
    command_prefix=get_prefix,
    description='A general purpose bot.',
    owner_id=224513210471022592,
    case_insensitive=True,
    # activity = discord.Activity(name="for robo.help", type = 3)
)


# Colored logs install
bot.log = logging.getLogger(__name__)
coloredlogs.install(level='DEBUG', logger=bot.log, fmt='(%(asctime)s) %(levelname)s %(message)s', datefmt='%m/%d/%y - %H:%M:%S %Z')

#Config.yml load
with open("config.yml", 'r') as config:
    try:
        data = yaml.safe_load(config)

    except yaml.YAMLError as exc:
        bot.log.critical("Could not load config.yml")
        print(exc)
        import sys
        sys.exit()

TOKEN = data['bot-token']
bot.reddit_id = data['reddit-id']
bot.reddit_secret = data['reddit-secret']
bot.prefixes = ", ".join(['`r.`', '`R.`', '`robo.`', '`Robo.`', 'or when mentioned'])

cogs = ['cogs.meta', 'cogs.tools', 'cogs.reddit', 'cogs.fun']

@bot.event
async def on_ready():

    bot.log.info(f'Logged in as {bot.user.name} - {bot.user.id}')
        
    bot.startup_time = d.now()

    bot.remove_command('help')

    for cog in cogs:
        bot.load_extension(cog)
    
    return

# Finally, login the bot
bot.run(TOKEN, bot=True, reconnect=True)