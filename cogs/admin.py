import discord
from discord.ext import commands, menus

from datetime import datetime as d
import traceback
import psutil
import typing
import time
import io
from jishaku.codeblocks import codeblock_converter

from .utils.utils import TabularData
from .utils.menus import MenuPages
from .utils.human_time import plural
from .utils import colors, human_time


CLAM_DMS_CATEGORY = 714981398540451841


class ErrorSource(menus.ListPageSource):
    def __init__(self, entries, error_id):
        super().__init__(entries, per_page=9)
        self.error_id = error_id

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        message = f"**Page {menu.current_page + 1}/{self.get_max_pages()} \N{BULLET} Error {self.error_id}**```py\n"
        for i, line in enumerate(entries, start=offset):
            message += line
        message += "\n```"
        return message


class AllErrorsSource(menus.ListPageSource):
    def __init__(self, entries):
        super().__init__(entries, per_page=6)

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        em = discord.Embed(
            title=f"{len(self.entries)} Errors Cached", color=colors.PRIMARY
        )
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        description = []

        for i, error in enumerate(entries, start=offset):
            if str(error).startswith("Command raised an exception: "):
                e_formatted = str(error)[29:]
            else:
                e_formatted = str(error)
            description.append(f"`{len(self.entries) - 1 - i}.` {e_formatted}")

        em.description = "\n".join(description)

        return em


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

    # https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/admin.py#L353-L419
    @commands.command(hidden=True)
    async def sql(self, ctx, *, code: codeblock_converter):
        """Run some SQL."""
        # the imports are here because I imagine some people would want to use
        # this cog as a base for their other cog, and since this one is kinda
        # odd and unnecessary for most people, I will make it easy to remove
        # for those people.
        lang, query = code

        is_multistatement = query.count(";") > 1
        if is_multistatement:
            # fetch does not support multiple statements
            strategy = ctx.db.execute
        else:
            strategy = ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query)
            dt = (time.perf_counter() - start) * 1000.0
        except Exception:
            return await ctx.send(f"```py\n{traceback.format_exc()}\n```")

        rows = len(results)
        if is_multistatement or rows == 0:
            return await ctx.send(f"`{dt:.2f}ms: {results}`")

        headers = list(results[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = f"```\n{render}\n```\n*Returned {plural(rows):row} in {dt:.2f}ms*"
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(fp, "results.txt"))
        else:
            await ctx.send(fmt)

    @commands.command(hidden=True)
    async def sql_table(self, ctx, *, table_name: str):
        """Runs a query describing the table schema."""

        query = """SELECT column_name, data_type, column_default, is_nullable
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE table_name = $1
                """

        results = await ctx.db.fetch(query, table_name)

        headers = list(results[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = f"```\n{render}\n```"
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(fp, "results.txt"))
        else:
            await ctx.send(fmt)

    @commands.command(
        description="View or add someone to the blacklist",
        hidden=True,
        aliases=["block"],
    )
    async def blacklist(self, ctx, *, user: discord.User = None):
        if not user:
            blacklist = self.bot.blacklist

            if not blacklist:
                return await ctx.send("No blacklisted users")

            pages = ctx.pages(blacklist, title="Blacklisted users")
            return await pages.start(ctx)

        if user == ctx.author:
            return await ctx.send("Don't blacklist yourself! That'd be a real pain.")

        if str(user.id) in self.bot.blacklist:
            return await ctx.send("That user is already blacklisted.")

        self.bot.add_to_blacklist(user)

        await ctx.send(ctx.tick(True, f"Added **`{user}`** to the blacklist."))

    @commands.command(
        description="Remove someone from the blacklist",
        hidden=True,
        aliases=["unblock"],
    )
    async def unblacklist(self, ctx, user_id: int):
        if str(user_id) not in self.bot.blacklist:
            return await ctx.send("That user isn't blacklisted.")

        self.bot.remove_from_blacklist(user_id)

        user = self.bot.get_user(user_id)

        if not user:
            human_friendly = f"Removed user with ID **`{user_id}`** from the blacklist."

        else:
            human_friendly = f"Removed user **`{user}`** from the blacklist."

        await ctx.send(ctx.tick(True, human_friendly))

    @commands.command(
        description="Temporarily blacklist a user", hidden=True, aliases=["tempblock"]
    )
    async def tempblacklist(
        self, ctx, user: discord.User, duration: human_time.FutureTime
    ):
        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, that functionality isn't available right now. Try again later."
            )

        if user == ctx.author:
            return await ctx.send("Don't blacklist yourself! That'd be a real pain.")

        if str(user.id) not in self.bot.blacklist:
            self.bot.add_to_blacklist(user)

        timer = await timers.create_timer(duration.dt, "tempblacklist", user.id)

        friendly_time = human_time.human_timedelta(duration.dt, source=ctx.message.created_at)
        await ctx.send(
            ctx.tick(True, f"Blacklisted user `{user}` for {friendly_time}.")
        )

    @commands.Cog.listener()
    async def on_tempblacklist_timer_complete(self, timer):
        user_id = timer.args[0]

        self.bot.remove_from_blacklist(user_id)

        user = self.bot.get_user(user_id)

        if not user:
            human_friendly = f"Removed tempblacklisted user with ID **`{user_id}`** from the blacklist."

        else:
            human_friendly = (
                f"Removed tempblacklisted user **`{user}`** from the blacklist."
            )

        human_friendly += f"\nOriginally tempblacklisted {human_time.human_timedelta(timer.created_at)}."

        em = discord.Embed(
            title="Tempblacklist Expiration",
            timestamp=timer.created_at,
            color=discord.Color.green(),
        )
        value = f"{user} (ID: {user.id})" if user else f"User with ID {user_id}"
        em.add_field(name="User", value=value, inline=False)
        em.add_field(
            name="Originally tempblacklisted",
            value=human_time.human_timedelta(timer.created_at),
        )
        em.set_footer(text="Blacklist date")

        if user:
            em.set_thumbnail(url=user.avatar_url)

        console = self.bot.console
        await console.send(embed=em)

    @commands.command(
        name="reload", description="Reload an extension", aliases=["load"], hidden=True,
    )
    @commands.is_owner()
    async def _reload(self, ctx, *, cog="all"):
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
                        f"**{ctx.tick(False)} Extension `{ext}` not loaded.**\n"
                        f"```py\n{traceback_data}```\n\n"
                    )
                    traceback.print_exception(type(e), e, e.__traceback__)
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
                f"**{ctx.tick(False)} Extension `{cog.lower()}` not loaded.**\n```py\n{traceback_data}```"
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
        em = discord.Embed(title="Current Process Stats", color=discord.Color.teal(),)
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
        first_step = list(self.bot.error_cache)
        errors = first_step[::-1]
        pages = MenuPages(source=AllErrorsSource(errors), clear_reactions_after=True,)
        await pages.start(ctx)

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
        pages = MenuPages(
            source=ErrorSource(lines, len(self.bot.error_cache) - 1),
            clear_reactions_after=True,
        )
        await pages.start(ctx)

    @_error.command(aliases=["i", "find", "get", "search"])
    @commands.is_owner()
    async def index(self, ctx, index: int):
        if len(self.bot.error_cache) == 0:
            return await ctx.send("No previous errors cached.")
        try:
            e = self.bot.error_cache[index]
        except IndexError:
            return await ctx.send(ctx.tick(False, f"There is no error at that index."))
        etype = type(e)
        trace = e.__traceback__
        verbosity = 4
        lines = traceback.format_exception(etype, e, trace, verbosity)
        pages = MenuPages(source=ErrorSource(lines, index), clear_reactions_after=True,)
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
                return await ctx.send(ctx.tick(False, f"I couldn't find that user."))
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
            return await ctx.send(
                f"{ctx.tick(False)} You must be in a DM session to invoke this command."
            )
        await ctx.dm_session.close()
        self.dm_sessions.pop(ctx.dm_session.channel.id)

    @close.command(name="all", description="Close all DM session")
    async def close_all(self, ctx):
        for dm_session in self.dm_sessions.values():
            await dm_session.close()
        num_sessions = len(self.dm_sessions)
        self.bot.dm_sessions = {}
        await ctx.send(f"{ctx.tick(True)} Closed {num_sessions} DM session(s)")

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

        if (
            message.content.startswith("!") or message.content.startswith("c.")
        ) and message.author.id == self.bot.owner_id:
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
