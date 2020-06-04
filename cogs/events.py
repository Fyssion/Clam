import discord
from discord.ext import commands, menus, tasks

from collections import Counter, defaultdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import traceback
import json
import psutil
import typing
import asyncio
import asyncpg
import humanize
import dateparser
from dateparser.search import search_dates
import pytz

from .utils.menus import MenuPages
from .utils import db, colors

utc = pytz.UTC


class EventNotFound(commands.BadArgument):
    pass


class EventSource(menus.ListPageSource):
    def __init__(self, data, ctx):
        super().__init__(data, per_page=10)
        self.ctx = ctx

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        all_events = []
        for i, (todo_id, name, starts_at, members) in enumerate(entries, start=offset):
            formatted = starts_at.strftime("%b %d, %Y at %#I:%M %p UTC")
            joined = ":white_check_mark: " if self.ctx.author.id in members else ""
            all_events.append(f"{joined}{name} `({todo_id})` - {formatted}")

        description = (
            f"Total: **{len(self.entries)}**\nKey: name `(id)` - date\n\n"
            + "\n".join(all_events)
        )

        em = discord.Embed(
            title="Events in this Server",
            description=description,
            color=colors.PRIMARY,
        )
        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.avatar_url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        return em


class Events(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=64), index=True)
    description = db.Column(db.String(length=120))
    message_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    guild_id = db.Column(db.Integer(big=True), index=True)
    owner_id = db.Column(db.Integer(big=True), index=True)
    members = db.Column(db.Array(db.Integer(big=True)), index=True)
    notify = db.Column(db.Integer(big=True))
    created_at = db.Column(
        db.Datetime(), default="now() at time zone 'utc'", index=True
    )
    starts_at = db.Column(db.Datetime(), index=True)


class Event:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.id = record["id"]
        self.name = record["name"]
        self.description = record["description"]
        self.message_id = record["message_id"]
        self.channel_id = record["channel_id"]
        self.guild_id = record["guild_id"]
        self.owner_id = record["owner_id"]
        self.members = record["members"]
        self.notify = record["notify"]
        self.created_at = record["created_at"]
        self.starts_at = record["starts_at"]

        return self


class EventConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            argument = int(argument)
        except ValueError:
            raise commands.BadArgument("Event must be int.")

        query = """SELECT *
                    FROM events
                    WHERE id=$1;
                """

        record = await ctx.db.fetchrow(query, argument)

        if not record:
            raise EventNotFound("Event was not found.")

        return Event.from_record(record)


class PartialEvent:
    def __init__(
        self,
        name,
        description,
        owner_id,
        starts_at,
        message_id,
        guild_id,
        channel_id,
        members,
        notify,
    ):
        self.name = name
        self.description = description
        self.owner_id = owner_id
        self.starts_at = starts_at
        self.message_id = message_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.members = members
        self.notify = notify


