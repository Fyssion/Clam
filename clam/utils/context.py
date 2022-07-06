from typing import Union

import discord
from discord.ext import commands

from .emojis import GREEN_TICK, RED_TICK
from .menus import BasicPages, ConfirmView, MenuPages, TablePages
from .utils import reply_to


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

    async def get_guild_log(self):
        return await self.bot.get_guild_log(self.guild.id)

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

    async def confirm(self, message, *, timeout=60.0, delete_after=True, author_id=None):
        author_id = author_id or self.author.id
        view = ConfirmView(
            timeout=timeout,
            delete_after=delete_after,
            ctx=self,
            author_id=author_id,
        )
        view.message = await self.send(message, view=view)
        await view.wait()
        return view.value

    def pages(self, entries, per_page=10, **paginator_kwargs):
        return BasicPages(entries, per_page, embed=False, **paginator_kwargs, ctx=self)

    def embed_pages(
        self, entries, embed, per_page=10,
    ):
        if embed is None:
            raise ValueError("Embed argument must not be None.")

        return BasicPages(entries, per_page, embed, ctx=self)

    def table_pages(self, *args, **kwargs):
        return MenuPages(TablePages(*args, **kwargs), ctx=self)


class GuildContext(Context):
    guild: discord.Guild
    author: discord.Member
    channel: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread]
    me: discord.Member
