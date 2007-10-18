# Copyright (C) 2006-2007 by Jelmer Vernooij
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
"""Rebase."""

from bzrlib.config import Config
from bzrlib.errors import BzrError, NoSuchFile, UnknownFormatError, UnrelatedBranches
from bzrlib.generate_ids import gen_revision_id
from bzrlib.merge import Merger
from bzrlib import osutils
from bzrlib.revision import NULL_REVISION
from bzrlib.trace import mutter
import bzrlib.ui as ui

from maptree import MapTree, map_file_ids
import os

REBASE_PLAN_FILENAME = 'rebase-plan'
REBASE_CURRENT_REVID_FILENAME = 'rebase-current'
REBASE_PLAN_VERSION = 1
REVPROP_REBASE_OF = 'rebase-of'

def rebase_plan_exists(wt):
    """Check whether there is a rebase plan present.

    :param wt: Working tree for which to check.
    :return: boolean
    """
    try:
        return wt._control_files.get(REBASE_PLAN_FILENAME).read() != ''
    except NoSuchFile:
        return False


def read_rebase_plan(wt):
    """Read a rebase plan file.

    :param wt: Working Tree for which to write the plan.
    :return: Tuple with last revision info and replace map.
    """
    text = wt._control_files.get(REBASE_PLAN_FILENAME).read()
    if text == '':
        raise NoSuchFile(REBASE_PLAN_FILENAME)
    return unmarshall_rebase_plan(text)


def write_rebase_plan(wt, replace_map):
    """Write a rebase plan file.

    :param wt: Working Tree for which to write the plan.
    :param replace_map: Replace map (old revid -> (new revid, new parents))
    """
    wt._control_files.put_utf8(REBASE_PLAN_FILENAME, 
            marshall_rebase_plan(wt.branch.last_revision_info(), replace_map))


def remove_rebase_plan(wt):
    """Remove a rebase plan file.

    :param wt: Working Tree for which to remove the plan.
    """
    wt._control_files.put_utf8(REBASE_PLAN_FILENAME, '')


def marshall_rebase_plan(last_rev_info, replace_map):
    """Marshall a rebase plan.

    :param last_rev_info: Last revision info tuple.
    :param replace_map: Replace map (old revid -> (new revid, new parents))
    :return: string
    """
    ret = "# Bazaar rebase plan %d\n" % REBASE_PLAN_VERSION
    ret += "%d %s\n" % last_rev_info
    for oldrev in replace_map:
        (newrev, newparents) = replace_map[oldrev]
        ret += "%s %s" % (oldrev, newrev) + \
            "".join([" %s" % p for p in newparents]) + "\n"
    return ret


def unmarshall_rebase_plan(text):
    """Unmarshall a rebase plan.

    :param text: Text to parse
    :return: Tuple with last revision info, replace map.
    """
    lines = text.split('\n')
    # Make sure header is there
    if lines[0] != "# Bazaar rebase plan %d" % REBASE_PLAN_VERSION:
        raise UnknownFormatError(lines[0])

    pts = lines[1].split(" ", 1)
    last_revision_info = (int(pts[0]), pts[1])
    replace_map = {}
    for l in lines[2:]:
        if l == "":
            # Skip empty lines
            continue
        pts = l.split(" ")
        replace_map[pts[0]] = (pts[1], pts[2:])
    return (last_revision_info, replace_map)


def regenerate_default_revid(repository, revid):
    rev = repository.get_revision(revid)
    return gen_revision_id(rev.committer, rev.timestamp)


