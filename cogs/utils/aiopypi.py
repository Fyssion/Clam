import aiohttp
from datetime import datetime


BASE_URL = "https://pypi.org/pypi/"


class PackageNotFoundError(Exception):
    pass


class File:
    """Represents a downloadable file on PyPI."""

    def __init__(self, data):
        self.data = data

        self.comment_text = data["comment_text"]
        self.digests = data["digests"]
        self.downloads = data["downloads"]
        self.filename = data["filename"]
        self.has_sig = data["has_sig"]
        self.md5_digest = data["md5_digest"]
        self.packagetype = data["packagetype"]
        self.python_version = data["python_version"]
        self.requires_python = data["requires_python"]
        self.size = data["size"]
        self.upload_time = datetime.fromisoformat(data["upload_time"])
        self.url = data["url"]
        self.yanked = data["yanked"]
        self.yanked_reason = data["yanked_reason"]


class Release:
    """Represents a package release on PyPI."""

    def __init__(self, release, data):
        self.release = release
        self.version = release
        self.data = data

        self.files = []
        for file_data in data:
            self.files.append(File(file_data))

    def __str__(self):
        return self.version


class Package:
    """Represents a package on PyPI."""

    def __init__(self, data):
        self.data = data
        info = data["info"]

        self.author = info["author"]
        self.author_email = info["author_email"]
        self.bugtrack_url = info["bugtrack_url"]
        self.classifiers = info["classifiers"]
        self.description = info["description"]
        self.description_content_type = info["description_content_type"]
        self.docs_url = info["docs_url"]
        self.download_url = info["download_url"]
        self.download_last_day = info["downloads"]["last_day"]
        self.download_last_month = info["downloads"]["last_month"]
        self.download_last_week = info["downloads"]["last_week"]
        self.home_page = info["home_page"]
        self.keywords = info["keywords"]
        self.license = info["license"]
        self.maintainer = info["maintainer"]
        self.maintainer_email = info["maintainer_email"]
        self.name = info["name"]
        self.package_url = info["package_url"]
        self.url = self.package_url
        self.platform = info["platform"]
        self.project_url = ["project_url"]
        self.project_urls = info["project_urls"]
        self.release_url = info["release_url"]
        self.requires_dist = info["requires_dist"] or []
        self.requires_python = info["requires_python"]
        self.summary = info["summary"]
        self.short_description = self.summary
        self.version = info["version"]
        self.yanked = info["yanked"]
        self.yanked_reason = info["yanked_reason"]

        self.last_serial = data["last_serial"]

        self.releases = []
        if data["releases"]:
            for release in data["releases"]:
                self.releases.append(Release(release, data["releases"][release]))

        self.files = []
        if data["urls"]:
            for file_data in data["urls"]:
                self.files.append(File(file_data))

    def __str__(self):
        return self.name


async def fetch_package(package):
    """Fetch a package from PyPI.

    Parameters
    -----------
    package: :class:`str`
        The name of the package you want to fetch

    Returns
    --------
    package: :class:`.Package`
        The PyPI package found.

    Raises
    -------
    PackageNotFoundError:
        The package was not found in PyPI.

    """
    async with aiohttp.ClientSession() as session:
        async with session.get(BASE_URL + package + "/json") as resp:
            if resp.status == 200:
                data = await resp.json()
            else:
                raise PackageNotFoundError("That package wasn't found in PyPi.")
    return Package(data)


async def fetch_package_release(package, release):
    """Fetch a specific release of a package from PyPI.

    Parameters
    -----------
    package: :class:`str`
        The name of the package you want to fetch
    release: :class:`str`
        The specific release you want to fetch

    Returns
    --------
    package: :class:`.Package`
        The PyPI package found.

    Raises
    -------
    PackageNotFoundError:
        The package or release was not found in PyPI.

    """
    async with aiohttp.ClientSession() as session:
        async with session.get(BASE_URL + package + "/" + release + "/json") as resp:
            if resp.status == 200:
                data = await resp.json()
            else:
                raise PackageNotFoundError("That package release wasn't found in PyPi.")
    return Package(data)
