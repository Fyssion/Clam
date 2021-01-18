import asyncio
import datetime

import discord
from discord.ext import commands, menus, tasks

import asyncpg
import enum
import humanize
import pytz

from .utils import colors, db, humantime
from .utils.menus import MenuPages


utc = pytz.UTC


class EventNotFound(commands.BadArgument):
    pass


class Events(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=64), index=True)
    description = db.Column(db.String(length=120))
    message_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    guild_id = db.Column(db.Integer(big=True), index=True)
    owner_id = db.Column(db.Integer(big=True), index=True)
    participants = db.Column(db.Array(db.Integer(big=True)), index=True)
    notify_role = db.Column(db.Integer(big=True))
    created_at = db.Column(
        db.Datetime(), default="now() at time zone 'utc'", index=True
    )
    starts_at = db.Column(db.Datetime(), index=True)
    timezone = db.Column(db.String(length=20))

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS events_unq_idx ON events (LOWER(name), guild_id);"
        return statement + "\n" + sql


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
        self.participants = record["participants"]
        self.notify_role = record["notify_role"]
        self.created_at = record["created_at"]
        self.starts_at = record["starts_at"]
        self.timezone = record["timezone"]

        return self

    def format_time(self):
        return self.starts_at.strftime("%b %d, %Y at %#I:%M %p %z")


class EditOptionMenu(menus.Menu):
    def __init__(self):
        super().__init__(timeout=30.0)
        self.result = None
        description = "\n".join([
            "\N{CLOCK FACE ONE OCLOCK} | timezone",
            "\N{LEFT SPEECH BUBBLE} | name",
            "\N{PAGE FACING UP} | description",
        ])

        self.embed = discord.Embed(
            title="\N{MEMO} Edit Options",
            description=f"Please press an option below.\n\n{description}",
            color=discord.Color.orange(),
        )

    async def send_initial_message(self, ctx, channel):
        return await channel.send(embed=self.embed)

    @menus.button("\N{CLOCK FACE ONE OCLOCK}")
    async def do_timezone(self, payload):
        self.result = "timezone"
        self.stop()

    @menus.button("\N{LEFT SPEECH BUBBLE}")
    async def do_name(self, payload):
        self.result = "name"
        self.stop()

    @menus.button("\N{PAGE FACING UP}")
    async def do_description(self, payload):
        self.result = "description"
        self.stop()

    async def prompt(self, ctx):
        await self.start(ctx, wait=True)
        return self.result


class EventSource(menus.ListPageSource):
    def __init__(self, data, ctx, all=False):
        super().__init__(data, per_page=10)
        self.ctx = ctx
        self.all = all

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        all_events = []
        ctx = self.ctx

        for (i, record) in enumerate(entries, start=offset):
            event = Event.from_record(record)
            formatted = ctx.cog.format_event_time(event)

            joined = (
                ":white_check_mark: "
                if self.ctx.author.id in event.participants
                else ""
            )

            author_is_reminded = discord.utils.get(ctx.author.roles, id=event.notify_role)

            reminded = " :alarm_clock:" if author_is_reminded else ""

            if self.all:
                guild = ctx.bot.get_guild(event.guild_id)
                guild = f"{guild}: " if guild else f"Unknown guild with ID {event.guild_id}: "
            else:
                guild = ""

            all_events.append(f"{joined}{guild}{event.name} `({event.id})` - {formatted}{reminded}")

        emoji_key = ":white_check_mark: | RSVP'd\n:alarm_clock: | Reminder set"

        description = (
            f"Total: **{len(self.entries)}**\nKey: name `(id)` - date\n{emoji_key}\n\n"
            + "\n".join(all_events)
        )

        em = discord.Embed(
            title="Events in this Server" if not self.all else "All Events",
            description=description,
            color=colors.PRIMARY,
        )
        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.avatar_url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        return em


class EventConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            message = await commands.MessageConverter().convert(arg)
        except Exception:
            pass
        else:
            query = """SELECT *
                       FROM events
                       WHERE channel_id=$1 AND message_id=$2;
                    """
            record = await ctx.db.fetchrow(query, message.channel.id, message.id)

            if record:
                return Event.from_record(record)

        try:
            arg = int(arg)
        except ValueError:
            arg = arg.lower().strip()
            query = """SELECT *
                       FROM events
                       WHERE LOWER(name)=$1 AND guild_id=$2;
                    """
            record = await ctx.db.fetchrow(query, arg, ctx.guild.id)
        else:
            query = """SELECT *
                       FROM events
                       WHERE id=$1;
                    """
            record = await ctx.db.fetchrow(query, arg)

        if not record:
            raise EventNotFound("Event was not found.")

        return Event.from_record(record)


class PartialEvent:
    def __init__(
        self,
        id,
        name,
        description,
        owner_id,
        starts_at,
        message_id,
        guild_id,
        channel_id,
        participants,
        notify_role,
        timezone="UTC",
    ):
        self.id = id
        self.name = name
        self.description = description
        self.owner_id = owner_id
        self.starts_at = starts_at
        self.message_id = message_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.participants = participants
        self.notify_role = notify_role
        self.timezone = timezone


class EventNameValidator(commands.Converter):
    async def convert(self, ctx, arg):
        if len(arg) > 64:
            raise commands.BadArgument(
                f"That name is too long. It must be 64 characters or less ({len(arg)}/64)."
            )

        query = "SELECT 1 FROM events WHERE LOWER(name)=$1;"
        exists = await ctx.db.fetchval(query, arg)

        if exists:
            raise commands.BadArgument("An event with that name already exists in this server.")

        return arg