def generate_simple_plan(history, start_revid, stop_revid, onto_revid, 
                         onto_ancestry, get_parents, generate_revid):
    """Create a simple rebase plan that replays history based 
    on one revision being replayed on top of another.

    :param history: Revision history
    :param start_revid: Id of revision at which to start replaying
    :param stop_revid: Id of revision until which to stop replaying
    :param onto_revid: Id of revision on top of which to replay
    :param onto_ancestry: Ancestry of onto_revid
    :param get_parents: Function for obtaining the parents of a revision
    :param generate_revid: Function for generating new revision ids

    :return: replace map
    """
    assert start_revid is None or start_revid in history
    assert stop_revid is None or stop_revid in history
    replace_map = {}
    if start_revid is not None:
        start_revno = history.index(start_revid)
    else:
        start_revno = None
    if stop_revid is not None:
        stop_revno = history.index(stop_revid)+1
    else:
        stop_revno = None
    new_parent = onto_revid
    for oldrevid in history[start_revno:stop_revno]: 
        parents = get_parents(oldrevid)
        assert len(parents) == 0 or \
                parents[0] == history[history.index(oldrevid)-1]
        parents[0] = new_parent
        parents = filter(lambda p: p not in onto_ancestry or p == onto_revid, 
                         parents) 
        newrevid = generate_revid(oldrevid)
        assert newrevid != oldrevid
        replace_map[oldrevid] = (newrevid, parents)
        new_parent = newrevid
    return replace_map


def generate_transpose_plan(graph, renames, get_parents, generate_revid):
    """Create a rebase plan that replaces a bunch of revisions
    in a revision graph.

    :param graph: Revision graph in which to operate
    :param renames: Renames of revision
    :param get_parents: Function for determining parents
    :param generate_revid: Function for creating new revision ids
    """
    replace_map = {}
    todo = []
    children = {}
    for r in graph:
        if not children.has_key(r):
            children[r] = []
        for p in graph[r]:
            if not children.has_key(p):
                children[p] = []
            children[p].append(r)

    # todo contains a list of revisions that need to 
    # be rewritten
    for r in renames:
        replace_map[r] = (renames[r], get_parents(renames[r]))
        todo.append(r)

    total = len(todo)
    processed = set()
    i = 0
    pb = ui.ui_factory.nested_progress_bar()
    try:
        while len(todo) > 0:
            r = todo.pop()
            i += 1
            pb.update('determining dependencies', i, total)
            # Add entry for them in replace_map
            for c in children[r]:
                if c in renames:
                    continue
                if replace_map.has_key(c):
                    parents = replace_map[c][1]
                else:
                    parents = list(graph[c])
                assert isinstance(parents, list), \
                        "Expected list of parents, got: %r" % parents
                # replace r in parents with replace_map[r][0]
                if not replace_map[r][0] in parents:
                    parents[parents.index(r)] = replace_map[r][0]
                replace_map[c] = (generate_revid(c), parents)
                assert replace_map[c][0] != c
            processed.add(r)
            # Add them to todo[]
            todo.extend(filter(lambda x: not x in processed, children[r]))
    finally:
        pb.finished()

    # Remove items from the map that already exist
    for revid in renames:
        if replace_map.has_key(revid):
            del replace_map[revid]

    return replace_map


def rebase_todo(repository, replace_map):
    """Figure out what revisions still need to be rebased.

    :param repository: Repository that contains the revisions
    :param replace_map: Replace map
    """
    for revid in replace_map:
        if not repository.has_revision(replace_map[revid][0]):
            yield revid


def rebase(repository, replace_map, replay_fn):
    """Rebase a working tree according to the specified map.

    :param repository: Repository that contains the revisions
    :param replace_map: Dictionary with revisions to (optionally) rewrite
    :param merge_fn: Function for replaying a revision
    """
    todo = list(rebase_todo(repository, replace_map))
    dependencies = {}

    # Figure out the dependencies
    for revid in todo:
        for p in replace_map[revid][1]:
            if repository.has_revision(p):
                continue
            if not dependencies.has_key(p):
                dependencies[p] = []
            dependencies[p].append(revid)

    pb = ui.ui_factory.nested_progress_bar()
    total = len(todo)
    i = 0
    try:
        while len(todo) > 0:
            pb.update('rebase revisions', i, total)
            i += 1
            revid = todo.pop()
            (newrevid, newparents) = replace_map[revid]
            if filter(repository.has_revision, newparents) != newparents:
                # Not all parents present yet, avoid for now
                continue
            if repository.has_revision(newrevid):
                # Was already converted, no need to worry about it again
                continue
            replay_fn(repository, revid, newrevid, newparents)
            assert repository.has_revision(newrevid)
            assert repository.revision_parents(newrevid) == newparents, \
                   "expected parents %r, got %r" % (newparents, 
                           repository.revision_parents(newrevid))
            if dependencies.has_key(newrevid):
                todo.extend(dependencies[newrevid])
                del dependencies[newrevid]
    finally:
        pb.finished()
        
    #assert all(map(repository.has_revision, 
    #           [replace_map[r][0] for r in replace_map]))



