import discord
from discord.ext import commands, menus

from datetime import datetime
from datetime import timezone
import traceback
import json
import psutil
import typing
import asyncio
import asyncpg
import humanize

from .utils.menus import MenuPages
from .utils import db, colors


class TodoNotFound(commands.BadArgument):
    pass


class TodoTaskSource(menus.ListPageSource):
    def __init__(self, data, ctx, list_type):
        super().__init__(data, per_page=10)
        self.ctx = ctx
        self.list_type = list_type

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        all_todos = []
        if self.list_type == "all":
            for i, (todo_id, name, completed_at) in enumerate(entries, start=offset):
                if completed_at:
                    all_todos.append(
                        f":ballot_box_with_check: ~~{name}~~ `({todo_id})`"
                    )
                else:
                    all_todos.append(f":black_large_square: {name} `({todo_id})`")
        else:
            for i, (todo_id, name) in enumerate(entries, start=offset):
                all_todos.append(f":black_large_square: {name} `({todo_id})`")

        description = (
            f"Total tasks: **{len(self.entries)}**\nKey: name `(id)`\n\n"
            + "\n".join(all_todos)
        )

        em = discord.Embed(
            title="Your Todo List", description=description, color=colors.PRIMARY,
        )
        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.avatar_url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        return em


class Todos(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=64), index=True)
    author_id = db.Column(db.Integer(big=True), index=True)
    created_at = db.Column(
        db.Datetime(), default="now() at time zone 'utc'", index=True
    )
    completed_at = db.Column(db.Datetime(), index=True)

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS todos_uniq_idx ON todos (LOWER(name), author_id);"
        return statement + "\n" + sql


class TodoTaskConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            argument = int(argument)
            query = """SELECT id, name, created_at, completed_at
                       FROM todos
                       WHERE id=$1 AND author_id=$2;
                    """
        except ValueError:
            query = """SELECT id, name, created_at, completed_at
                       FROM todos
                       WHERE name=$1 AND author_id=$2;
                    """

        result = await ctx.db.fetchrow(query, argument, ctx.author.id)

        if not result:
            raise TodoNotFound("Task was not found.")

        return result


class Todo(commands.Cog):
    """Todo lists"""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self.emoji = ":page_facing_up:"

    async def cog_command_error(self, ctx, error):
        if isinstance(error, TodoNotFound):
            await ctx.send("Task was not found.")
            ctx.handled = True

    @commands.group(invoke_without_command=True)
    async def todo(self, ctx):
        """Manage your todo list

        This command houses a series of subcommands used for
        managing a todo list. Each item on your todo list
        is referred to as a task.

        You can create tasks, view your todo list, check
        off tasks, and more. View the subcommands below for
        more info.
        """
        await ctx.invoke(self.todo_list)

    @todo.command(
        name="add",
        description="Add an task to your todo list",
        usage="[name]",
        aliases=["create", "new"],
    )
    async def todo_add(self, ctx, *, name):
        if len(name) > 64:
            return await ctx.send(
                "That name is too long. Must be 64 characters or less."
            )

        query = """INSERT INTO todos (name, author_id)
                   VALUES ($1, $2);
                """

        async with ctx.db.acquire() as con:
            tr = con.transaction()
            await tr.start()

            try:
                await ctx.db.execute(query, name, ctx.author.id)
            except asyncpg.UniqueViolationError:
                await tr.rollback()
                await ctx.send("You already have a task with this name.")
            except:
                await tr.rollback()
                await ctx.send("Could not create task.")
            else:
                await tr.commit()
                await ctx.send(
                    f":page_facing_up: Added **`{discord.utils.escape_mentions(name)}`** to your todo list."
                )

    @todo.command(
        name="done",
        description="Mark an task from your todo list as done",
        usage="[name or id]",
        aliases=["check", "complete"],
    )
    async def todo_done(self, ctx, *, task):
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

    @todo.command(
        name="delete",
        description="Delete a task from your todo list",
        usage="[name or id]",
        aliases=["remove"],
    )
    async def todo_delete(self, ctx, *, task):
        try:
            task = int(task)
            query = "DELETE FROM todos WHERE id=$1 AND author_id=$2;"
        except ValueError:
            task = task
            query = "DELETE FROM todos WHERE name=$1 AND author_id=$2;"

        result = await ctx.db.execute(query, task, ctx.author.id)
        if result.split(" ")[1] == "0":
            return await ctx.send("Task was not found.")

        await ctx.send(":wastebasket: Task deleted.")

    @todo.command(
        name="info",
        description="View info about a task",
        usage="[name or id]",
        aliases=["information"],
    )
    async def todo_info(self, ctx, *, task: TodoTaskConverter):
        todo_id, name, created_at, completed_at = task

        if completed_at:
            description = f":ballot_box_with_check: ~~{name}~~ `({todo_id})`"
            description += f"\nCreated {humanize.naturaldate(created_at)}."
            description += f"\nCompleted {humanize.naturaldate(completed_at)}."
        else:
            description = f":black_large_square: {name} `({todo_id})`"
            description += f"\nCreated {humanize.naturaldate(created_at)}."

        em = discord.Embed(
            title="Task Info",
            description=description,
            color=colors.PRIMARY,
            timestamp=created_at,
        )

        em.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)
        em.set_footer(text="Task created")

        await ctx.send(embed=em)

    @todo.command(
        name="list", description="List all incomplete tasks", aliases=["incomplete"],
    )
    async def todo_list(self, ctx):
        query = """SELECT id, name
                   FROM todos
                   WHERE author_id=$1 AND completed_at IS NULL
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have nothing on your todo list.")

        pages = MenuPages(
            source=TodoTaskSource(records, ctx, "list"), clear_reactions_after=True,
        )
        await pages.start(ctx)

    @todo.command(name="all", description="View all tasks")
    async def todo_all(self, ctx):
        query = """SELECT id, name, completed_at
                   FROM todos
                   WHERE author_id=$1
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have no tasks.")

        pages = MenuPages(
            source=TodoTaskSource(records, ctx, "all"), clear_reactions_after=True,
        )
        await pages.start(ctx)


def setup(bot):
    bot.add_cog(Todo(bot))