class TimezoneValidator(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            pytz.timezone(arg)
        except Exception:
            raise commands.BadArgument("I couldn't find a timezone by that name. "
                                       "Please make sure you have the correct timezone name.")
        return arg


class PromptResponse(enum.Enum):
    TIMED_OUT = 0
    CANCELLED = 1


class Events(commands.Cog):
    """Create and manage events on Discord.

    Members can join, leave, and get notifed for events.
    """

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self.emoji = ":fireworks:"

        self._current_event = None
        self._event_ready = asyncio.Event()

        # self._event_dispatch_task = self.bot.loop.create_task(
        #     self.event_dispatch_loop()
        # )

        self.event_task.add_exception_type(
            OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError
        )
        self.event_task.start()

    def cog_unload(self):
        # self._event_dispatch_task.cancel()
        self.event_task.cancel()

    async def get_all_active_events(self, *, connection=None, seconds=30):
        query = """SELECT *
                   FROM events
                   WHERE starts_at < (CURRENT_TIMESTAMP + $1::interval)
                   ORDER BY starts_at;
                """
        con = connection or self.bot.pool

        records = await con.fetch(query, datetime.timedelta(seconds=seconds))

        if not records:
            return [None]

        events = [Event.from_record(r) if r else None for r in records]

        return events

    async def dispatch_event(self, event):
        now = datetime.datetime.utcnow()

        if event.starts_at >= now:
            to_sleep = (event.starts_at - now).total_seconds()
            await asyncio.sleep(to_sleep)

        await self.end_event(event)

    @tasks.loop(seconds=30)
    async def event_task(self):
        events = await self.get_all_active_events()

        for event in events:
            if event is not None:
                self.bot.loop.create_task(self.dispatch_event(event))

    @event_task.before_loop
    async def before_event_task(self):
        await self.bot.wait_until_ready()
        # Wait for pool to connect
        while True:
            if self.bot.pool is None:
                await asyncio.sleep(1)
            else:
                break

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
        em.set_footer(text="Event time")

        await message.edit(embed=em)

        if event.notify_role:
            notify_role = guild.get_role(event.notify_role)
            if notify_role:
                await channel.send(
                    f"{notify_role.mention}\nEvent `{event.name}` has started!"
                )
                await self.delete_event_role(notify_role)

        else:
            await channel.send(
                f"<@{event.owner_id}>\nEvent `{event.name}` has started!"
            )

    async def get_event(self, event_id):
        query = """SELECT * FROM events WHERE id=$1;"""
        async with self.bot.pool.acquire() as con:
            record = await con.fetchrow(query, event_id)
            if record is not None:
                return Event.from_record(record)
            return None

    async def delete_event(self, event):
        pass

    def format_event_time(self, event):
        when = event.starts_at
        localized = utc.localize(when).astimezone(pytz.timezone(event.timezone))
        return localized.strftime("%b %d, %Y at %#I:%M %p %Z")

    async def update_event_field(self, event, field, value):
        channel = self.bot.get_channel(event.channel_id)

        if not channel:
            return

        message = await channel.fetch_message(event.message_id)

        embed = message.embeds[0]

        old = embed.fields[field]

        embed.set_field_at(field, name=old.name, value=value, inline=False)

        await message.edit(embed=embed)

    async def delete_event_role(self, role):
        timers = self.bot.get_cog("Timers")

        if not timers:
            return await role.delete()

        when = datetime.datetime.datetime.utcnow() + datetime.timedelta(hours=5)

        await timers.create_timer(when, "event_role_delete", role.guild.id, role.id)

    @commands.Cog.listener()
    async def on_event_role_delete_timer_complete(self, timer):  # wowza
        guild_id, role_id = timer.args

        guild = self.bot.get_guild(guild_id)

        if not guild:
            return

        role = guild.get_role(role_id)

        if not role:
            return

        await role.delete()

    def create_event_embed(self, event):
        em = discord.Embed(
            title=event.name,
            description=(event.description or "")
            + (
                "\n\nPress :white_check_mark: to RSVP!\n"
                "You can press it again to leave."
            ),
            color=discord.Color.green(),
            timestamp=event.starts_at,
        )

        guild = self.bot.get_guild(event.guild_id)

        shortened = event.participants[20:] if len(event.participants) > 20 else event.participants
        participants = "\n".join(
            [guild.get_member(m).mention for m in shortened]
        )

        if len(event.participants) > 20:
            participants += f"\n...and {len(event.participants) - 20} more."
            participants += ("\nView all the participants with "
                             f"`{self.bot.guild_prefix(guild.id)}event participants {event.id}`")

        when = pytz.timezone(event.timezone).localize(event.starts_at)

        em.add_field(
            name="When", value=when.strftime("%b %d, %Y at %#I:%M %p %Z"), inline=False
        )

        em.add_field(name="Participants", value=participants or "No participants yet")
        em.set_footer(text=f"ID: {event.id} | Event starts")

        return em

    async def send_confirmation_message(self, event, channel, member, notify_role):
        em = discord.Embed(
            title=f"Successfully RSVP'd to {event.name}",
            description="Press \N{ALARM CLOCK} to be notifed when the event starts.",
            color=discord.Color.green(),
        )

        if not notify_role:
            em.description = "You cannot sign up to be notified for this event."

        try:
            bot_msg = await member.send(embed=em)

            if not notify_role:
                return

            def check(p):
                return (
                    str(p.emoji) == "\N{ALARM CLOCK}"
                    and p.user_id == member.id
                    and not p.guild_id
                    and p.message_id == bot_msg.id
                )

            await bot_msg.add_reaction("\N{ALARM CLOCK}")

            try:
                await self.bot.wait_for(
                    "raw_reaction_add", check=check, timeout=600  # 10 minute timeout
                )

                await member.add_roles(notify_role)
                em.description = "You will be notified when the event starts."

            except asyncio.TimeoutError:
                em.description = "You have not chosen to be notified when the event starts."

            await bot_msg.edit(embed=em)
            await bot_msg.remove_reaction("\N{ALARM CLOCK}", self.bot.user)

        except discord.Forbidden:
            await channel.send(
                f"{member.mention}: Please enable DMs in the future so I can send you event confirmation messages.",
                delete_after=10.0,
            )

    async def member_join_or_leave_event(self, event, guild, member, channel, force_join=False, force_leave=False):
        notify_role = guild.get_role(event.notify_role)

        if member.id in event.participants:
            event.participants.pop(event.participants.index(member.id))
            join = False
        else:
            event.participants.append(member.id)
            join = True

        if force_join and not join:
            return False

        if force_leave and join:
            return False

        message = await channel.fetch_message(event.message_id)

        em = message.embeds[0]

        members_mentioned = "\n".join([f"<@{m}>" for m in event.participants])
        em.set_field_at(1, name="Participants", value=members_mentioned or "\u200b")

        query = """UPDATE events
                   SET participants=$1
                   WHERE id=$2;
                """

        await self.bot.pool.execute(query, event.participants, event.id)

        await message.edit(embed=em)

        if not join:
            return True

        self.bot.loop.create_task(self.send_confirmation_message(event, channel, member, notify_role))

        return True

    async def handle_reaction(self, payload):
        if str(payload.user_id) in self.bot.blacklist:
            return

        if str(payload.emoji) == "✅":
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

            query = """SELECT *
                       FROM events
                       WHERE message_id=$1 AND channel_id=$2;
                    """

            record = await self.bot.pool.fetchrow(query, payload.message_id, channel.id)

            if not record:
                return

            event = Event.from_record(record)

            await self.member_join_or_leave_event(event, guild, member, channel)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.handle_reaction(payload)

    @commands.group(invoke_without_command=True)
    async def event(self, ctx):
        """Create and manage events on Discord!

        Features:
        - Easily create events with an interactive event creator
        - Ability to set a reminder for the event

        Note that the bot needs the manage roles permission in order
        to allow people to set a reminder. This is because the bot creates
        an event role and assigns it to people who want to be notified.
        """
        await ctx.send_help(ctx.command)

    def get_tz_embed(self):
        description = (
                "You must specify a timezone in the "
                "[tz database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). "
                "The timezone name is located under the `TZ database name` column."
            )

        em = discord.Embed(description=description, color=colors.PRIMARY)

        examples = [
            '"America/Chicago"',
            '"Europe/Amsterdam"',
            '"America/Los_Angeles"'
            '"UTC"'
        ]

        em.add_field(name="Examples", value="\n".join(examples))

        return em

    async def prompt(self, ctx, *, converter=None, delete_after=None):
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        while True:  # scary
            try:
                message = await self.bot.wait_for("message", check=check, timeout=180)
            except asyncio.TimeoutError:
                await ctx.send("You timed out. Aborting.")
                return PromptResponse.TIMED_OUT

            if message.content == f"{ctx.prefix}abort":
                await ctx.send("Aborted.")
                return PromptResponse.CANCELLED

            if not converter:
                result = message.content
                break

            try:
                result = await discord.utils.maybe_coroutine(converter, ctx, message.content)
            except commands.BadArgument as e:
                await ctx.send(f"{e}\nPlease try again.", delete_after=delete_after)
                continue

            else:
                break

        return result

    async def interactive_event_creation(self, ctx):
        await ctx.send(f"Welcome to the interactive event creator. To abort, use `{ctx.prefix}abort`.\n"
                       "What is the name of the event?")
        name = await self.prompt(ctx, converter=EventNameValidator().convert)

        if isinstance(name, PromptResponse):
            return

        em = self.get_tz_embed()
        await ctx.send("In what timezone is the event held?", embed=em)
        timezone = await self.prompt(ctx, converter=TimezoneValidator().convert)

        if isinstance(timezone, PromptResponse):
            return

        def future_time(ctx, arg):
            return humantime.FutureTime(arg)

        await ctx.send("When will the event start?")
        when = await self.prompt(ctx, converter=future_time)

        if isinstance(when, PromptResponse):
            return

        when = when.dt

        await ctx.send("What is the description of the event? Enter 'None' to skip.")
        description = await self.prompt(ctx)

        if isinstance(description, PromptResponse):
            return

        if description.lower().strip() == "none":
            description = None

        await ctx.send("Which channel should I send the event message to?")
        channel = await self.prompt(ctx, converter=commands.TextChannelConverter().convert)

        if isinstance(channel, PromptResponse):
            return

        return name, timezone, when, description, channel

    @event.command(
        name="create", aliases=["new"],
    )
    async def event_create(self, ctx):
        """Create a new event

        This leads you through an interactive event creation.
        """
        options = await self.interactive_event_creation(ctx)
        if not options:
            return
        name, timezone, when, description, channel = options

        embed = discord.Embed(description="Creating your event...", color=discord.Color.green())
        msg = await channel.send(embed=embed)

        try:
            notify_role = await ctx.guild.create_role(name=f"EVENT: {name}", mentionable=True)
            notify_role_id = notify_role.id
        except discord.Forbidden:
            await ctx.send("Warning: Could not create event role.")
            notify_role, notify_role_id = None, None

        query = """INSERT INTO events (name, description, owner_id, starts_at, message_id, guild_id, channel_id, participants, notify_role, timezone)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id;
                """

        event_args = (
            name,
            None,
            ctx.author.id,
            when,
            msg.id,
            ctx.guild.id,
            channel.id,
            [ctx.author.id],
            notify_role_id,
            timezone,
        )

        record = await ctx.db.fetchrow(query, *event_args)

        partial_event = PartialEvent(record[0], *event_args)

        delta = (when - datetime.datetime.utcnow()).total_seconds()

        if delta <= (86400 * 7):  # 7 days
            self._event_ready.set()

        # if self._current_event and date < self._current_event.starts_at:
        #     self._event_dispatch_task.cancel()
        #     self._event_dispatch_task = self.bot.loop.create_task(
        #         self.event_dispatch_loop()
        #     )

        embed = self.create_event_embed(partial_event)
        await msg.edit(embed=embed)
        await msg.add_reaction("\N{WHITE HEAVY CHECK MARK}")

        await ctx.send(ctx.tick(True, "Successfully created your event."))

        await self.send_confirmation_message(partial_event, ctx.channel, ctx.author, notify_role)

    @event.command(
        name="join", description="Join an event", aliases=["rsvp"]
    )
    async def event_join(self, ctx, *, event: EventConverter):
        result = await self.member_join_or_leave_event(event, ctx.guild, ctx.author, ctx.channel, force_join=True)
        if result:
            await ctx.send(ctx.tick(True, f"Joined event `{event.name}`"))
        else:
            await ctx.send("You have already joined that event.")

    @event.command(name="leave", description="Leave an event")
    async def event_leave(self, ctx, *, event: EventConverter):
        result = await self.member_join_or_leave_event(event, ctx.guild, ctx.author, ctx.channel, force_leave=True)
        if result:
            await ctx.send(ctx.tick(True, f"Left event `{event.name}`"))
        else:
            await ctx.send("You have not joined that event.")

    async def update_event_name_or_description(self, event, option, ctx, check):
        await ctx.send(f"What would you like to change the {option} to?")

        try:
            message = await self.bot.wait_for("message", timeout=60.0, check=check)

        except asyncio.TimeoutError:
            return await ctx.send("You took too long. Aborting.")

        if len(message.content) > 60 and option == "name":
            return await ctx.send("The name must be under 60 characters.")

        if len(message.content) > 120 and option == "description":
            return await ctx.send("The description must be under 120 characters.")

        query = f"""UPDATE events
                    SET {option}=$1
                    WHERE id=$2
                """

        await self.bot.pool.execute(query, message.content, event.id)

        event_channel = self.bot.get_channel(event.channel_id)

        if not event_channel:
            await ctx.send(
                "I couldn't find that event's channel. Was it deleted or hidden?"
            )

        else:

            try:
                event_message = await event_channel.fetch_message(event.message_id)

            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await ctx.send(
                    "I couldn't find that event's message. Was it deleted?"
                )
            else:
                em = event_message.embeds[0]
                if option == "name":
                    em.title = message.content
                    old = em.title
                elif option == "description":
                    if event.description:
                        em.description.replace(event.description, message.content)
                    else:
                        em.description = message.content + "\n\n" + em.description
                    old = event.description or "No description"
                await event_message.edit(embed=em)

            await ctx.send(
                ctx.tick(True, f" Updated event {option} from `{old}` to `{message.content}`")
            )

    @event.command(
        name="edit", description="Edit an event",
    )
    async def event_edit(self, ctx, *, event: EventConverter):
        member = ctx.author

        if member.id != event.owner_id:
            return await ctx.send("Only the event owner can make edits.")

        option = await EditOptionMenu().prompt(ctx)

        def check(ms):
            return ms.author == member and ms.channel == ctx.channel

        if option in ["name", "description"]:
            await self.update_event_name_or_description(event, option, ctx, check)

        elif option == "timezone":
            tz_embed = self.get_tz_embed()
            await ctx.send(embed=tz_embed)
            try:
                message = await self.bot.wait_for("message", timeout=60.0, check=check)

            except asyncio.TimeoutError:
                return await ctx.send("You took too long. Aborting.")

            timezone = message.content

            # make sure the timezone is valid
            try:
                pytz.timezone(timezone)
            except Exception:
                return await ctx.send("I couldn't find a timezone by that name. "
                                      "Please make sure you have the correct timezone name.")

            query = """UPDATE events
                        SET timezone=$1
                        WHERE id=$2;
                    """

            await self.bot.pool.execute(query, timezone, event.id)

            event.timezone = timezone

            await self.update_event_field(event, 0, self.format_event_time(event))

            await ctx.send(ctx.tick(True, "Updated your event's timezone."))

    @event.command(
        name="delete",
        description="Delete an event",
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

        channel = self.bot.get_channel(event.channel_id)
        try:
            message = await channel.fetch_message(event.message_id)

            em = message.embeds[0]
            em.description = (
                event.description or ""
            ) + "Sorry, this event has been cancelled."
            em.color = discord.Color.orange()

            await message.edit(embed=em)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        if event.notify_role:
            role = ctx.guild.get_role(event.notify_role)
            if role:
                await role.delete()

        await ctx.send(":wastebasket: Event cancelled and deleted.")

    @event.command(
        name="view",
        description="Get a link to jump to the event message",
        aliases=["show", "info"],
        hidden=True,
    )
    async def event_info(self, ctx, *, event: EventConverter):
        jump_url = f"https://discord.com/channels/{event.guild_id}/{event.channel_id}/{event.message_id}"
        await ctx.send(f"Jump to event: {jump_url}")

    @event.command(
        name="list", description="List all upcoming events", aliases=["upcoming"],
    )
    async def event_list(self, ctx):
        query = """SELECT *
                   FROM events
                   WHERE guild_id=$1
                   ORDER BY starts_at DESC
                """

        records = await ctx.db.fetch(query, ctx.guild.id)

        if not records:
            return await ctx.send("There are no events in this server.")

        pages = MenuPages(source=EventSource(records, ctx), clear_reactions_after=True,)
        await pages.start(ctx)

    @event.command(name="all", description="View all events")
    @commands.is_owner()
    async def event_all(self, ctx):
        query = """SELECT *
                   FROM events
                   ORDER BY starts_at DESC
                """

        records = await ctx.db.fetch(query)

        if not records:
            return await ctx.send("There are no events :(")

        pages = MenuPages(
            source=EventSource(records, ctx, all=True), clear_reactions_after=True,
        )
        await pages.start(ctx)


def setup(bot):
    bot.add_cog(Events(bot))