def replay_snapshot(repository, oldrevid, newrevid, new_parents, 
                    revid_renames, fix_revid=None):
    """Replay a commit by simply commiting the same snapshot with different 
    parents.

    :param repository: Repository in which the revision is present.
    :param oldrevid: Revision id of the revision to copy.
    :param newrevid: Revision id of the revision to create.
    :param new_parents: Revision ids of the new parent revisions.
    :param revid_renames: Revision id renames for texts.
    """
    assert isinstance(new_parents, list)
    mutter('creating copy %r of %r with new parents %r' % 
                               (newrevid, oldrevid, new_parents))
    oldrev = repository.get_revision(oldrevid)

    revprops = dict(oldrev.properties)
    revprops[REVPROP_REBASE_OF] = oldrevid

    builder = repository.get_commit_builder(branch=None, 
                                            parents=new_parents, 
                                            config=Config(),
                                            committer=oldrev.committer,
                                            timestamp=oldrev.timestamp,
                                            timezone=oldrev.timezone,
                                            revprops=revprops,
                                            revision_id=newrevid)
    try:

        # Check what new_ie.file_id should be
        # use old and new parent inventories to generate new_id map
        fileid_map = map_file_ids(repository, oldrev.parent_ids, new_parents)
        oldtree = MapTree(repository.revision_tree(oldrevid), fileid_map)
        total = len(oldtree.inventory)
        pb = ui.ui_factory.nested_progress_bar()
        i = 0
        try:
            parent_invs = map(repository.get_revision_inventory, new_parents)
            transact = repository.get_transaction()
            for path, ie in oldtree.inventory.iter_entries():
                pb.update('upgrading file', i, total)
                ie = ie.copy()
                # Either this file was modified last in this revision, 
                # in which case it has to be rewritten
                if fix_revid is not None:
                    ie.revision = fix_revid(ie.revision)
                if ie.revision == oldrevid:
                    if repository.weave_store.get_weave_or_empty(ie.file_id, repository.get_transaction()).has_version(newrevid):
                        ie.revision = newrevid
                    else:
                        ie.revision = None
                else:
                    # or it was already there before the commit, in 
                    # which case the right revision should be used
                    if revid_renames.has_key(ie.revision):
                        ie.revision = revid_renames[ie.revision]
                    # make sure at least one of the new parents contains 
                    # the ie.file_id, ie.revision combination
                    #if len(filter(lambda inv: ie.file_id in inv and inv[ie.file_id].revision == ie.revision, parent_invs)) == 0:
                    #    raise ReplayParentsInconsistent(ie.file_id, ie.revision)
                i += 1
                builder.record_entry_contents(ie, parent_invs, path, oldtree,
                        oldtree.path_content_summary(path))
        finally:
            pb.finished()

        builder.finish_inventory()
    except:
        builder.repository.abort_write_group()
        raise
    return builder.commit(oldrev.message)


def commit_rebase(wt, oldrev, newrevid):
    """Commit a rebase.
    
    :param wt: Mutable tree with the changes.
    :param oldrev: Revision info of new revision to commit.
    :param newrevid: New revision id."""
    assert oldrev.revision_id != newrevid
    revprops = dict(oldrev.properties)
    revprops[REVPROP_REBASE_OF] = oldrev.revision_id
    wt.commit(message=oldrev.message, timestamp=oldrev.timestamp, 
              timezone=oldrev.timezone, revprops=revprops, rev_id=newrevid)
    write_active_rebase_revid(wt, None)


