import asyncio
import datetime
from typing import Optional, Dict, Any

import discord
from discord.ext import commands, menus

from .emojis import WAY_BACK, BACK, FORWARD, WAY_FOWARD, STOP, GREEN_TICK, RED_TICK
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


# Sourced from Rapptz/RoboDanny: https://github.com/Rapptz/RoboDanny/blob/9f0b799f979f3bf4c6aa32de3d16d1547ab84edd/cogs/utils/context.py#L28-L65
class ConfirmView(discord.ui.View):
    def __init__(self, *, timeout: float, author_id: int, ctx, delete_after: bool) -> None:
        super().__init__(timeout=timeout)
        self.value: Optional[bool] = None
        self.delete_after: bool = delete_after
        self.author_id: int = author_id
        self.ctx = ctx
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        else:
            await interaction.response.send_message("This confirmation dialog is not for you.", ephemeral=True)
            return False

    async def on_timeout(self) -> None:
        if self.delete_after and self.message:
            await self.message.delete()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.value = True
        await interaction.response.defer()
        if self.delete_after:
            await interaction.delete_original_message()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.value = False
        await interaction.response.defer()
        if self.delete_after:
            await interaction.delete_original_message()
        self.stop()


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


# Sourced from Rapptz/RoboDanny: https://github.com/Rapptz/RoboDanny/blob/833c9c31d0b00dc696b064b33b39a404d9dda88d/cogs/utils/paginator.py#L11-L198
class MenuPages(discord.ui.View):
    def __init__(
        self,
        source: menus.PageSource,
        *,
        ctx: commands.Context,
        check_embeds: bool = True,
        compact: bool = False,
    ):
        super().__init__()
        self.source: menus.PageSource = source
        self.check_embeds: bool = check_embeds
        self.ctx: commands.Context = ctx
        self.message: Optional[discord.Message] = None
        self.current_page: int = 0
        self.compact: bool = compact
        self.input_lock = asyncio.Lock()
        self.clear_items()
        self.fill_items()

    def fill_items(self) -> None:
        if not self.compact:
            self.numbered_page.row = 1
            self.stop_pages.row = 1

        if self.source.is_paginating():
            max_pages = self.source.get_max_pages()
            use_last_and_first = max_pages is not None and max_pages >= 2
            if use_last_and_first:
                self.add_item(self.go_to_first_page)  # type: ignore
            self.add_item(self.go_to_previous_page)  # type: ignore
            if not self.compact:
                self.add_item(self.go_to_current_page)  # type: ignore
            self.add_item(self.go_to_next_page)  # type: ignore
            if use_last_and_first:
                self.add_item(self.go_to_last_page)  # type: ignore
            if not self.compact:
                self.add_item(self.numbered_page)  # type: ignore
            self.add_item(self.stop_pages)  # type: ignore

    async def _get_kwargs_from_page(self, page: int) -> Dict[str, Any]:
        value = await discord.utils.maybe_coroutine(self.source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {'content': value, 'embed': None}
        elif isinstance(value, discord.Embed):
            return {'embed': value, 'content': None}
        else:
            return {}

    async def show_page(self, interaction: discord.Interaction, page_number: int) -> None:
        page = await self.source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        self._update_labels(page_number)
        if kwargs:
            if interaction.response.is_done():
                if self.message:
                    await self.message.edit(**kwargs, view=self)
            else:
                await interaction.response.edit_message(**kwargs, view=self)

    def _update_labels(self, page_number: int) -> None:
        self.go_to_first_page.disabled = page_number == 0
        if self.compact:
            max_pages = self.source.get_max_pages()
            self.go_to_last_page.disabled = max_pages is None or (page_number + 1) >= max_pages
            self.go_to_next_page.disabled = max_pages is not None and (page_number + 1) >= max_pages
            self.go_to_previous_page.disabled = page_number == 0
            return

        self.go_to_current_page.label = str(page_number + 1)
        self.go_to_previous_page.label = str(page_number)
        self.go_to_next_page.label = str(page_number + 2)
        self.go_to_next_page.disabled = False
        self.go_to_previous_page.disabled = False
        self.go_to_first_page.disabled = False

        max_pages = self.source.get_max_pages()
        if max_pages is not None:
            self.go_to_last_page.disabled = (page_number + 1) >= max_pages
            if (page_number + 1) >= max_pages:
                self.go_to_next_page.disabled = True
                self.go_to_next_page.label = '…'
            if page_number == 0:
                self.go_to_previous_page.disabled = True
                self.go_to_previous_page.label = '…'

    async def show_checked_page(self, interaction: discord.Interaction, page_number: int) -> None:
        max_pages = self.source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(interaction, page_number)
            elif max_pages > page_number >= 0:
                await self.show_page(interaction, page_number)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id in (self.ctx.bot.owner_id, self.ctx.author.id):
            return True
        await interaction.response.send_message('This pagination menu cannot be controlled by you, sorry!', ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.edit(view=None)

    async def on_error(self, error: Exception, item: discord.ui.Item, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            await interaction.followup.send('An unknown error occurred, sorry', ephemeral=True)
        else:
            await interaction.response.send_message('An unknown error occurred, sorry', ephemeral=True)

    async def start(self) -> None:
        if self.check_embeds and not self.ctx.channel.permissions_for(self.ctx.me).embed_links:
            await self.ctx.send('Bot does not have embed links permission in this channel.')
            return

        await self.source._prepare_once()
        page = await self.source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        self._update_labels(0)
        self.message = await self.ctx.send(**kwargs, view=self)

    @discord.ui.button(label='≪', style=discord.ButtonStyle.grey)
    async def go_to_first_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the first page"""
        await self.show_page(interaction, 0)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.blurple)
    async def go_to_previous_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the previous page"""
        await self.show_checked_page(interaction, self.current_page - 1)

    @discord.ui.button(label='Current', style=discord.ButtonStyle.grey, disabled=True)
    async def go_to_current_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        pass

    @discord.ui.button(label='Next', style=discord.ButtonStyle.blurple)
    async def go_to_next_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the next page"""
        await self.show_checked_page(interaction, self.current_page + 1)

    @discord.ui.button(label='≫', style=discord.ButtonStyle.grey)
    async def go_to_last_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the last page"""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(interaction, self.source.get_max_pages() - 1)

    @discord.ui.button(label='Skip to page...', style=discord.ButtonStyle.grey)
    async def numbered_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """lets you type a page number to go to"""
        if self.input_lock.locked():
            await interaction.response.send_message('Already waiting for your response...', ephemeral=True)
            return

        if self.message is None:
            return

        async with self.input_lock:
            channel = self.message.channel
            author_id = interaction.user and interaction.user.id
            await interaction.response.send_message('What page do you want to go to?', ephemeral=True)

            def message_check(m):
                return m.author.id == author_id and channel == m.channel and m.content.isdigit()

            try:
                msg = await self.ctx.bot.wait_for('message', check=message_check, timeout=30.0)
            except asyncio.TimeoutError:
                await interaction.followup.send('Took too long.', ephemeral=True)
                await asyncio.sleep(5)
            else:
                page = int(msg.content)
                await msg.delete()
                await self.show_checked_page(interaction, page - 1)

    @discord.ui.button(label='Quit', style=discord.ButtonStyle.red)
    async def stop_pages(self, button: discord.ui.Button, interaction: discord.Interaction):
        """stops the pagination session."""
        await interaction.response.defer()
        await interaction.delete_original_message()
        self.stop()


class BasicPages(MenuPages):
    def __init__(self, entries, per_page, embed=None, ctx=None, **paginator_kwargs):
        if embed:
            super().__init__(
                EmbedPageSource(entries, per_page, embed), ctx=ctx,
            )

        else:
            super().__init__(
                BasicPageSource(entries, per_page=per_page, **paginator_kwargs), ctx=ctx
            )
