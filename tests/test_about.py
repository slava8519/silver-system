from collections import Counter
from html.parser import HTMLParser
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.parse import unquote, urljoin, urlsplit

from second_brain import storage
from second_brain.webserver import server


class PageLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = Counter()
        self.links = []
        self.resources = []
        self.header_links = []
        self.navigation_links = []
        self.map_links = []
        self.map_resources = []
        self.map_caption = []
        self.article_ids = set()
        self.containers = {}
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("header", "nav", "article", "figure", "figcaption"):
            self.containers[tag] = attrs
        if "id" in attrs:
            self.ids[attrs["id"]] += 1
            if "article" in self.containers:
                self.article_ids.add(attrs["id"])
        in_map = self.containers.get("figure", {}).get("id") == "system-map"
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
            if "header" in self.containers:
                self.header_links.append(attrs["href"])
            if "nav" in self.containers:
                self.navigation_links.append(attrs["href"])
            if in_map:
                self.map_links.append(attrs["href"])
        if in_map and tag != "a":
            self.map_resources.extend(attrs[key] for key in ("src", "href", "xlink:href") if key in attrs)
            if tag == "object" and "data" in attrs:
                self.map_resources.append(attrs["data"])
            for candidate in attrs.get("srcset", "").split(","):
                if candidate.strip():
                    self.map_resources.append(candidate.split()[0])
        if tag == "link" and attrs.get("href"):
            self.resources.append(attrs["href"])
        if tag in ("img", "script", "source") and attrs.get("src"):
            self.resources.append(attrs["src"])

    def handle_endtag(self, tag):
        self.containers.pop(tag, None)

    def handle_data(self, data):
        if "figcaption" in self.containers and self.containers.get("figure", {}).get("id") == "system-map":
            self.map_caption.append(data)


class AboutPageTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_vault = storage.initialize(Path(tmp.name) / "Work")
        self.servers = {}
        for mode, vault in (("work", self.work_vault), ("demo", Path(tmp.name) / "Demo")):
            httpd = server(vault, 0, mode=mode)
            worker = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
            worker.start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(worker.join)
            self.addCleanup(httpd.shutdown)
            self.servers[mode] = httpd

    def get(self, mode, path):
        conn = HTTPConnection("127.0.0.1", self.servers[mode].server_port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200, (mode, path))
            return response.headers.get_content_type(), body
        finally:
            conn.close()

    def test_both_headers_link_to_the_same_static_help(self):
        for mode in self.servers:
            with self.subTest(mode=mode):
                _, home = self.get(mode, "/")
                headers = PageLinks(home.decode("utf-8")).header_links
                self.assertIn("/about.html", [urljoin("/", href) for href in headers])
        content_type, empty_vault_page = self.get("work", "/about.html")
        self.assertEqual(content_type, "text/html")
        self.assertEqual(self.get("demo", "/about.html"), (content_type, empty_vault_page))
        storage.create(self.work_vault, "note", "Материал владельца", "Личная запись для проверки.")
        self.assertEqual(self.get("work", "/about.html")[1], empty_vault_page)

    def test_help_fragments_and_local_links_resolve(self):
        _, body = self.get("work", "/about.html")
        page = PageLinks(body.decode("utf-8"))
        checked = set()
        sections = set()
        for href in page.links + page.resources:
            url = urlsplit(urljoin("/about.html", href))
            if url.scheme or url.netloc:
                continue
            if url.path == "/about.html" and url.fragment:
                fragment = unquote(url.fragment)
                self.assertEqual(page.ids[fragment], 1, href)
                sections.add(fragment)
            path = url.path + ("?" + url.query if url.query else "")
            if path not in checked:
                checked.add(path)
                for mode in self.servers:
                    with self.subTest(mode=mode, path=path):
                        _, resource = self.get(mode, path)
                        self.assertTrue(resource)
        self.assertGreaterEqual(len(sections), 2, "В справке нужны переходы к разным разделам.")

    def test_system_map_is_linked_and_readable_without_scripting(self):
        _, body = self.get("work", "/about.html")
        page = PageLinks(body.decode("utf-8"))
        self.assertIn("/about.html#system-map", [urljoin("/about.html", link) for link in page.navigation_links])
        self.assertEqual(page.ids["system-map"], 1)
        self.assertTrue("".join(page.map_caption).strip(), "Подпись схемы должна читаться в исходном HTML.")
        targets = set()
        for href in page.map_links:
            url = urlsplit(urljoin("/about.html", href))
            self.assertEqual((url.netloc, url.scheme, url.path), ("", "", "/about.html"), href)
            fragment = unquote(url.fragment)
            self.assertIn(fragment, page.article_ids, href)
            targets.add(fragment)
        self.assertGreaterEqual(len(targets), 2, "Узлы схемы должны открывать разные части статьи.")
        for reference in page.map_resources:
            url = urlsplit(urljoin("/about.html", reference))
            if url.scheme != "data":
                self.assertFalse(url.netloc or url.scheme, reference)
                self.get("work", url.path)
