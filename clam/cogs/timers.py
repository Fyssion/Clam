# SOURCE: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/reminder.py

"""
The MIT License (MIT)

Copyright (c) 2017 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import asyncio
import datetime
import textwrap

import asyncpg
import discord
from discord.ext import commands, tasks, menus

from .utils import colors, db, humantime
from .utils.formats import plural
from .utils.menus import MenuPages


class TimersTable(db.Table, table_name="timers"):
    id = db.PrimaryKeyColumn()

    expires = db.Column(db.Datetime, index=True)
    created = db.Column(db.Datetime, default="now() at time zone 'utc'")
    event = db.Column(db.String)
    extra = db.Column(db.JSON, default="'{}'::jsonb")


class TimerPageSource(menus.ListPageSource):
    def __init__(self, entries):
        super().__init__(entries, per_page=10)

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        em = discord.Embed(
            title="Your Reminders",
            description=f"Total reminders: **{len(self.entries)}**\n\nReminders:\n",
            color=colors.PRIMARY,
        )

        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        for i, (_id, expires, message) in enumerate(entries, start=offset):
            shorten = textwrap.shorten(message, width=512)
            em.add_field(
                name=f"`ID: {_id}` {humantime.timedelta(expires)}",
                value=shorten or "No message",
                inline=False,
            )

        return em


class Timer:
    __slots__ = ("args", "kwargs", "event", "id", "created_at", "expires")

    def __init__(self, *, record):
        self.id = record["id"]

        extra = record["extra"]
        self.args = extra.get("args", [])
        self.kwargs = extra.get("kwargs", {})
        self.event = record["event"]
        self.created_at = record["created"]
        self.expires = record["expires"]

    @classmethod
    def temporary(cls, *, expires, created, event, args, kwargs):
        pseudo = {
            "id": None,
            "extra": {"args": args, "kwargs": kwargs},
            "event": event,
            "created": created,
            "expires": expires,
        }
        return cls(record=pseudo)

    def __eq__(self, other):
        try:
            return self.id == other.id
        except AttributeError:
            return False

    def __hash__(self):
        return hash(self.id)

    @property
    def human_delta(self):
        return humantime.timedelta(self.created_at)

    def __repr__(self):
        return f"<Timer created={self.created_at} expires={self.expires} event={self.event}>"


class Timers(commands.Cog):
    """Timers helper cog"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":alarm_clock:"

        self._have_data = asyncio.Event(loop=bot.loop)
        self._current_timer = None
        self.timer_task.add_exception_type(
            OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError
        )
        self.timer_task.start()

    def cog_unload(self):
        self.timer_task.cancel()

    async def get_active_timers(self, *, connection=None, seconds=30):
        query = """SELECT * FROM timers
                   WHERE expires < ((now() at time zone 'utc') + $1::interval)
                   ORDER BY expires;
                """
        con = connection or self.bot.pool

        records = await con.fetch(query, datetime.timedelta(seconds=seconds))

        if not records:
            return [None]

        timers = [Timer(record=r) if r else None for r in records]

        return timers

    async def call_timer(self, timer):
        # delete the timer
        query = "DELETE FROM timers WHERE id=$1;"
        await self.bot.pool.execute(query, timer.id)

        # dispatch the event
        event_name = f"{timer.event}_timer_complete"
        self.bot.dispatch(event_name, timer)

    async def dispatch_timer(self, timer):
        now = datetime.datetime.utcnow()

        if timer.expires >= now:
            to_sleep = (timer.expires - now).total_seconds()
            await asyncio.sleep(to_sleep)

        await self.call_timer(timer)

    @tasks.loop(seconds=30)
    async def timer_task(self):
        timers = await self.get_active_timers()

        for timer in timers:
            if timer is not None:
                self.bot.loop.create_task(self.dispatch_timer(timer))

    @timer_task.before_loop
    async def before_timer_task(self):
        await self.bot.wait_until_ready()
        # Wait for pool to connect
        while True:
            if self.bot.pool is None:
                await asyncio.sleep(1)
            else:
                break

    async def short_timer_optimisation(self, seconds, timer):
        await asyncio.sleep(seconds)
        event_name = f"{timer.event}_timer_complete"
        self.bot.dispatch(event_name, timer)

    async def create_timer(self, *args, **kwargs):
        """Creates a timer.
        Parameters
        -----------
        when: datetime.datetime
            When the timer should fire.
        event: str
            The name of the event to trigger.
            Will transform to 'on_{event}_timer_complete'.
        \*args
            Arguments to pass to the event
        \*\*kwargs
            Keyword arguments to pass to the event
        connection: asyncpg.Connection
            Special keyword-only argument to use a specific connection
            for the DB request.
        created: datetime.datetime
            Special keyword-only argument to use as the creation time.
            Should make the timedeltas a bit more consistent.
        Note
        ------
        Arguments and keyword arguments must be JSON serialisable.
        Returns
        --------
        :class:`Timer`
        """
        when, event, *args = args

        try:
            connection = kwargs.pop("connection")
        except KeyError:
            connection = self.bot.pool

        try:
            now = kwargs.pop("created")
        except KeyError:
            now = discord.utils.utcnow()

        # Remove timezone information since the database does not deal with it
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        timer = Timer.temporary(
            event=event, args=args, kwargs=kwargs, expires=when, created=now
        )
        delta = (when - now).total_seconds()
        if delta <= 60:
            # a shortcut for small timers
            self.bot.loop.create_task(self.short_timer_optimisation(delta, timer))
            return timer

        query = """INSERT INTO timers (event, extra, expires, created)
                   VALUES ($1, $2::jsonb, $3, $4)
                   RETURNING id;
                """

        row = await connection.fetchrow(
            query, event, {"args": args, "kwargs": kwargs}, when, now
        )
        timer.id = row[0]

        # only set the data check if it can be waited on
        if delta <= (86400 * 40):  # 40 days
            self._have_data.set()

        # # check if this timer is earlier than our currently run timer
        # if self._current_timer and when < self._current_timer.expires:
        #     # cancel the task and re-run it
        #     self._task.cancel()
        #     self._task = self.bot.loop.create_task(self.dispatch_timers())

        return timer

    @commands.group(
        aliases=["reminder", "timer"], invoke_without_command=True
    )
    async def remind(
        self,
        ctx,
        *,
        when: humantime.UserFriendlyTime(commands.clean_content, default=""),
    ):
        """Create a reminder that will notify you when completed

        Note that times are in UTC.
        To create a reminder, specify a time and an optional
        message to go with it.

        Examples:
        `{prefix}remind 2d do laundry`
        `{prefix}remind Meet with friends in five hours`
        `{prefix}remind 1 hour`
        `{prefix}remind attend the tourney on may 25th`
        """

        timer = await self.create_timer(
            when.dt,
            "reminder",
            ctx.author.id,
            ctx.channel.id,
            when.arg,
            connection=ctx.db,
            created=ctx.message.created_at,
            message_id=ctx.message.id,
        )
        delta = humantime.timedelta(when.dt, source=timer.created_at, discord_fmt=False)
        friendly_message = f"message: {when.arg}" if when.arg else "no message"
        await ctx.send(
            f"{ctx.tick(True)} Set a reminder for **`{delta}`** with {friendly_message}"
        )

    @remind.command(name="list", aliases=["all"], ignore_extra=False)
    async def remind_list(self, ctx):
        """View your currently running reminders."""
        query = """SELECT id, expires, extra #>> '{args,2}'
                   FROM timers
                   WHERE event = 'reminder'
                   AND extra #>> '{args,0}' = $1
                   ORDER BY expires
                   LIMIT 10;
                """

        records = await ctx.db.fetch(query, str(ctx.author.id))

        if len(records) == 0:
            return await ctx.send("No currently running reminders.")

        pages = MenuPages(source=TimerPageSource(records), clear_reactions_after=True,)
        await pages.start(ctx)

    @remind.command(name="here", ignore_extra=False)
    async def remind_here(self, ctx):
        """View your currently running reminders in the current channel."""
        query = """SELECT id, expires, extra #>> '{args,2}'
                   FROM timers
                   WHERE event = 'reminder'
                   AND extra #>> '{args,0}' = $1
                   AND extra #>> '{args,1}' = $2
                   ORDER BY expires
                   LIMIT 10;
                """

        records = await ctx.db.fetch(query, str(ctx.author.id), str(ctx.channel.id))

        if len(records) == 0:
            return await ctx.send("No currently running reminders in this channel.")

        pages = MenuPages(source=TimerPageSource(records), clear_reactions_after=True,)
        await pages.start(ctx)

    @remind.command(name="delete", aliases=["remove", "cancel"], ignore_extra=False)
    async def remind_delete(self, ctx, *, id: int):
        """Deletes a reminder by its ID.
        To get a reminder ID, use `{prefix}remind list`
        """

        query = """DELETE FROM timers
                   WHERE id=$1
                   AND event = 'reminder'
                   AND extra #>> '{args,0}' = $2;
                """

        status = await ctx.db.execute(query, id, str(ctx.author.id))
        if status == "DELETE 0":
            raise commands.BadArgument(
                "Could not delete any reminders with that ID."
                "\nDoes that reminder exist and do you own it?"
            )

        await ctx.send(f"{ctx.tick(True)} Successfully deleted reminder.")

    @remind.command(name="clear", ignore_extra=False)
    async def remind_clear(self, ctx):
        """Clears all reminders you have set."""

        # For UX purposes this has to be two queries.

        query = """SELECT COUNT(*)
                   FROM timers
                   WHERE event = 'reminder'
                   AND extra #>> '{args,0}' = $1;
                """

        author_id = str(ctx.author.id)
        total = await ctx.db.fetchrow(query, author_id)
        total = total[0]
        if total == 0:
            return await ctx.send("You don't have any reminders.")

        confirm = await ctx.confirm(
            f"Are you sure you want to delete {plural(total):reminder}?"
        )
        if not confirm:
            return await ctx.send("Aborting")

        query = """DELETE FROM timers WHERE event = 'reminder' AND extra #>> '{args,0}' = $1;"""
        await ctx.db.execute(query, author_id)

        await ctx.send(f"{ctx.tick(True)} Successfully deleted {plural(total):reminder}.")

    @commands.Cog.listener()
    async def on_reminder_timer_complete(self, timer):
        author_id, channel_id, message = timer.args

        try:
            channel = self.bot.get_channel(channel_id) or (
                await self.bot.fetch_channel(channel_id)
            )
        except discord.HTTPException:
            return

        guild_id = (
            channel.guild.id if isinstance(channel, discord.TextChannel) else "@me"
        )
        message_id = timer.kwargs.get("message_id")

        em = discord.Embed(
            title="Reminder",
            description=f"**When:** {timer.human_delta}\n**Message:** {message or 'None'}",
            color=colors.PRIMARY,
        )

        if message_id:
            em.description += f"\n\n[Jump to message](https://discord.com/channels/{guild_id}/{channel.id}/{message_id})"

        try:
            await channel.send(f"<@{author_id}>, here is your reminder.", embed=em)
        except discord.HTTPException:
            return


def setup(bot):
    bot.add_cog(Timers(bot))
