# backend/services/log_prettify.py

import asyncio
import tempfile
from bs4 import BeautifulSoup

from backend.logging_config import get_logger

logger = get_logger(context="log_prettify")


async def strip_spans(html_string):
    soup = BeautifulSoup(html_string, 'html.parser')

    # Find all 'span' tags and unwrap them
    # Meaning keep the insides of the span tags but remove the tags themselves
    for span in soup.find_all('span'):
        span.unwrap()

    return soup.prettify()


async def strip_multiple_blank_lines(html):
    new_html = ""
    prev_line_len = 0

    for line in html.split("\n"):
        stripped = await strip_spans(line)
        if len(stripped) == 0 and prev_line_len == 0:
            pass
        else:
            if len(new_html):
                new_html += "\n"
            new_html += line

        prev_line_len = len(stripped)

    return new_html


async def ansi_to_html(log_ansi):
    with tempfile.TemporaryFile(mode='w+t') as fp:
        fp.write(log_ansi)
        fp.seek(0)  # Rewind to the beginning to read

        process = await asyncio.create_subprocess_exec(
            "/home/lime/nova-hub/terminal-to-html",
            "-preview",
            stdin=fp,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stdout_data, _ = await process.communicate()

    return stdout_data.decode()
