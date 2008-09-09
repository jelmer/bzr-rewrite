# Copyright (C) 2006,2008 by Jelmer Vernooij
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
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
"""Upgrading revisions made with older versions of the mapping."""

from bzrlib import ui
from bzrlib.errors import BzrError, InvalidRevisionId
from bzrlib.revision import Revision
from bzrlib.trace import info

import itertools
from bzrlib.plugins.svn.mapping import mapping_registry


class RebaseNotPresent(DependencyNotPresent):
    _fmt = "Unable to import bzr-rebase (required for upgrade support): %(error)s"

    def __init__(self, error):
        DependencyNotPresent.__init__(self, 'bzr-rebase', error)


def check_rebase_version(min_version):
    """Check what version of bzr-rebase is installed.

    Raises an exception when the version installed is older than 
    min_version.

    :raises RebaseNotPresent: Raised if bzr-rebase is not installed or too old.
    """
    try:
        from bzrlib.plugins.rebase import version_info as rebase_version_info
        if rebase_version_info[:2] < min_version:
            raise RebaseNotPresent("Version %r present, at least %r required" 
                                   % (rebase_version_info, min_version))
    except ImportError, e:
        raise RebaseNotPresent(e)



class UpgradeChangesContent(BzrError):
    """Inconsistency was found upgrading the mapping of a revision."""
    _fmt = """Upgrade will change contents in revision %(revid)s. Use --allow-changes to override."""

    def __init__(self, revid):
        self.revid = revid



def create_upgraded_revid(revid, mapping_suffix, upgrade_suffix="-upgrade"):
    """Create a new revision id for an upgraded version of a revision.
    
    Prevents suffix to be appended needlessly.

    :param revid: Original revision id.
    :return: New revision id
    """
    if revid.endswith(upgrade_suffix):
        return revid[0:revid.rfind("-svn")] + mapping_suffix + upgrade_suffix
    else:
        return revid + mapping_suffix + upgrade_suffix


def determine_fileid_renames(old_tree, new_tree):
    for old_file_id in old_tree:
        new_file_id = new_tree.path2id(old_tree.id2path(old_file_id))
        if new_file_id is not None:
            yield old_file_id, new_file_id


def upgrade_workingtree(wt, foreign_repository, new_mapping, mapping_registry, 
                        allow_changes=False, verbose=False):
    """Upgrade a working tree.

    :param foreign_repository: Foreign repository object
    """
    wt.lock_write()
    try:
        old_revid = wt.last_revision()
        renames = upgrade_branch(wt.branch, foreign_repository, new_mapping=new_mapping,
                                 mapping_registry=mapping_registry,
                                 allow_changes=allow_changes, verbose=verbose)
        last_revid = wt.branch.last_revision()
        wt.set_last_revision(last_revid)

        # Adjust file ids in working tree
        for (old_fileid, new_fileid) in determine_fileid_renames(wt.branch.repository.revision_tree(old_revid), wt.basis_tree()):
            path = wt.id2path(old_fileid)
            wt.remove(path)
            wt.add([path], [new_fileid])
    finally:
        wt.unlock()

    return renames


def upgrade_branch(branch, foreign_repository, new_mapping=None, 
                   allow_changes=False, verbose=False):
    """Upgrade a branch to the current mapping version.
    
    :param branch: Branch to upgrade.
    :param foreign_repository: Repository to fetch new revisions from
    :param allow_changes: Allow changes in mappings.
    :param verbose: Whether to print verbose list of rewrites
    """
    revid = branch.last_revision()
    renames = upgrade_repository(branch.repository, foreign_repository, 
              revision_id=revid, new_mapping=new_mapping,
              allow_changes=allow_changes, verbose=verbose)
    if len(renames) > 0:
        branch.generate_revision_history(renames[revid])
    return renames


def check_revision_changed(oldrev, newrev):
    """Check if two revisions are different. This is exactly the same 
    as Revision.equals() except that it does not check the revision_id."""
    if (newrev.inventory_sha1 != oldrev.inventory_sha1 or
        newrev.timestamp != oldrev.timestamp or
        newrev.message != oldrev.message or
        newrev.timezone != oldrev.timezone or
        newrev.committer != oldrev.committer or
        newrev.properties != oldrev.properties):
        raise UpgradeChangesContent(oldrev.revision_id)


