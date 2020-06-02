import discord
from discord.ext import commands, menus

from datetime import datetime
import traceback
import json
import psutil
import typing
import asyncio
import asyncpg

from .utils.menus import MenuPages
from .utils import db


class TodoNotFound(commands.BadArgument):
    pass


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


class TodoItemConverter(commands.Converter):
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
            raise TodoNotFound("Todo item was not found.")

        return result


class Todo(commands.Cog):
    """Todo lists"""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log

    async def cog_command_error(self, ctx, error):
        if isinstance(error, TodoNotFound):
            await ctx.send("Todo item was not found.")
            ctx.handled = True

    @commands.group(description="Manage a todo list", invoke_without_command=True)
    async def todo(self, ctx):
        await ctx.invoke(self.todo_list)

    @todo.command(
        name="add",
        description="Add an item to your todo list",
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
                await ctx.send("You already have a todo item with this name.")
            except:
                await tr.rollback()
                await ctx.send("Could not create todo item.")
            else:
                await tr.commit()
                await ctx.send(":page_facing_up: Added item to your todo list.")

    @todo.command(
        name="done",
        description="Mark an item from your todo list as done",
        usage="[name or id]",
        aliases=["check", "complete"],
    )
    async def todo_done(self, ctx, *, item):
        try:
            item = int(item)
            sql = """UPDATE todos
                     SET completed_at=$1
                     WHERE author_id=$2 AND id=$3;
                  """
        except ValueError:
            item = item
            sql = """UPDATE todos
                     SET completed_at=$1
                     WHERE author_id=$2 AND name=$3;
                  """

        result = await ctx.db.execute(sql, datetime.utcnow(), ctx.author.id, item)
        if result.split(" ")[1] == "0":
            return await ctx.send("Todo item was not found.")

        await ctx.send(":ballot_box_with_check: Todo item marked as done")

    @todo.command(
        name="delete",
        description="Delete an item from your todo list",
        usage="[name or id]",
        aliases=["remove"],
    )
    async def todo_delete(self, ctx, *, item):
        try:
            item = int(item)
            query = "DELETE FROM todos WHERE id=$1 AND author_id=$2;"
        except ValueError:
            item = item
            query = "DELETE FROM todos WHERE name=$1 AND author_id=$2;"

        result = await ctx.db.execute(query, item, ctx.author.id)
        if result.split(" ")[1] == "0":
            return await ctx.send("Todo item was not found.")

        await ctx.send(":wastebasket: Todo item deleted.")

    @todo.command(
        name="info",
        description="View info about a todo item",
        usage="[name or id]",
        aliases=["information"],
    )
    async def todo_info(self, ctx, *, item: TodoItemConverter):
        todo_id, name, created_at, completed_at = item

        if completed_at:
            description = f":ballot_box_with_check: ~~{name}~~ ({todo_id})"
        else:
            description = f":black_large_square: {name} ({todo_id})"

        em = discord.Embed(
            title="Todo Item Info",
            description=description,
            color=discord.Color.blurple(),
            timestamp=created_at,
        )

        em.set_author(name=ctx.author.id, icon_url=ctx.author.avatar_url)
        em.set_footer(text="Item created")

        await ctx.send(embed=em)

    @todo.command(
        name="list",
        description="List all incomplete todo items",
        aliases=["incomplete"],
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

        all_todos = []
        for todo_id, name in records:
            all_todos.append(f":black_large_square: {name} `({todo_id})`")

        description = "name `(id)`\n\n" + "\n".join(all_todos)

        em = discord.Embed(
            title="Your Todo List",
            description=description,
            color=discord.Color.blurple(),
        )
        em.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)

        await ctx.send(embed=em)

    @todo.command(name="all", description="View all todo items")
    async def todo_all(self, ctx):
        query = """SELECT id, name, completed_at
                   FROM todos
                   WHERE author_id=$1
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have no todo items.")

        all_todos = []
        for todo_id, name, completed_at in records:
            if completed_at:
                all_todos.append(f":ballot_box_with_check: ~~{name}~~ `({todo_id})`")
            else:
                all_todos.append(f":black_large_square: {name} `({todo_id})`")

        description = "Key: name `(id)`\n\n" + "\n".join(all_todos)

        em = discord.Embed(
            title="Your Todo List",
            description=description,
            color=discord.Color.blurple(),
        )
        em.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)

        await ctx.send(embed=em)


def setup(bot):
    bot.add_cog(Todo(bot))
