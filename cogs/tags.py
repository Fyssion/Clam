import discord
from discord.ext import commands


class Tags(commands.Cog, name=":bookmark: Tags"):

    def __init__(self, bot):
        self.bot = bot
        self.lot = self.bot.log


def setup(bot):
    bot.add_cog(Tags(bot))
