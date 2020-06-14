from discord.ext import commands
import discord

import enum

from .emojis import RED_TICK, GREEN_TICK


class Context(commands.Context):
    @property
    def guild_prefix(self):
        return self.bot.guild_prefix(self.guild)

    @property
    def console(self):
        return self.bot.console

    @property
    def db(self):
        return self.bot.pool

    def tick(self, tick):
        tick = bool(tick)
        ticks = {True: GREEN_TICK, False: RED_TICK}
        return ticks[tick]
