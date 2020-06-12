from discord.ext import commands
import discord

import enum

from .emojis import RED_TICK, GREEN_TICK, GRAY_TICK


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
        tick = int(tick)
        ticks = {0: RED_TICK, 1: GREEN_TICK, 2: GRAY_TICK}
        return ticks[tick]
