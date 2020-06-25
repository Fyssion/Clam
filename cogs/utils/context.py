from discord.ext import commands, menus
import discord

import enum

from . import colors
from .emojis import GREEN_TICK, RED_TICK
from .menus import MenuPages


class Confirm(menus.Menu):
    def __init__(self, msg):
        super().__init__(timeout=30.0, delete_message_after=True)
        self.msg = msg
        self.result = None

    async def send_initial_message(self, ctx, channel):
        return await channel.send(self.msg)

    @menus.button(GREEN_TICK)
    async def do_confirm(self, payload):
        self.result = True
        self.stop()

    @menus.button(RED_TICK)
    async def do_deny(self, payload):
        self.result = False
        self.stop()

    async def prompt(self, ctx):
        await self.start(ctx, wait=True)
        return self.result


class BasicPageSource(menus.ListPageSource):
    def __init__(self, entries, per_page, *, title=None, description=None, footer=None):
        super().__init__(self, entries, per_page=per_page)
        self.title = title
        self.description = description
        self.footer = footer

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page

        message = []
        if self.title:
            message.append(f"**{self.title}**")

        if self.description:
            message.append(self.description)

        message.append(f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        message.append(
            "\n".join(f"{i+1}. {v}" for i, v in enumerate(entries, start=offset))
        )

        if self.footer:
            message.append(self.footer)

        return "\n".join(message)


class BasicPageSource(menus.ListPageSource):
    def __init__(self, entries, per_page, *, title=None, description=None, footer=None):
        super().__init__(entries, per_page=per_page)
        self.title = title
        self.description = description
        self.footer = footer

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page

        message = []
        if self.title:
            message.append(f"**{self.title}**")

        if self.description:
            message.append(self.description)

        message.append(f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        message.append("```ini")
        message.append(
            "\n".join(f"[{i+1}] {v}" for i, v in enumerate(entries, start=offset))
        )
        message.append("```")

        if self.footer:
            message.append(self.footer)

        return "\n".join(message)


class EmbedPageSource(menus.ListPageSource):
    def __init__(self, entries, per_page, embed):
        super().__init__(entries, per_page=per_page)
        self.embed = embed

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page

        formatted = "\n".join(
            f"{i+1}. {v}" for i, v in enumerate(entries, start=offset)
        )
        self.embed.description = self.embed.description or ""
        self.embed.description += "\n\n" + formatted

        return self.embed


class BasicPages(MenuPages):
    def __init__(self, entries, per_page, embed=None, **paginator_kwargs):
        if embed:
            super().__init__(
                EmbedPageSource(entries, per_page, embed), clear_reactions_after=True,
            )

        else:
            super().__init__(
                BasicPageSource(entries, per_page=per_page, **paginator_kwargs),
                clear_reactions_after=True,
            )


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
