import asyncio
import calendar
import datetime

import discord
from discord.ext import commands, menus, tasks

import asyncpg
import enum
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
            "\N{CLOCK FACE ONE OCLOCK} | time",
            "\N{AIRPLANE} | timezone",
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
    async def do_time(self, payload):
        self.result = "time"
        self.stop()

    @menus.button("\N{AIRPLANE}")
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

            all_events.append(f"- {joined}{guild}{event.name} `(ID: {event.id})` - {formatted}{reminded}")

        emoji_key = ":white_check_mark: | RSVP'd\n:alarm_clock: | Reminder set"

        description = (
            f"Total: **{len(self.entries)}**\nKey: name `(ID: id)` - date\n{emoji_key}\n\n"
            + "\n".join(all_events)
            + f"\n\nTo join an event, use `{ctx.prefix}event join <event>`.\n"
            + f"To view details about an event, use `{ctx.prefix}event view <event>`."
        )

        em = discord.Embed(
            title="Events in this Server" if not self.all else "All Events",
            description=description,
            color=colors.PRIMARY,
        )
        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.avatar.url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        return em


class EventConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            message = await commands.MessageConverter().convert(ctx, arg)
        except Exception:
            pass
        else:
            query = """SELECT *
                       FROM events
                       WHERE guild_id=$1 AND channel_id=$2 AND message_id=$3;
                    """
            record = await ctx.db.fetchrow(query, message.guild.id, message.channel.id, message.id)

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
        exists = await ctx.db.fetchval(query, arg.lower().strip())

        if exists:
            raise commands.BadArgument("An event with that name already exists in this server.")

        return arg


