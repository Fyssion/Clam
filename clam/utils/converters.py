from discord.ext import commands

from .formats import plural


class StringMaxLengthConverter(commands.Converter):
    """A converter that only accepts strings under a certain length."""
    def __init__(self, length):
        super().__init__()
        self.length = length

    async def convert(self, ctx, arg):
        if len(arg) > self.length:
            message = f"That argument must be no more than {plural(self.length):character} ({len(arg)}/{self.length})."
            raise commands.BadArgument(message)\

        return arg
