import discord
from discord.ext import commands


class Tags(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":bookmark:"
        self.lot = self.bot.log


def setup(bot):
    bot.add_cog(Tags(bot))