class Events(commands.Cog):
    """Easily create and manage events on Discord!"""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self.emoji = ":page_facing_up:"

        # guild_id: List[(member_id, insertion)]
        # A batch of data for bulk inserting event changes
        # True - insert, False - remove
        self._data_batch = defaultdict(list)
        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self.batch_updates.add_exception_type(asyncpg.PostgresConnectionError)
        self.batch_updates.start()

        self._current_event = None
        self._event_ready = asyncio.Event()

        self._event_dispatch_task = self.bot.loop.create_task(
            self.event_dispatch_loop()
        )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, EventNotFound):
            await ctx.send("Task was not found.")
            ctx.handled = True

    def cog_unload(self):
        self.batch_updates.stop()
        self._event_dispatch_task.cancel()

    async def bulk_insert(self):
        query = """UPDATE events
                   SET members = x.result_array
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(event_id BIGINT, result_array BIGINT[])
                   WHERE events.id = x.event_id;
                """

        if not self._data_batch:
            return

        final_data = []
        for event_id, data in self._data_batch.items():
            event = await self.get_event(event_id)
            as_set = set(event.members)
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({"event_id": event_id, "result_array": list(as_set)})

        await self.bot.pool.execute(query, final_data)
        self._data_batch.clear()

    @tasks.loop(seconds=10.0)
    async def batch_updates(self):
        async with self._batch_lock:
            await self.bulk_insert()

    async def get_first_active_event(self, days=7):
        query = """SELECT *
                   FROM events
                   WHERE starts_at < (CURRENT_DATE + $1::interval)
                   ORDER BY starts_at
                   LIMIT 1;
                """

        record = await self.bot.pool.fetchrow(query, timedelta(days=days))
        return Event.from_record(record) if record else None

    async def get_active_events(self, days=40):
        print(1, 0)
        event = await self.get_first_active_event(days)
        print(1, 1)
        if event is not None:
            print(1, 2)
            self._event_ready.set()
            return event

        print(1, 3)
        self._event_ready.clear()
        self._current_event = None
        await self._event_ready.wait()
        return await self.get_first_active_event(days)

    async def end_event(self, event):
        guild = self.bot.get_guild(event.guild_id)

        query = "DELETE FROM events WHERE id=$1"

        result = await self.bot.pool.execute(query, event.id)

        if not guild:
            return

        channel = guild.get_channel(event.channel_id)

        if not channel:
            return

        message = await channel.fetch_message(event.message_id)

        em = message.embeds[0]
        em.color = discord.Color.orange()
        em.description = (event.description or "") + "\n\nSorry, the event has started."

        await message.edit(embed=em)

        if event.notify:
            notify_role = guild.get_role(event.notify)
            if notify_role:
                await channel.send(
                    f"{notify_role.mention}\nEvent `{event.name}` has started!"
                )
                await notify_role.delete()

        else:
            await channel.send(
                f"<@{event.owner_id}>\nEvent `{event.name}` has started!"
            )

    async def event_dispatch_loop(self):
        try:
            while not self.bot.is_closed():
                print(1)
                event = await self.get_active_events()
                self._current_event = event
                now = datetime.utcnow()
                print(2)

                if utc.localize(event.starts_at) >= utc.localize(now):
                    print(3)
                    to_sleep = (
                        utc.localize(event.starts_at) - utc.localize(now)
                    ).total_seconds()
                    print(4)
                    await asyncio.sleep(to_sleep)
                print(5)
                await self.end_event(event)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self._event_dispatch_task.cancel()
            self._event_dispatch_task = self.bot.loop.create_task(
                self.event_dispatch_loop()
            )
        except:
            raise

    async def get_event(self, event_id):
        query = """SELECT * FROM events WHERE id=$1;"""
        async with self.bot.pool.acquire() as con:
            record = await con.fetchrow(query, event_id)
            if record is not None:
                return Event.from_record(record)
            return None

    async def delete_event(self, event):
        pass

    def create_event_embed(self, event):
        em = discord.Embed(
            title=event.name,
            description=(event.description or "")
            + "\n\nClick :white_check_mark: to join/leave!",
            color=discord.Color.green(),
            timestamp=event.starts_at,
        )
        guild = self.bot.get_guild(event.guild_id)
        members = "\n".join([guild.get_member(m).mention for m in event.members])
        em.add_field(name="Participants", value=members or "\u200b")
        em.set_footer(text="Event starts")

        return em

    async def handle_reaction(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)

        if not guild:
            return

        member = guild.get_member(payload.user_id)
        channel = guild.get_channel(payload.channel_id)

        if not channel:
            return

        if not member:
            return

        if member.bot:
            return

        query = """SELECT id, members, notify
                   FROM events
                   WHERE message_id=$1 AND channel_id=$2;
                """

        result = await self.bot.pool.fetchrow(query, payload.message_id, channel.id)

        if not result:
            return

        event_id, members, notify = result

        notify_role = guild.get_role(notify)

        if not notify_role:
            return

        if event_id in self._data_batch.keys():
            data = self._data_batch[event_id]
            as_set = set(members)
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)
            members = list(as_set)

        if member.id in members:
            members.pop(members.index(member.id))
            await member.remove_roles(notify_role)
            add = False
        else:
            members.append(member.id)
            await member.add_roles(notify_role)
            add = True

        message = await channel.fetch_message(payload.message_id)

        em = message.embeds[0]

        if not em.fields:
            return

        members_mentioned = "\n".join([guild.get_member(m).mention for m in members])
        em.set_field_at(0, name="Participants", value=members_mentioned or "\u200b")

        async with self._batch_lock:
            self._data_batch[event_id].append((member.id, add))

        await message.edit(embed=em)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.handle_reaction(payload)

    @commands.group(
        description="Create and manage events in Discord!", invoke_without_command=True
    )
    async def event(self, ctx):
        await ctx.send_help(ctx.command)

    @event.command(
        name="new",
        description="Create a new event",
        usage="[name]",
        aliases=["create", "add"],
    )
    @commands.bot_has_permissions(manage_roles=True, manage_messages=True)
    async def event_new(self, ctx, *, event):
        try:
            parsed_dates = search_dates(event, settings={"TIMEZONE": "UTC"})
        except ValueError:
            return await ctx.send(
                "Could not find a date or time. Please specify a date or time."
            )
        if not parsed_dates:
            return await ctx.send(
                "Could not find a date or time. Please specify a valid date or time."
            )
        date_string, date = parsed_dates[0]

        if utc.localize(date) < utc.localize(datetime.utcnow()):
            return await ctx.send("Please specify a date in the future.")

        name = event.replace(date_string, "").strip()

        if len(name) > 64:
            return await ctx.send(
                "That name is too long. Must be 64 characters or less."
            )

        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        bot_msg = await ctx.send(
            "Would you like to set a description? Reply with `no` to leave it blank."
        )

        try:
            description_msg = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )
        except asyncio.TimeoutError:
            return await ctx.send("You timed out. Sorry, please try again.")

        if description_msg.content.lower() == "no":
            description = None
        else:
            description = description_msg.content
            if len(description) > 120:
                return await ctx.send(
                    "Sorry, that description is too long. It must be under 120 characters."
                )

        await bot_msg.delete()
        await description_msg.delete()

        bot_msg = await ctx.send(
            "Would you like to notify members when the event begins? (y/n)"
        )

        try:
            notify_msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("You timed out. Sorry, please try again.")

        await bot_msg.delete()
        await notify_msg.delete()

        if notify_msg.content.lower().startswith("y"):
            role_name = name[:20] if len(name) > 20 else name
            notify_role = await ctx.guild.create_role(
                name=f"EVENT: {role_name}", mentionable=True
            )
        elif notify_msg.content.lower().startswith("n"):
            notify_role = None
        else:
            return await ctx.send("Please answer with `y` or `n`. Try again.")

        embed = discord.Embed(
            description="Creating your event...", color=discord.Color.green()
        )
        msg = await ctx.send(embed=embed)

        query = """INSERT INTO events (name, description, owner_id, starts_at, message_id, guild_id, channel_id, members, notify)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   RETURNING id;
                """

        result = await ctx.db.execute(
            query,
            name,
            description,
            ctx.author.id,
            date,
            msg.id,
            ctx.guild.id,
            ctx.channel.id,
            [ctx.author.id],
            notify_role.id if notify_role else None,
        )

        partial_event = PartialEvent(
            name,
            description,
            ctx.author.id,
            date,
            msg.id,
            ctx.guild.id,
            ctx.channel.id,
            [ctx.author.id],
            notify_role.id if notify_role else None,
        )

        delta = (utc.localize(date) - utc.localize(datetime.utcnow())).total_seconds()

        if delta <= (86400 * 7):  # 7 days
            self._event_ready.set()

        if self._current_event and date < self._current_event.start_at:
            self._event_dispatch_task.cancel()
            self._event_dispatch_task = self.bot.loop.create_task(
                self.event_dispatch_loop()
            )

        embed = self.create_event_embed(partial_event)
        await msg.edit(embed=embed)
        if notify_role:
            await ctx.author.add_roles(notify_role)
        await msg.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @event.command(name="join", description="Join an event", usage="[name or id]")
    async def event_join(self, ctx, event: EventConverter):
        pass

    @event.command(name="leave", description="Leave an event", usage="[name or id]")
    async def event_leave(self, ctx, event: EventConverter):
        pass

    @event.command(
        name="edit", description="Edit an event", usage="[name or id]",
    )
    async def event_edit(self, ctx, *, task):
        try:
            task = int(task)
            sql = """UPDATE todos
                     SET completed_at=NOW() AT TIME ZONE 'UTC'
                     WHERE author_id=$1 AND id=$2;
                  """
        except ValueError:
            task = task
            sql = """UPDATE todos
                     SET completed_at=NOW() AT TIME ZONE 'UTC'
                     WHERE author_id=$1 AND name=$2;
                  """

        result = await ctx.db.execute(sql, ctx.author.id, task)
        if result.split(" ")[1] == "0":
            return await ctx.send("Task was not found.")

        await ctx.send(":ballot_box_with_check: Task marked as done")

    @event.command(
        name="delete",
        description="Delete an event",
        usage="[name or id]",
        aliases=["remove", "cancel"],
    )
    async def event_delete(self, ctx, *, event: EventConverter):
        query = "DELETE FROM events WHERE id=$1 AND owner_id=$2 AND guild_id=$3;"

        if ctx.author.id != event.owner_id:
            return await ctx.send("You do not own this event.")

        result = await ctx.db.execute(query, event.id, ctx.author.id, ctx.guild.id)
        if result.split(" ")[1] == "0":
            return await ctx.send(
                f"An event called `{event}` with you as the owner was not found."
            )

        if event.notify:
            role = ctx.guild.get_role(event.notify)
            if role:
                await role.delete()

        await ctx.send(":wastebasket: Event cancelled and deleted.")

    @event.command(
        name="info",
        description="View info about an event",
        usage="[name or id]",
        aliases=["information"],
    )
    async def event_info(self, ctx, *, task: EventConverter):
        todo_id, name, created_at, completed_at = task

        if completed_at:
            description = f":ballot_box_with_check: ~~{name}~~ `({todo_id})`"
            description += f"\nCreated {humanize.naturaldate(created_at)}."
            description += f"\nCompleted {humanize.naturaldate(completed_at)}."
        else:
            description = f":black_large_square: {name} `({todo_id})`"
            description += f"\nCreated {humanize.naturaldate(created_at)}."

        em = discord.Embed(
            title="Event Info",
            description=description,
            color=colors.PRIMARY,
            timestamp=created_at,
        )

        em.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)
        em.set_footer(text="Event starts")

        await ctx.send(embed=em)

    @event.command(
        name="list", description="List all upcoming events", aliases=["upcoming"],
    )
    async def event_list(self, ctx):
        query = """SELECT id, name, starts_at, members
                   FROM events
                   WHERE guild_id=$1
                   ORDER BY starts_at DESC
                """

        records = await ctx.db.fetch(query, ctx.guild.id)

        if not records:
            return await ctx.send("There are no events in this server.")

        pages = MenuPages(source=EventSource(records, ctx), clear_reactions_after=True,)
        await pages.start(ctx)

    @event.command(name="all", description="View all events for this guild")
    async def event_all(self, ctx):
        query = """SELECT id, name, completed_at
                   FROM todos
                   WHERE author_id=$1
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have no tasks.")

        pages = MenuPages(
            source=EventSource(records, ctx, "all"), clear_reactions_after=True,
        )
        await pages.start(ctx)


def setup(bot):
    bot.add_cog(Events(bot))