def generate_upgrade_map(new_mapping, revs, mapping_registry):
    """Generate an upgrade map for use by bzr-rebase.

    :param new_mapping: Mapping to upgrade revisions to.
    :param revs: Iterator over revisions to upgrade.
    :return: Map from old revids as keys, new revids as values stored in a 
             dictionary.
    """
    rename_map = {}
    # Create a list of revisions that can be renamed during the upgade
    for revid in revs:
        assert isinstance(revid, str)
        try:
            (foreign_revid, mapping) = mapping_registry.parse_revision_id(revid)
        except InvalidRevisionId:
            # Not a foreign revision, nothing to do
            continue
        newrevid = new_mapping.revision_id_foreign_to_bzr(foreign_revid)
        if revid == newrevid:
            continue
        rename_map[revid] = newrevid

    return rename_map

MIN_REBASE_VERSION = (0, 4)

def create_upgrade_plan(repository, foreign_repository, new_mapping,
                        mapping_registry, revision_id=None, allow_changes=False):
    """Generate a rebase plan for upgrading revisions.

    :param repository: Repository to do upgrade in
    :param foreign_repository: Subversion repository to fetch new revisions from.
    :param new_mapping: New mapping to use.
    :param revision_id: Revision to upgrade (None for all revisions in 
        repository.)
    :param allow_changes: Whether an upgrade is allowed to change the contents
        of revisions.
    :return: Tuple with a rebase plan and map of renamed revisions.
    """
    from bzrlib.plugins.rebase.rebase import generate_transpose_plan
    check_rebase_version(MIN_REBASE_VERSION)

    graph = repository.get_graph()
    if revision_id is None:
        potential = repository.all_revision_ids()
    else:
        potential = itertools.imap(lambda (rev, parents): rev, 
                graph.iter_ancestry([revision_id]))
    upgrade_map = generate_upgrade_map(new_mapping, potential, mapping_registry)
   
    # Make sure all the required current version revisions are present
    for revid in upgrade_map.values():
        if not repository.has_revision(revid):
            repository.fetch(foreign_repository, revid)

    if not allow_changes:
        for oldrevid, newrevid in upgrade_map.items():
            oldrev = repository.get_revision(oldrevid)
            newrev = repository.get_revision(newrevid)
            check_revision_changed(oldrev, newrev)

    if revision_id is None:
        heads = repository.all_revision_ids() 
    else:
        heads = [revision_id]

    plan = generate_transpose_plan(graph.iter_ancestry(heads), upgrade_map, 
      graph,
      lambda revid: create_upgraded_revid(revid, new_mapping.upgrade_suffix))
    def remove_parents((oldrevid, (newrevid, parents))):
        return (oldrevid, newrevid)
    upgrade_map.update(dict(map(remove_parents, plan.items())))

    return (plan, upgrade_map)

 
def upgrade_repository(repository, foreign_repository, new_mapping, 
                       mapping_registry, revision_id=None, allow_changes=False, 
                       verbose=False):
    """Upgrade the revisions in repository until the specified stop revision.

    :param repository: Repository in which to upgrade.
    :param foreign_repository: Repository to fetch new revisions from.
    :param new_mapping: New mapping.
    :param revision_id: Revision id up until which to upgrade, or None for 
                        all revisions.
    :param allow_changes: Allow changes to mappings.
    :param verbose: Whether to print list of rewrites
    :return: Dictionary of mapped revisions
    """
    check_rebase_version(MIN_REBASE_VERSION)
    from bzrlib.plugins.rebase.rebase import (
        replay_snapshot, rebase, rebase_todo)

    # Find revisions that need to be upgraded, create
    # dictionary with revision ids in key, new parents in value
    try:
        repository.lock_write()
        foreign_repository.lock_read()
        (plan, revid_renames) = create_upgrade_plan(repository, foreign_repository, 
                                                    new_mapping, mapping_registry,
                                                    revision_id=revision_id,
                                                    allow_changes=allow_changes)
        if verbose:
            for revid in rebase_todo(repository, plan):
                info("%s -> %s" % (revid, plan[revid][0]))
        def fix_revid(revid):
            try:
                (foreign_revid, mapping) = mapping_registry.parse_revision_id(revid)
            except InvalidRevisionId:
                return revid
            return new_mapping.revision_id_foreign_to_bzr(foreign_revid)
        def replay(repository, oldrevid, newrevid, new_parents):
            return replay_snapshot(repository, oldrevid, newrevid, new_parents,
                                   revid_renames, fix_revid)
        rebase(repository, plan, replay)
        return revid_renames
    finally:
        repository.unlock()
        foreign_repository.unlock()

