from discord.ext import commands
import discord

from .utils import reply_to
from .emojis import GREEN_TICK, RED_TICK
from .menus import MenuPages, Confirm, BasicPages, TablePages


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

    async def delete_send(self, *args, **kwargs):
        if "delete_after" not in kwargs:
            kwargs["delete_after"] = 5.0

        await self.send(*args, **kwargs)

    async def reply(self, content, **kwargs):
        await reply_to(self.message, content, **kwargs)

    def tick(self, tick, label=None):
        ticks = {True: GREEN_TICK, False: RED_TICK}
        tick = ticks[tick]

        return f"{tick} {label}" if label else tick

    async def confirm(self, message):
        return await Confirm(message).prompt(self)

    def pages(self, entries, per_page=10, **paginator_kwargs):
        return BasicPages(entries, per_page, embed=False, **paginator_kwargs)

    def embed_pages(
        self, entries, embed, per_page=10,
    ):
        if embed is None:
            raise ValueError("Embed argument must not be None.")

        return BasicPages(entries, per_page, embed)

    def table_pages(self, *args, **kwargs):
        return MenuPages(TablePages(*args, **kwargs), clear_reactions_after=True)
