import discord
from discord.ext import menus

import asyncio
import datetime

from cogs.utils.emojis import WAY_BACK, BACK, FORWARD, WAY_FOWARD, STOP, GREEN_TICK, RED_TICK
from .tabulate import tabulate


class MessageLabel:
    """A label to pass into UpdatingMessage

    This takes an emoji and some text, and will render out like this:
    (left: MessageLabel instance, right: render result)
      - MessageLabel<emoji='✅', text='Compute x'>     : '✅ Compute x'
      - MessageLabel<emoji='❌', text='Compute other'> : '❌ Compute other'
    """
    __slots__ = ("emoji", "text")

    def __init__(self, emoji, text):
        self.emoji = emoji
        self.text = text

    def __repr__(self):
        return f"MessageLabel<emoji='{self.emoji}', text='{self.text}'>"


class UpdatingMessage:
    """A simple updating status message

    This message displays a list of MessageLabel(s) and
    is automatically updated it every 3 seconds.

    This message can only be an embed as of writing.

    If it has been more than 3 seconds without an update,
    the message will be updated when there is a change
    (instead of every 3 seconds).

    Parameters
        -----------
        embed: Optional[:class:`discord.Embed`]
            The starting embed for the message.
        labels: Optional[List[:class:`MessageLabel`]]
            A list of labels to initialize instance with.
            You can add more labels with :meth:`add_label`
    """
    def __init__(self, *, embed=None, labels=None):
        self.context = None

        self.changes = 0
        self.labels = labels or []
        self.embed = embed or discord.Embed()
        self.original_description = self.embed.description
        self.message = None

        self._closed = False
        self._last_update = None
        self._updater_task = None

    @property
    def closed(self):
        """Returns whether the message has been closed (is no longer updating)"""
        return self._closed

    def render_embed(self):
        em = self.embed

        description = []

        for label in self.labels:
            description.append(f"{label.emoji} {label.text}")

        description = "\n".join(description)

        if self.original_description:
            em.description = self.original_description + description

        else:
            em.description = description

        return em

    def add_label(self, emoji, text):
        """Add a label

        Parameters
        -----------
        emoji: Optional[:class:`str`]
            The emoji of the label
        text: Optional[:class:`str`]
            The text of the label
        """
        self.labels.append(MessageLabel(emoji, text))

    def change_label(self, label, emoji=None, text=None):
        """Change a label

        Parameters
        -----------
        label: :class:`int`
            The index of the label
        emoji: Optional[:class:`str`]
            The emoji to change to
        text: Optional[:class:`str`]
            The text to change to
        """
        label = self.labels[label]

        label.emoji = emoji or label.emoji
        label.text = text or label.text

        self.changes += 1

    async def start(self, ctx):
        """Start the updating message. This creates the updater task."""
        self.context = ctx

        em = self.render_embed()
        self.message = await ctx.send(embed=em)

        self._updater_task = ctx.bot.loop.create_task(self.updater_loop())

    async def stop(self):
        """Stop the updating message. This performs a final edit before closing."""
        await self.message.edit(embed=self.render_embed())
        self._closed = True

    async def updater_loop(self):
        bot = self.context.bot
        changes = self.changes

        await bot.wait_until_ready()

        while not bot.is_closed() and not self.closed:
            await asyncio.sleep(0.5)

            if not self.changes:
                pass

            elif changes >= self.changes:
                pass

            else:
                now = datetime.datetime.utcnow()
                if self._last_update and self._last_update + datetime.timedelta(seconds=1) > now:
                    await discord.utils.sleep_until(self._last_update + datetime.timedelta(seconds=1))

                em = self.render_embed()

                changes = self.changes
                await self.message.edit(embed=em)


