import aiohttp
import json
from datetime import datetime
from random import randint


class XkcdError(Exception):
    pass


class Comic:
    """
    INSTANCES SHOULD ONLY BE CREATED VIA THE fetch_comic() CLASSMETHOD.

    Attributes:

    data - The decoded and parsed JSON data

    url - The permaurl for the comic
    number - The comic's number
    title - Title of the comic
    alt_text - The text you get when hovering over the image
    desciption - Alias for alt_text
    image_url - URL to the image
    year - The year the comic was published
    month - The month the comic was published
    day - The day the comic was published
    publish_date - The datetime object taken from the year, month, and day
    date_str - Formatted datetime ({month} {day}, {year})

    """
    __slots__ = ["number", "url", "_unparsed_data", "data",
                 "title", "alt_text", "description", "image_url",
                 "year", "month", "day", "publish_date", "date_str"]

    XKCD_URL = "https://www.xkcd.com/"
    IMAGE_URL = "https://imgs.xkcd.com/comics/"

    def __init__(self, data, number, url):
        self.number = number
        self.url = url
        self._unparsed_data = data
        self.data = json.loads(self._unparsed_data.decode())
        self.title = self.data['safe_title']
        self.alt_text = self.data['alt']
        self.description = self.alt_text
        self.image_url = self.data['img']
        self.year = int(self.data["year"])
        self.month = int(self.data["month"])
        self.day = int(self.data["day"])
        self.publish_date = datetime(self.year, self.month, self.day)
        self.date_str = self.publish_date.strftime("%B %#d, %Y")

    def __str__(self):
        return self.title

    @classmethod
    async def fetch_comic(cls, number):
        """Fetches a comic and returns an instance of the Comic class"""
        if type(number) is str and number.isdigit():
            number = int(number)
        number
        if number <= 0:
            raise XkcdError("That comic does not exist.")

        url = cls.XKCD_URL + str(number)
        xkcd_json = url + "/info.0.json"

        async with aiohttp.ClientSession().get(xkcd_json) as resp:
            unparsed_data = await resp.read()

        return cls(unparsed_data, number, url)


async def get_latest_comic_num():
    """Fetches and returns the number of the latest comic."""
    async with aiohttp.ClientSession().get("https://xkcd.com/info.0.json") as resp:
        unparsed = await resp.read()
    data = json.loads(unparsed.decode())
    number = data['num']
    return int(number)


async def get_latest_comic():
    """Gets the latest comic and returns a Comic object"""
    latest = await get_latest_comic_num()
    return await Comic.fetch_comic(latest)


async def get_random_comic():
    """Gets a random comic and returns a Comic object"""
    latest = await get_latest_comic_num()
    number = randint(1, latest)
    return await Comic.fetch_comic(number)


async def get_comic(number: int):
    """Gets a comic and returns and returns a Comic object"""
    latest = await get_latest_comic_num()
    if number > latest:
        raise XkcdError("That comic does not exist. Number is too high.")
    return await Comic.fetch_comic(number)
