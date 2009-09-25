#!/usr/bin/python
bzr_plugin_name = 'rewrite'

bzr_plugin_version = (0, 5, 4, 'final', 0)

bzr_compatible_versions = [
    (1, 14, 0), (1, 15, 0), (1, 16, 0), (1, 17, 0), (1, 18, 0),
    (2, 0, 0)]

bzr_minimum_version = bzr_compatible_versions[0]

bzr_maximum_version = bzr_compatible_versions[-1]

bzr_commands = [
    "replay",
    "rebase",
    "rebase_abort",
    "rebase_continue",
    "rebase_todo",
    ]

