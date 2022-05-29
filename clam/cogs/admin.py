import asyncio
import datetime
import importlib
import io
import time
import os
import re
import subprocess
import sys
import traceback
import typing

import discord
import pkg_resources
import psutil
from discord.ext import commands, menus, tasks
from jishaku.codeblocks import codeblock_converter
from jishaku.features.root_command import natural_size

from clam.utils import aiopypi, colors, humantime
from clam.utils.emojis import OK_SIGN
from clam.utils.formats import plural, TabularData
from clam.utils.menus import MenuPages


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
    """Bot admin commands and features."""

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

    @commands.command()
    async def eval(self, ctx, *, argument: codeblock_converter):
        """Alias for `{prefix}jishaku python`. Runs some Python code."""

        jishaku = self.bot.get_cog("Jishaku")

        if not jishaku:
            return await ctx.send("Jishaku is not loaded.")

        await ctx.invoke(jishaku.jsk_python, argument=argument)

    # https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/admin.py#L353-L419
    @commands.command()
    async def sql(self, ctx, *, code: codeblock_converter):
        """Runs some SQL."""

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

    @commands.command()
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

    @commands.command(aliases=["block"])
    async def blacklist(self, ctx, *, user: discord.User = None):
        """Shows the blacklist or adds someone to the blacklist."""

        if not user:
            blacklist = self.bot.blacklist

            if not blacklist:
                return await ctx.send("No blacklisted users")

            pages = ctx.pages(blacklist, title="Blacklisted users")
            return await pages.start()

        if user == ctx.author:
            return await ctx.send("Don't blacklist yourself! That'd be a real pain.")

        if str(user.id) in self.bot.blacklist:
            return await ctx.send("That user is already blacklisted.")

        self.bot.add_to_blacklist(user)

        await ctx.send(ctx.tick(True, f"Added **`{user}`** to the blacklist."))

    @commands.command(aliases=["unblock"])
    async def unblacklist(self, ctx, user_id: int):
        """Removes someone from the blacklist."""

        if str(user_id) not in self.bot.blacklist:
            return await ctx.send("That user isn't blacklisted.")

        try:
            self.bot.remove_from_blacklist(user_id)
        except ValueError:
            return await ctx.send("For some reason I couldn't index that user ID. Try again?")

        user = self.bot.get_user(user_id)

        if not user:
            human_friendly = f"Removed user with ID **`{user_id}`** from the blacklist."

        else:
            human_friendly = f"Removed user **`{user}`** from the blacklist."

        await ctx.send(ctx.tick(True, human_friendly))

    @commands.command(aliases=["tempblock"])
    async def tempblacklist(self, ctx, user: discord.User, duration: humantime.FutureTime):
        """Temporarily blacklists a user."""

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

        friendly_time = humantime.timedelta(
            duration.dt, source=ctx.message.created_at
        )
        await ctx.send(
            ctx.tick(True, f"Blacklisted user `{user}` for {friendly_time}.")
        )

    @commands.Cog.listener()
    async def on_tempblacklist_timer_complete(self, timer):
        user_id = timer.args[0]

        if str(user_id) not in self.bot.blacklist:
            return

        self.bot.remove_from_blacklist(user_id)

        user = self.bot.get_user(user_id)

        if not user:
            human_friendly = f"Removed tempblacklisted user with ID **`{user_id}`** from the blacklist."

        else:
            human_friendly = (
                f"Removed tempblacklisted user **`{user}`** from the blacklist."
            )

        human_friendly += f"\nOriginally tempblacklisted {humantime.timedelta(timer.created_at)}."

        em = discord.Embed(
            title="Tempblacklist Expiration",
            timestamp=timer.created_at,
            color=discord.Color.green(),
        )
        value = f"{user} (ID: {user.id})" if user else f"User with ID {user_id}"
        em.add_field(name="User", value=value, inline=False)
        em.add_field(
            name="Originally tempblacklisted",
            value=humantime.timedelta(timer.created_at),
        )
        em.set_footer(text="Blacklist date")

        if user:
            em.set_thumbnail(url=user.display_avatar.url)

        console = self.bot.console
        await console.send(embed=em)

    @commands.group(name="reload", aliases=["load"], invoke_without_command=True)
    @commands.is_owner()
    async def _reload(self, ctx, *, cog):
        """Reloads an extension."""

        extension = f"clam.cogs.{cog.lower()}"

        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"{OK_SIGN}")
            self.log.info(f"Extension '{cog}' successfully reloaded.")
        except Exception as e:
            traceback_data = "".join(
                traceback.format_exception(type(e), e, e.__traceback__, 1)
            )
            await ctx.send(
                f"**{ctx.tick(False)} Extension `{cog}` not loaded.**\n```py\n{traceback_data}```"
            )
            self.log.warning(
                f"Extension 'cogs.{cog}' not loaded.\n{traceback_data}"
            )

    # https://github.com/Rapptz/RoboDanny/blob/6211293d8fe19ad46a266ded2464752935a3fb94/cogs/admin.py#L89-L97
    async def run_process(self, command):
        try:
            process = await asyncio.create_subprocess_shell(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            result = await process.communicate()
        except NotImplementedError:
            process = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            result = await self.bot.loop.run_in_executor(None, process.communicate)

        return [output.decode() for output in result]

    # https://github.com/Rapptz/RoboDanny/blob/6211293d8fe19ad46a266ded2464752935a3fb94/cogs/admin.py#L146-L214
    _GIT_PULL_REGEX = re.compile(r"\s*(?P<filename>.+?)\s*\|\s*[0-9]+\s*[+-]+")

    def find_modules_from_git(self, output):
        files = self._GIT_PULL_REGEX.findall(output)
        ret = []
        for file in files:
            root, ext = os.path.splitext(file)
            if ext != ".py":
                continue

            if root.startswith("clam/cogs/"):
                # A submodule is a directory inside the main cog directory for
                # my purposes
                ret.append((root.count("/") - 2, root.replace("/", ".")))

        # For reload order, the submodules should be reloaded first
        ret.sort(reverse=True)
        return ret

    async def reload_or_load_extension(self, module):
        try:
            await self.bot.reload_extension(module)
        except commands.ExtensionNotLoaded:
            await self.bot.load_extension(module)

    @_reload.command(name="all")
    async def _reload_all(self, ctx):
        """Reloads all modules, while pulling from git."""

        async with ctx.typing():
            stdout, stderr = await self.run_process("git pull")

        # progress and stuff is redirected to stderr in git pull
        # however, things like "fast forward" and files
        # along with the text "already up-to-date" are in stdout

        if stdout.startswith("Already up-to-date."):
            return await ctx.send(stdout)

        modules = self.find_modules_from_git(stdout)

        if not modules:
            return await ctx.send("No modules need to be updated.")

        mods_text = "\n".join(
            f"{index}. `{module}`" for index, (_, module) in enumerate(modules, start=1)
        )
        prompt_text = (
            f"This will update the following modules, are you sure?\n{mods_text}"
        )
        confirm = await ctx.confirm(prompt_text)
        if not confirm:
            return await ctx.send("Aborting.")

        statuses = []
        for is_submodule, module in modules:
            if is_submodule:
                try:
                    actual_module = sys.modules[module]
                except KeyError:
                    statuses.append(("\N{SLEEPING SYMBOL}", module))
                else:
                    try:
                        importlib.reload(actual_module)
                    except Exception as e:
                        traceback_data = "".join(
                            traceback.format_exception(type(e), e, e.__traceback__, 1)
                        )
                        statuses.append(
                            (ctx.tick(False), f"{module}\n```py\n{traceback_data}\n```")
                        )
                    else:
                        statuses.append((ctx.tick(True), module))
            else:
                try:
                    await self.reload_or_load_extension(module)
                except commands.ExtensionError as e:
                    traceback_data = "".join(
                        traceback.format_exception(type(e), e, e.__traceback__, 1)
                    )
                    statuses.append(
                        (ctx.tick(False), f"{module}\n```py\n{traceback_data}\n```")
                    )
                else:
                    statuses.append((ctx.tick(True), module))

        await ctx.send("\n".join(f"{status} `{module}`" for status, module in statuses))

    def readable(self, value):
        gigs = round(value // 1000000000)
        if gigs <= 0:
            megs = round(value // 1000000)
            return f"{megs}mb"
        return f"{gigs}gb"

    @commands.command(aliases=["process"])
    @commands.is_owner()
    async def host(self, ctx):
        """Shows stats about the host."""

        em = discord.Embed(
            title="Host Stats",
            color=discord.Color.teal(),
        )
        em.add_field(
            name="CPU",
            value=f"{psutil.cpu_percent()}% used with {plural(psutil.cpu_count()):CPU}",
        )
        mem = psutil.virtual_memory()
        em.add_field(
            name="Memory",
            value=f"{mem.percent}% used\n{natural_size(mem.used)}/{natural_size(mem.total)}",
        )
        disk = psutil.disk_usage("/")
        em.add_field(
            name="Disk",
            value=f"{disk.percent}% used\n{natural_size(disk.used)}/{natural_size(disk.total)}",
        )
        uptime = datetime.datetime.fromtimestamp(psutil.boot_time())
        em.add_field(
            name="Boot Time",
            value=f"{humantime.timedelta(uptime)}",
            inline=False,
        )

        await ctx.send(embed=em)

    @commands.group(name="error", aliases=["e"], invoke_without_command=True)
    @commands.is_owner()
    async def _error(self, ctx):
        first_step = list(self.bot.error_cache)
        errors = first_step[::-1]
        pages = MenuPages(AllErrorsSource(errors), ctx=ctx)
        await pages.start()

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
        pages = MenuPages(ErrorSource(lines, len(self.bot.error_cache) - 1), ctx=ctx)
        await pages.start()

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
        pages = MenuPages(ErrorSource(lines, index), ctx=ctx)
        await pages.start()

    @commands.command(aliases=["shutdown"])
    @commands.is_owner()
    async def logout(self, ctx):
        """Shuts down the bot."""

        await ctx.send("Logging out :wave:")
        await self.bot.close()

    @commands.group(aliases=["dms"], invoke_without_command=True)
    @commands.is_owner()
    async def dm(self, ctx):
        """Manages DMs with the bot."""

        await ctx.invoke(self.dm_all)

    @dm.command(name="all")
    @commands.is_owner()
    async def dm_all(self, ctx):
        """Shows all DM sessions."""

        if not self.dm_sessions:
            return await ctx.send("No active DMs.")
        dms = "Current active DMs:"
        for dm in self.dm_sessions:
            dms += f"\n{dm.user}"
        await ctx.send(dms)

    @dm.command()
    @commands.is_owner()
    async def create(self, ctx, user: typing.Union[discord.User, int]):
        """Starts a new DM session with a user."""

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

    @dm.group(invoke_without_command=True)
    @commands.is_owner()
    async def close(self, ctx):
        """Closes a DM session with a user."""

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

    @dm.command(name="reply", aliases=["send"])
    async def dm_reply(self, ctx, user: discord.User, *, message):
        """Replies to a DM."""

        try:
            await user.send(message)
        except discord.Forbidden:
            return await ctx.send("Could not send message.")

        channel = self.bot.get_channel(679841169248747696)
        em = discord.Embed(
            description=message,
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow(),
        )
        em.set_author(
            name=f"To: {user} ({user.id})",
            icon_url=user.display_avatar.url,
        )
        em.set_footer(text="Outgoing DM")
        await channel.send(embed=em)

        if ctx.channel != channel:
            await ctx.send(ctx.tick(True, "Sent DM"))

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
            timestamp=datetime.datetime.utcnow(),
        )
        em.set_author(
            name=f"To: {dm_session.user} ({dm_session.user.id})",
            icon_url=dm_session.user.display_avatar.url,
        )
        em.set_footer(text="Outgoing DM")
        return await channel.send(embed=em)

    @commands.Cog.listener("on_message")
    async def dm_listener(self, message):
        if not isinstance(message.channel, discord.DMChannel) or message.author.bot:
            return

        if message.content.startswith(("c.", "!", "?")):
            return

        channel = self.bot.get_channel(679841169248747696)
        em = discord.Embed(
            description=message.clean_content,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow(),
        )
        em.set_author(
            name=f"From: {message.author} ({message.author.id})",
            icon_url=message.author.display_avatar.url,
        )
        em.set_footer(text="Incoming DM")

        if message.attachments:
            em.set_image(url=message.attachments[0].url)

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

        await dm_session.user.typing()

    @commands.Cog.listener("on_typing")
    async def typing_recieve(self, channel, user, when):
        dm_session = self.find_session_from_user(user)

        if not dm_session:
            return
        if user.bot:
            return

        await dm_session.channel.typing()


async def setup(bot):
    await bot.add_cog(Admin(bot))
