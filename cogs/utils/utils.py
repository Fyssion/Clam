import codecs
import io
import os
import pathlib
import zlib

import discord


async def quote(message, content, *, quote=None, **kwargs):
    quote = quote or message.content
    quote = discord.utils.escape_mentions(quote)
    quote = quote.replace("\n", "\n> ")
    formatted = f"> {quote}\n{message.author.mention} {content}"
    await message.channel.send(formatted, **kwargs)


async def reply_to(message, content, **kwargs):
    formatted = f"Replying to {message.author.mention} from {message.jump_url}\n{content}"
    await message.channel.send(formatted, **kwargs)


def get_lines_of_code(comments=False):
    total = 0
    file_amount = 0
    for path, subdirs, files in os.walk("."):
        if "venv" in subdirs:
            subdirs.remove("venv")
        if "env" in subdirs:
            subdirs.remove("env")
        for name in files:
            if name.endswith(".py"):
                file_amount += 1
                with codecs.open(
                    "./" + str(pathlib.PurePath(path, name)), "r", "utf-8"
                ) as f:
                    for i, l in enumerate(f):
                        if (
                            l.strip().startswith("#") or len(l.strip()) == 0
                        ):  # skip commented lines.
                            if comments:
                                total += 1
                            pass
                        else:
                            total += 1
    excomments = " (including comments and newlines)" if comments else ""
    return f"I am made of {total:,} lines of Python{excomments}, spread across {file_amount:,} files!"


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)

    def readline(self):
        return self.stream.readline().decode("utf-8")

    def skipline(self):
        self.stream.readline()

    def read_compressed_chunks(self):
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self):
        buf = b""
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b"\n")
            while pos != -1:
                yield buf[:pos].decode("utf-8")
                buf = buf[pos + 1 :]
                pos = buf.find(b"\n")


def hover_link(ctx, msg, text="`?`"):
    return (
        f"[{text}](https://www.discordapp.com/"
        f"channels/{ctx.guild.id}/{ctx.channel.id} "
        f""""{msg}")"""
    )


def is_int(string):
    try:
        int(string)
        return True
    except ValueError:
        return False
