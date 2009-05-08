# Copyright (C) 2007-2009 Jelmer Vernooij <jelmer@samba.org>

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Mapping upgrade tests."""

from bzrlib.bzrdir import BzrDir
from bzrlib.repository import Repository
from bzrlib.tests import (
    TestCase,
    TestSkipped,
    )

from bzrlib.plugins.rebase.upgrade import (
    UpgradeChangesContent,
    upgrade_branch,
    upgrade_repository,
    upgrade_workingtree,
    create_upgraded_revid,
    generate_upgrade_map,
    )


class TestUpgradeChangesContent(TestCase):

    def test_init(self):
        x = UpgradeChangesContent("revisionx")
        self.assertEqual("revisionx", x.revid)


class ParserTests(TestCase):

    def test_create_upgraded_revid_new(self):
        self.assertEqual("bla-svn3-upgrade",
                         create_upgraded_revid("bla", "-svn3"))

    def test_create_upgraded_revid_upgrade(self):
        self.assertEqual("bla-svn3-upgrade",
                         create_upgraded_revid("bla-svn1-upgrade", "-svn3"))