#!/usr/bin/env python3

import logging
import os
import sys

import notional
from notional.iterator import EndpointIterator

logging.basicConfig(level=logging.INFO)

dbid = sys.argv[1]
auth_token = os.getenv("NOTION_AUTH_TOKEN")

notion = notional.connect(auth=auth_token)

tasks = EndpointIterator(
    endpoint=notion.databases.query,
    database_id=dbid,
    sorts=[{"direction": "ascending", "property": "Last Update"}],
)

for data in tasks:
    print(f"{data['id']} => {data['url']}")