class TablePages(menus.ListPageSource):
    def __init__(self, data, *, language="prolog", title="", description="", per_page=10):
        entries = tabulate(data, as_list=True)
        super().__init__(entries, per_page=per_page)

        self.language = language
        self.title = title
        self.description = description

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        table = "\n".join(v for i, v in enumerate(entries, start=offset))

        max_pages = self.get_max_pages()
        page_num = f"Page {menu.current_page + 1}/{max_pages}" if max_pages > 1 else ""
        return f"**{self.title}**\n{self.description}\n```{self.language}\n{table}\n```\n{page_num}"


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

        message.append("```ini")
        message.append(
            "\n".join(f"[{i+1}] {v}" for i, v in enumerate(entries, start=offset))
        )
        message.append("```")

        friendly = "entries" if len(self.entries) > 1 else "entry"
        message.append(f"{len(self.entries)} {friendly} | Page {menu.current_page + 1}/{self.get_max_pages()}")

        if self.footer:
            message.append(self.footer)

        return "\n".join(message)


class EmbedPageSource(menus.ListPageSource):
    def __init__(self, entries, per_page, embed):
        super().__init__(entries, per_page=per_page)
        self.embed = embed
        self.original_description = embed.description or ""

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page

        formatted = "\n".join(
            f"`{i+1}.` {v}" for i, v in enumerate(entries, start=offset)
        )
        self.embed.description = self.original_description
        self.embed.description += "\n\n" + formatted

        page_num = f"Page {menu.current_page + 1}/{self.get_max_pages()}"

        friendly = "entries" if len(self.entries) > 1 else "entry"
        self.embed.set_footer(text=f"{len(self.entries)} {friendly} | {page_num}")

        return self.embed


class MenuPages(menus.Menu):
    """A special type of Menu dedicated to pagination.
    Attributes
    ------------
    current_page: :class:`int`
        The current page that we are in. Zero-indexed
        between [0, :attr:`PageSource.max_pages`).
    """

    def __init__(self, source, **kwargs):
        self._source = source
        self.current_page = 0
        super().__init__(**kwargs)

    @property
    def source(self):
        """:class:`PageSource`: The source where the data comes from."""
        return self._source

    async def change_source(self, source):
        """|coro|
        Changes the :class:`PageSource` to a different one at runtime.
        Once the change has been set, the menu is moved to the first
        page of the new source if it was started. This effectively
        changes the :attr:`current_page` to 0.
        Raises
        --------
        TypeError
            A :class:`PageSource` was not passed.
        """

        if not isinstance(source, menus.PageSource):
            raise TypeError(
                "Expected {0!r} not {1.__class__!r}.".format(menus.PageSource, source)
            )

        self._source = source
        self.current_page = 0
        if self.message is not None:
            await source._prepare_once()
            await self.show_page(0)

    def should_add_reactions(self):
        return self._source.is_paginating()

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(
            self._source.format_page, self, page
        )
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def show_page(self, page_number):
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        await self.message.edit(**kwargs)

    async def send_initial_message(self, ctx, channel):
        """|coro|
        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.
        This implementation shows the first page of the source.
        """
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        return await channel.send(**kwargs)

    async def start(self, ctx, *, channel=None, wait=False):
        await self._source._prepare_once()
        await super().start(ctx, channel=channel, wait=wait)

    async def show_checked_page(self, page_number):
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif max_pages > page_number >= 0:
                await self.show_page(page_number)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def show_current_page(self):
        if self._source.paginating:
            await self.show_page(self.current_page)

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        if max_pages is None:
            return True
        return max_pages <= 2

    @menus.button(
        WAY_BACK, position=menus.First(0), skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_first_page(self, payload):
        """go to the first page"""
        await self.show_page(0)

    @menus.button(BACK, position=menus.First(1))
    async def go_to_previous_page(self, payload):
        """go to the previous page"""
        await self.show_checked_page(self.current_page - 1)

    @menus.button(FORWARD, position=menus.Last(0))
    async def go_to_next_page(self, payload):
        """go to the next page"""
        await self.show_checked_page(self.current_page + 1)

    @menus.button(
        WAY_FOWARD, position=menus.Last(1), skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_last_page(self, payload):
        """go to the last page"""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(self._source.get_max_pages() - 1)

    @menus.button(STOP, position=menus.Last(2))
    async def stop_pages(self, payload):
        """stops the pagination session."""
        self.stop()


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
