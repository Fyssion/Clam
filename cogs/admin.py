import discord
from discord.ext import commands, menus

from datetime import datetime as d
import traceback
import json
import psutil
import typing

from .utils.menus import MenuPages


CLAM_DMS_CATEGORY = 714981398540451841


class ErrorSource(menus.ListPageSource):
    def __init__(self, entries):
        super().__init__(entries, per_page=9)

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        message = f"**Page {menu.current_page + 1}/{self.get_max_pages()}**```py\n"
        for i, line in enumerate(entries, start=offset):
            message += line
        message += "\n```"
        return message


class DMSession:
    def __init__(self, user, channel):
        super().__init__()
        self.user = user
        self.channel = channel
        self.is_closed = False

    async def send(self, *args, **kwargs):
        return await self.user.send(*args, **kwargs)

    async def close(self):
        await self.channel.delete(reason="Closing DM Session")
        self.is_closed = True


class Admin(commands.Cog):
    """Admin commands and features"""

    def __init__(self, bot):
        self.bot = bot
        self.hidden = True
        self.log = self.bot.log

        if not hasattr(self.bot, "dm_sessions"):
            # channel_id: DMSession
            self.bot.dm_sessions = {}

        self.dm_sessions = self.bot.dm_sessions

    def get_dm_session(self, channel):
        if channel.id in self.dm_sessions.keys():
            dm_session = self.dm_sessions[channel.id]
        else:
            dm_session = None
        return dm_session

    def find_session_from_user(self, user):
        for dm_session in self.dm_sessions.values():
            if dm_session.user.id == user.id:
                return dm_session
        return None

    async def cog_before_invoke(self, ctx):
        ctx.dm_session = self.get_dm_session(ctx.channel)

    async def cog_check(self, ctx):
        if not await commands.is_owner().predicate(ctx):
            raise commands.NotOwner("You do not own this bot.")
        return True

    @commands.command(
        name="reload",
        description="Reload an extension",
        aliases=["load"],
        usage="[cog]",
        hidden=True,
    )
    @commands.is_owner()
    async def _reload(self, ctx, cog="all"):
        if cog == "all":
            msg = ""

            for ext in self.bot.cogs_to_load:
                try:
                    self.bot.reload_extension(ext)
                    msg += (
                        f"**<a:cool_ok_sign:699837382433701998> Reloaded** `{ext}`\n\n"
                    )
                    self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")

                except Exception as e:
                    traceback_data = "".join(
                        traceback.format_exception(type(e), e, e.__traceback__, 1)
                    )
                    msg += (
                        f"**:warning: Extension `{ext}` not loaded.**\n"
                        f"```py\n{traceback_data}```\n\n"
                    )
                    self.log.warning(
                        f"Extension 'cogs.{cog.lower()}' not loaded.\n"
                        f"{traceback_data}"
                    )
            return await ctx.send(msg)

        try:
            self.bot.reload_extension(cog.lower())
            await ctx.send(f"<a:cool_ok_sign:699837382433701998>")
            self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")
        except Exception as e:
            traceback_data = "".join(
                traceback.format_exception(type(e), e, e.__traceback__, 1)
            )
            await ctx.send(
                f"**:warning: Extension `{cog.lower()}` not loaded.**\n```py\n{traceback_data}```"
            )
            self.log.warning(
                f"Extension 'cogs.{cog.lower()}' not loaded.\n{traceback_data}"
            )

    @commands.group(name="cog")
    @commands.is_owner()
    async def _cog(self, ctx):
        pass

    @_cog.command(name="reload")
    @commands.is_owner()
    async def _add_cog(self, ctx, cog):
        self.bot.add_cog(cog)
        self.bot.cogs_to_load.append(cog)
        self.bot.ordered_cogs.append(self.bot.cogs.keys()[-1])
        return await ctx.send("Cog added.")

    def readable(self, value):
        gigs = round(value // 1000000000)
        if gigs <= 0:
            megs = round(value // 1000000)
            return f"{megs}mb"
        return f"{gigs}gb"

    @commands.group(
        name="process", hidden=True, aliases=["computer", "comp", "cpu", "ram"]
    )
    @commands.is_owner()
    async def _process(self, ctx):
        em = discord.Embed(
            title="Current Process Stats",
            color=discord.Color.teal(),
            timestamp=d.utcnow(),
        )
        em.add_field(
            name="CPU",
            value=f"{psutil.cpu_percent()}% used with {psutil.cpu_count()} CPU(s)",
        )
        mem = psutil.virtual_memory()
        em.add_field(
            name="Virtual Memory",
            value=f"{mem.percent}% used\n{self.readable(mem.used)}/{self.readable(mem.total)}",
        )
        disk = psutil.disk_usage("/")
        em.add_field(
            name="Disk",
            value=f"{disk.percent}% used\n{self.readable(disk.used)}/{self.readable(disk.total)}",
        )

        await ctx.send(embed=em)

    @commands.group(
        name="error", hidden=True, aliases=["e"], invoke_without_command=True,
    )
    @commands.is_owner()
    async def _error(self, ctx):
        cache_len = len(self.bot.error_cache)
        await ctx.send(f"I have **{cache_len}** cached errors.")

    @_error.command(aliases=["pre", "p", "prev"])
    @commands.is_owner()
    async def previous(self, ctx):
        try:
            e = self.bot.error_cache[len(self.bot.error_cache) - 1]
        except IndexError:
            return await ctx.send("No previous errors cached.")
        etype = type(e)
        trace = e.__traceback__
        verbosity = 4
        lines = traceback.format_exception(etype, e, trace, verbosity)
        pages = MenuPages(source=ErrorSource(lines), clear_reactions_after=True,)
        await pages.start(ctx)

    @_error.command(aliases=["i", "find", "get", "search"], usage="[index]")
    @commands.is_owner()
    async def index(self, ctx, i: int):
        if len(self.bot.error_cache) == 0:
            return await ctx.send("No previous errors cached.")
        try:
            e = self.bot.error_cache[i]
        except IndexError:
            return await ctx.send("There is no error at that index.")
        etype = type(e)
        trace = e.__traceback__
        verbosity = 4
        lines = traceback.format_exception(etype, e, trace, verbosity)
        pages = MenuPages(source=ErrorSource(lines), clear_reactions_after=True,)
        await pages.start(ctx)

    @commands.command(
        name="logout", description="Logs out and shuts down bot", hidden=True
    )
    @commands.is_owner()
    async def logout_command(self, ctx):
        self.log.info("Logging out of Discord.")
        await ctx.send("Logging out :wave:")
        await self.bot.session.close()
        await self.bot.logout()

    @commands.group(
        description="DMs with the bot", aliases=["dms"], invoke_without_command=True
    )
    @commands.is_owner()
    async def dm(self, ctx):
        await ctx.invoke(self.all_dms)

    @dm.command(name="all", description="View all current DMs.")
    @commands.is_owner()
    async def all_dms(self, ctx):
        if not self.dm_sessions:
            return await ctx.send("No active DMs.")
        dms = "Current active DMs:"
        for dm in self.dm_sessions:
            dms += f"\n{dm.user}"
        await ctx.send(dms)

    @dm.command(
        description="Create a new DM session with a user.", aliases=["new", "start"]
    )
    @commands.is_owner()
    async def create(self, ctx, user: typing.Union[discord.User, int]):
        if type(user) == int:
            user = self.bot.get_user(user)
            if not user:
                return await ctx.send("I couldn't find that user.")
        category = ctx.guild.get_channel(CLAM_DMS_CATEGORY)
        channel = await category.create_text_channel(
            name=str(user), reason="Create DM session"
        )
        dm_session = DMSession(user, channel)
        self.dm_sessions[channel.id] = dm_session

    @dm.group(
        description="Close a DM session with a user.",
        aliases=["delete", "stop", "remove"],
        invoke_without_command=True,
    )
    @commands.is_owner()
    async def close(self, ctx):
        if not ctx.dm_session:
            return await ctx.send("You must be in a DM session to invoke this command.")
        await ctx.dm_session.close()
        self.dm_sessions.pop(ctx.dm_session.channel.id)

    @close.command(name="all", description="Close all DM session")
    async def close_all(self, ctx):
        for dm_session in self.dm_sessions.values():
            await dm_session.close()
        num_sessions = len(self.dm_sessions)
        self.bot.dm_sessions = {}
        await ctx.send(f"Closed {num_sessions} DM session(s)")

    @commands.Cog.listener("on_message")
    async def dm_sender(self, message):
        dm_session = self.get_dm_session(message.channel)

        if not dm_session:
            return
        if message.author.bot:
            return
        if message.content.startswith(self.bot.guild_prefix(message.guild)):
            return

        try:
            await dm_session.send(message.content)
        except discord.Forbidden:
            return await dm_session.channel.send("Could not send message.")

        channel = self.bot.get_channel(679841169248747696)
        em = discord.Embed(
            description=message.clean_content,
            color=discord.Color.red(),
            timestamp=d.utcnow(),
        )
        em.set_author(
            name=f"To: {dm_session.user} ({dm_session.user.id})",
            icon_url=dm_session.user.avatar_url,
        )
        em.set_footer(text="Outgoing DM")
        return await channel.send(embed=em)

    @commands.Cog.listener("on_message")
    async def dm_listener(self, message):
        if not isinstance(message.channel, discord.DMChannel) or message.author.bot:
            return

        channel = self.bot.get_channel(679841169248747696)
        em = discord.Embed(
            description=message.clean_content,
            color=discord.Color.blue(),
            timestamp=d.utcnow(),
        )
        em.set_author(
            name=f"From: {message.author} ({message.author.id})",
            icon_url=message.author.avatar_url,
        )
        em.set_footer(text="Incoming DM")
        await channel.send(embed=em)

        dm_session = self.find_session_from_user(message.author)
        if not dm_session:
            return

        await dm_session.channel.send(f"{dm_session.user}: {message.content}")

    @commands.Cog.listener("on_typing")
    async def typing_send(self, channel, user, when):
        dm_session = self.get_dm_session(channel)

        if not dm_session:
            return
        if user.bot:
            return

        await dm_session.user.trigger_typing()

    @commands.Cog.listener("on_typing")
    async def typing_recieve(self, channel, user, when):
        dm_session = self.find_session_from_user(user)

        if not dm_session:
            return
        if user.bot:
            return

        await dm_session.channel.trigger_typing()


def setup(bot):
    bot.add_cog(Admin(bot))