def replay_delta_workingtree(wt, oldrevid, newrevid, newparents, 
                             merge_type=None):
    """Replay a commit in a working tree, with a different base.

    :param wt: Working tree in which to do the replays.
    :param oldrevid: Old revision id
    :param newrevid: New revision id
    :param newparents: New parent revision ids
    """
    repository = wt.branch.repository
    if merge_type is None:
        from bzrlib.merge import Merge3Merger
        merge_type = Merge3Merger
    oldrev = wt.branch.repository.get_revision(oldrevid)
    # Make sure there are no conflicts or pending merges/changes 
    # in the working tree
    if wt.changes_from(wt.basis_tree()).has_changed():
        raise BzrError("Working tree has uncommitted changes.")
    complete_revert(wt, [newparents[0]])
    assert not wt.changes_from(wt.basis_tree()).has_changed()

    oldtree = repository.revision_tree(oldrevid)
    write_active_rebase_revid(wt, oldrevid)
    merger = Merger(wt.branch, this_tree=wt)
    merger.set_other_revision(oldrevid, wt.branch)
    try:
        merger.find_base()
    except UnrelatedBranches:
        merger.set_base_revision(NULL_REVISION, wt.branch)
    merger.merge_type = merge_type
    merger.do_merge()
    for newparent in newparents[1:]:
        wt.add_pending_merge(newparent)

    commit_rebase(wt, oldrev, newrevid)


def workingtree_replay(wt, map_ids=False, merge_type=None):
    """Returns a function that can replay revisions in wt.

    :param wt: Working tree in which to do the replays.
    :param map_ids: Whether to try to map between file ids (False for path-based merge)
    """
    def replay(repository, oldrevid, newrevid, newparents):
        assert wt.branch.repository == repository
        return replay_delta_workingtree(wt, oldrevid, newrevid, newparents, 
                                        merge_type=merge_type)
    return replay


def write_active_rebase_revid(wt, revid):
    """Write the id of the revision that is currently being rebased. 

    :param wt: Working Tree that is being used for the rebase.
    :param revid: Revision id to write
    """
    if revid is None:
        revid = NULL_REVISION
    wt._control_files.put_utf8(REBASE_CURRENT_REVID_FILENAME, revid)


def read_active_rebase_revid(wt):
    """Read the id of the revision that is currently being rebased.

    :param wt: Working Tree that is being used for the rebase.
    :return: Id of the revision that is being rebased.
    """
    try:
        text = wt._control_files.get(REBASE_CURRENT_REVID_FILENAME).read().rstrip("\n")
        if text == NULL_REVISION:
            return None
        return text
    except NoSuchFile:
        return None


def complete_revert(wt, newparents):
    """Simple helper that reverts to specified new parents and makes sure none 
    of the extra files are left around.

    :param wt: Working tree to use for rebase
    :param newparents: New parents of the working tree
    """
    newtree = wt.branch.repository.revision_tree(newparents[0])
    delta = wt.changes_from(newtree)
    wt.branch.generate_revision_history(newparents[0])
    wt.set_parent_ids(newparents[:1])
    for (f, _, _) in delta.added:
        abs_path = wt.abspath(f)
        if osutils.lexists(abs_path):
            if osutils.isdir(abs_path):
                osutils.rmtree(abs_path)
            else:
                os.unlink(abs_path)
    wt.revert(None, old_tree=newtree, backups=False)
    assert not wt.changes_from(wt.basis_tree()).has_changed()
    wt.set_parent_ids(newparents)


class ReplaySnapshotError(BzrError):
    _fmt = """Replaying the snapshot failed: %(message)s."""

    def __init__(self, message):
        BzrError.__init__(self)
        self.message = message


class ReplayParentsInconsistent(BzrError):
    _fmt = """Parents were inconsistent while replaying commit for file id %(fileid)s, revision %(revid)s."""

    def __init__(self, fileid, revid):
        BzrError.__init__(self)
        self.fileid = fileid
        self.revid = revid
