# Copyright (C) 2009 by Jelmer Vernooij <jelmer@samba.org>
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Revision pseudonyms."""

from collections import defaultdict

from bzrlib import (
    errors,
    foreign,
    ui,
    )


def extract_foreign_revids(rev):
    """Find ids of semi-equivalent revisions in foreign VCS'es.

    :param: Bazaar revision object
    :return: Set with semi-equivalent revisions.
    """
    ret = set()
    if "converted-from" in rev.properties:
        for line in rev.properties.get("converted-from", "").splitlines():
            (kind, serialized_foreign_revid) = line.split(" ", 1)
            ret.add((kind, serialized_foreign_revid))
    # Maybe an older-style launchpad-cscvs import ?
    if "cscvs-svn-branch-path" in rev.properties:
        import urllib
        ret.add(("svn", "%s:%s:%s" % (
             rev.properties["cscvs-svn-repository-uuid"],
             rev.properties["cscvs-svn-revision-number"],
             urllib.quote(rev.properties["cscvs-svn-branch-path"].strip("/")))))
    # Perhaps 'rev' is a foreign revision ?
    if getattr(rev, "foreign_revid", None) is not None:
        ret.add(("svn", rev.mapping.vcs.serialize_foreign_revid(rev.foreign_revid)))
    # Try parsing the revision id
    try:
        foreign_revid, mapping = \
            foreign.foreign_vcs_registry.parse_revision_id(rev.revision_id)
    except errors.InvalidRevisionId:
        pass
    else:
        ret.add((mapping.vcs.abbreviation, 
            mapping.vcs.serialize_foreign_revid(foreign_revid)))

    return ret


def find_pseudonyms(repository, revids):
    """Find revisions that are pseudonyms of each other.

    :param repository: Repository object
    :param revids: Sequence of revision ids to check
    :return: Iterable over sets of pseudonyms
    """
    # Where have foreign revids ended up?
    conversions = defaultdict(set)
    # What are native revids conversions of?
    conversion_of = defaultdict(set)
    revs = repository.get_revisions(revids)
    pb = ui.ui_factory.nested_progress_bar()
    try:
        for i, rev in enumerate(revs):
            pb.update("finding pseudonyms", i, len(revs))
            for foreign_revid in extract_foreign_revids(rev):
                conversion_of[rev.revision_id].add(foreign_revid)
                conversions[foreign_revid].add(rev.revision_id)
    finally:
        pb.finished()
    done = set()
    for foreign_revid in conversions.keys():
        ret = set()
        check = set(conversions[foreign_revid])
        while check:
            x = check.pop()
            extra = set()
            for frevid in conversion_of[x]:
                extra.update(conversions[frevid])
                del conversions[frevid]
            del conversion_of[x]
            check.update(extra)
            ret.add(x)
        if len(ret) > 1:
            yield ret


def pseudonyms_as_dict(l):
    """Convert an iterable over pseudonyms to a dictionary.

    :param l: Iterable over sets of pseudonyms
    :return: Dictionary with pseudonyms for each revid.
    """
    ret = {}
    for pns in l:
        for pn in pns:
            ret[pn] = pns - set([pn])
    return ret


def generate_rebase_map_from_pseudonyms(pseudonym_dict, existing, desired):
    """Create a rebase map from pseudonyms and existing/desired ancestry.

    :param pseudonym_dict: Dictionary with pseudonym as returned by pseudonyms_as_dict()
    :param existing: Existing ancestry, might need to be rebased
    :param desired: Desired ancestry
    :return: rebase map, as dictionary
    """
    rebase_map = {}
    for revid in existing:
        for pn in pseudonym_dict.get(revid, []):
            if pn in desired:
                rebase_map[revid] = pn
    return rebase_map