class TimezoneValidator(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            timezone = pytz.timezone(arg)
        except Exception:
            raise commands.BadArgument("I couldn't find a timezone by that name. "
                                       "Please make sure you have the correct timezone name.")
        return timezone, arg


class ChannelValidator(commands.Converter):
    async def convert(self, ctx, arg):
        channel = await commands.TextChannelConverter().convert(ctx, arg)

        them_perms = channel.permissions_for(ctx.author)
        if not all((them_perms.send_messages, them_perms.embed_links, them_perms.add_reactions)):
            raise commands.BadArgument(
                "You do not have permissions to either send messages, embed links, or add reactions in that channel."
            )

        me_perms = channel.permissions_for(ctx.guild.me)
        if not all((me_perms.send_messages, me_perms.embed_links, me_perms.add_reactions)):
            raise commands.BadArgument(
                "I do not have permissions to either send messages, embed links, or add reactions in that channel."
            )

        return channel


class PromptResponse(enum.Enum):
    TIMED_OUT = 0
    CANCELLED = 1


def future_time(timezone):
    def func(ctx, arg):
        try:
            result = humantime.ShortTime(arg)
            shorttime = True
        except Exception:
            result = humantime.Time(arg)
            shorttime = False

        if shorttime:
            return result.dt

        when = result.dt

        # unfortunatly there is a bug where the end time is one day ahead
        # if the timezone time and the utc time are on different days.
        # to fix this, I have to check if they are on different days and add/subtract
        # a day to compensate.
        # this has to be an awful solution but it is the only thing I could think of.
        # I dislike working with timezones like this.

        timezone_now = datetime.datetime.now(timezone)
        offset = timezone_now.utcoffset()

        utc_now = datetime.datetime.utcnow()
        utc_now_day = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        timezone_now_day = timezone_now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

        if utc_now_day > timezone_now_day:
            when = when - datetime.timedelta(days=1)
        elif utc_now_day < timezone_now_day:
            when = when + datetime.timedelta(days=1)

        when = when - offset

        if when < datetime.datetime.utcnow():
            raise commands.BadArgument("That time is in the past.")

        return when

    return func


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

    def cog_check(self, ctx):
        return commands.guild_only().predicate(ctx)

    async def get_all_active_events(self, *, connection=None, seconds=30):
        query = """SELECT *
                   FROM events
                   WHERE starts_at < ((now() at time zone 'utc') + $1::interval)
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
        em.description = (event.description or "") + "\n\nThis event has already started."
        em.set_footer(text="Event time")

        await message.edit(embed=em)

        mention = f"<@{event.owner_id}>"
        notify_role = None

        if event.notify_role:
            notify_role = guild.get_role(event.notify_role)
            if notify_role:
                mention = notify_role.mention

        jump_url = f"https://discord.com/channels/{event.guild_id}/{event.channel_id}/{event.message_id}"
        await channel.send(f"{mention}: {event.name} has started!\nView event: {jump_url}")

        if notify_role:
            await self.delete_event_role(notify_role)

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

        when = datetime.datetime.utcnow() + datetime.timedelta(hours=5)

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

    def format_participants(self, event, guild):
        shortened = event.participants[:10] if len(event.participants) > 10 else event.participants
        participants = "\n".join(
            [guild.get_member(m).mention for m in shortened]
        )

        if len(event.participants) > 10:
            participants += f"\n...and {len(event.participants) - 10} more."
            participants += ("\nView all the participants with "
                             f"`{self.bot.guild_prefix(guild)}event participants {event.name}`")

        return participants or "\u200b"

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

        when = self.format_event_time(event)

        em.add_field(
            name="When", value=when, inline=False
        )

        participants = self.format_participants(event, guild)

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

        participants = self.format_participants(event, guild)
        em.set_field_at(1, name="Participants", value=participants)

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
        The event's role will be deleted five hours after the event is over
        or is cancelled.
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
            "- America/Chicago",
            "- Europe/Amsterdam",
            "- America/Los_Angeles",
            "- UTC"
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

        timezone, timezone_name = timezone

        await ctx.send("When will the event start?")
        when = await self.prompt(ctx, converter=future_time(timezone))

        if isinstance(when, PromptResponse):
            return

        await ctx.send("What is the description of the event? Enter 'None' to skip.")
        description = await self.prompt(ctx)

        if isinstance(description, PromptResponse):
            return

        if description.lower().strip() == "none":
            description = None

        await ctx.send("Which channel should I send the event message to?")
        channel = await self.prompt(ctx, converter=ChannelValidator().convert)

        if isinstance(channel, PromptResponse):
            return

        return name, timezone_name, when, description, channel

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
            description,
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

    async def update_event_name_or_description(self, event, option, ctx):
        await ctx.send(f"What would you like to change the {option} to?")

        result = await self.prompt(ctx)

        if isinstance(result, PromptResponse):
            return

        if len(result) > 60 and option == "name":
            return await ctx.send("The name must be under 60 characters.")

        if len(result) > 120 and option == "description":
            return await ctx.send("The description must be under 120 characters.")

        query = f"""UPDATE events
                    SET {option}=$1
                    WHERE id=$2
                """

        await self.bot.pool.execute(query, result, event.id)

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
                    em.title = result
                    old = em.title
                elif option == "description":
                    if event.description:
                        em.description.replace(event.description, result)
                    else:
                        em.description = result + "\n\n" + em.description
                    old = event.description or "No description"
                await event_message.edit(embed=em)

            await ctx.send(
                ctx.tick(True, f" Updated event {option} from `{old}` to `{result}`")
            )

    @event.command(
        name="edit"
    )
    async def event_edit(self, ctx, *, event: EventConverter):
        """Edit an event.

        You can edit an event's starting time, timezone, name, and description.
        You must own the event to edit it.
        """
        member = ctx.author

        if member.id != event.owner_id:
            return await ctx.send("Only the event owner can make edits.")

        option = await EditOptionMenu().prompt(ctx)

        if option in ["name", "description"]:
            await self.update_event_name_or_description(event, option, ctx)

        elif option == "time":
            timezone = pytz.timezone(event.timezone)

            await ctx.send("Enter the new time for the event.")
            converter = future_time(timezone)
            when = await self.prompt(ctx, converter=converter)

            if isinstance(when, PromptResponse):
                return

            query = """UPDATE events
                       SET starts_at=$1
                       WHERE id=$2;
                    """

            await self.bot.pool.execute(query, when, event.id)

            event.starts_at = when

            await self.update_event_field(event, 0, self.format_event_time(event))
            await ctx.send(ctx.tick(True, "Updated your event's time."))

        elif option == "timezone":
            tz_embed = self.get_tz_embed()
            await ctx.send(embed=tz_embed)
            result = await self.prompt(ctx, converter=TimezoneValidator().convert)

            if isinstance(result, PromptResponse):
                return

            timezone, timezone_name = result

            query = """UPDATE events
                        SET timezone=$1
                        WHERE id=$2;
                    """

            await self.bot.pool.execute(query, timezone_name, event.id)

            event.timezone = timezone_name

            await self.update_event_field(event, 0, self.format_event_time(event))

            await ctx.send(ctx.tick(True, "Updated your event's timezone."))

    @event.command(
        name="delete",
        aliases=["remove", "cancel"],
    )
    async def event_delete(self, ctx, *, event: EventConverter):
        """Delete an event.

        You must own the event to delete it.
        Moderators can delete any event.
        """
        if not ctx.author.guild_permissions.manage_guild:
            if ctx.author.id != event.owner_id:
                return await ctx.send("You do not own this event.")

        query = "DELETE FROM events WHERE id=$1"
        await ctx.db.execute(query, event.id)

        channel = self.bot.get_channel(event.channel_id)
        try:
            message = await channel.fetch_message(event.message_id)

            em = message.embeds[0]
            em.description = (
                event.description or ""
            ) + "Sorry, this event has been cancelled."
            em.color = discord.Color.orange()

            await message.edit(embed=em)
        except discord.HTTPException:
            pass

        if event.notify_role:
            role = ctx.guild.get_role(event.notify_role)
            if role:
                try:
                    await role.delete()
                except discord.HTTPException:
                    pass

        await ctx.send("\N{WASTEBASKET} Event cancelled and deleted.")

    @event.command(
        name="view",
        description="Get a link to jump to the event message",
        aliases=["show", "info"],
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

    @event.command(name="participants", aliases=["members"])
    async def event_participants(self, ctx, *, event: EventConverter):
        participants = []

        for user_id in event.participants:
            participants.append(f"<@{user_id}>")

        em = discord.Embed(title=f"{event.name} Participants", color=colors.PRIMARY)
        menu = ctx.embed_pages(participants, em)
        await menu.start(ctx)

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
