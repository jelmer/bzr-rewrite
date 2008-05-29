# Copyright (C) 2007 by Jelmer Vernooij <jelmer@samba.org>
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
"""Rebase support.

The Bazaar rebase plugin adds support for rebasing branches to Bazaar.
It adds the command 'rebase' to Bazaar. When conflicts occur when replaying
patches, the user can resolve the conflict and continue the rebase using the
'rebase-continue' command or abort using the 'rebase-abort' command.
"""

from bzrlib.commands import Command, Option, display_command, register_command
from bzrlib.errors import (BzrCommandError, ConflictsInTree, NoSuchFile, 
                           UnrelatedBranches, NoSuchRevision, 
                           UncommittedChanges)
from bzrlib.trace import info, warning

version_info = (0, 3, 0, 'final', 0)
if version_info[3] == 'final':
    version_string = '%d.%d.%d' % version_info[:3]
else:
    version_string = '%d.%d.%d%s%d' % version_info
__version__ = version_string
__author__ = 'Jelmer Vernooij <jelmer@samba.org>'

min_compatible_bzr_version = (0, 92)

def check_bzrlib_version(desired):
    """Check that bzrlib is compatible.

    :param desired: Desired version.

    If version is < all compatible version, assume incompatible.
    """
    import bzrlib
    bzrlib_version = bzrlib.version_info[:2]
    if bzrlib.version_info[3] == 'dev':
        return
    if bzrlib_version < desired:
        warning('Installed bzr version %s is too old to be used with bzr-rebase'
                ' %s.' % (bzrlib.__version__, __version__))
        # Not using BzrNewError, because it may not exist.
        raise Exception, ('Version mismatch', desired)


check_bzrlib_version(min_compatible_bzr_version)

def find_last_common_revid(revhistory1, revhistory2):
    """Find the last revision two revision histories have in common.

    :param revhistory1: First revision history (list of revids)
    :param revhistory2: First revision history (list of revids)
    :return: The last common revision id
    """
    for revid in reversed(revhistory1):
        if revid in revhistory2:
            return revid

    raise UnrelatedBranches()


class cmd_rebase(Command):
    """Re-base a branch.

    Rebasing is the process of taking a branch and modifying the history so
    that it appears to start from a different point. This can be useful
    to clean up the history before submitting your changes. The tree at the
    end of the process will be the same as if you had merged the other branch,
    but the history will be different.

    The command takes the location of another branch on to which the branch in
    the current working directory will be rebased. If a branch is not specified
    then the parent branch is used, and this is usually the desired result.

    The first step identifies the revisions that are in the current branch that
    are not in the parent branch. The current branch is then set to be at the
    same revision as the target branch, and each revision is replayed on top
    of the branch. At the end of the process it will appear as though your
    current branch was branched off the current last revision of the target.

    Each revision that is replayed may cause conflicts in the tree. If this
    happens the command will stop and allow you to fix them up. Resolve the
    commits as you would for a merge, and then run 'bzr resolve' to marked
    them as resolved. Once you have resolved all the conflicts you should
    run 'bzr rebase-continue' to continue the rebase operation.

    If conflicts are encountered and you decide that you do not wish to continue
    you can run 'bzr rebase-abort'.

    The '--onto' option allows you to specify a different revision in the
    target branch to start at when replaying the revisions. This means that
    you can change the point at which the current branch will appear to be
    branched from when the operation completes.
    """
    takes_args = ['upstream_location?']
    takes_options = ['revision', 'merge-type', 'verbose',
        Option('dry-run',
            help="Show what would be done, but don't actually do anything."),
        Option('always-rebase-merges',
            help="Don't skip revisions that merge already present revisions."),
        Option('pending-merges', 
            help="Rebase pending merges onto local branch"),
        Option('onto', help='Different revision to replay onto.',
            type=str)]
    
    @display_command
    def run(self, upstream_location=None, onto=None, revision=None, 
            merge_type=None, verbose=False, dry_run=False, 
            always_rebase_merges=False, pending_merges=False):
        from bzrlib.branch import Branch
        from bzrlib.revisionspec import RevisionSpec
        from bzrlib.workingtree import WorkingTree
        from rebase import (generate_simple_plan, rebase, rebase_plan_exists, 
                            read_rebase_plan, remove_rebase_plan, 
                            workingtree_replay, write_rebase_plan,
                            regenerate_default_revid,
                            rebase_todo)
        if revision is not None and pending_merges:
            raise BzrCommandError("--revision and --pending-merges are mutually exclusive")

        wt = WorkingTree.open_containing(".")[0]
        wt.lock_write()
        if upstream_location is None:
            if pending_merges:
                upstream_location = "."
            else:
                upstream_location = wt.branch.get_push_location()
                if upstream_location is None:
                    upstream_location = wt.branch.get_parent()
                info("Rebasing on %s" % upstream_location)
        upstream = Branch.open_containing(upstream_location)[0]
        upstream_repository = upstream.repository
        upstream_revision = upstream.last_revision()
        try:
            # Abort if there already is a plan file
            if rebase_plan_exists(wt):
                raise BzrCommandError("A rebase operation was interrupted. Continue using 'bzr rebase-continue' or abort using 'bzr rebase-abort'")

            start_revid = None
            stop_revid = None
            if revision is not None:
                if len(revision) == 1:
                    if revision[0] is not None:
                        stop_revid = revision[0].as_revision_id(wt.branch)
                elif len(revision) == 2:
                    if revision[0] is not None:
                        start_revid = revision[0].as_revision_id(wt.branch)
                    if revision[1] is not None:
                        stop_revid = revision[1].as_revision_id(wt.branch)
                else:
                    raise BzrCommandError(
                        "--revision takes only one or two arguments")

            if pending_merges:
                wt_parents = wt.get_parent_ids()
                if len(wt_parents) in (0, 1):
                    raise BzrCommandError("No pending merges present.")
                elif len(wt_parents) > 2:
                    raise BzrCommandError("Rebasing more than one pending merge not supported")
                stop_revid = wt_parents[1]
                assert stop_revid is not None, "stop revid invalid"

            # Pull required revisions
            wt.branch.repository.fetch(upstream_repository, upstream_revision)
            if onto is None:
                onto = upstream.last_revision()
            else:
                rev_spec = RevisionSpec.from_string(onto)
                onto = rev_spec.as_revision_id(upstream)

            wt.branch.repository.fetch(upstream_repository, onto)

            if stop_revid is not None:
                revhistory = list(wt.branch.repository.iter_reverse_revision_history(stop_revid))
                revhistory.reverse()
            else:
                revhistory = wt.branch.revision_history()

            if start_revid is None:
                common_revid = find_last_common_revid(revhistory, 
                                                 upstream.revision_history())
                if common_revid == upstream.last_revision():
                    self.outf.write("No revisions to rebase.\n")
                    return
                try:
                    start_revid = revhistory[revhistory.index(common_revid)+1]
                except NoSuchRevision:
                    raise BzrCommandError("No common revision, please specify --revision")

            # Create plan
            replace_map = generate_simple_plan(
                    revhistory, start_revid, stop_revid, onto,
                    wt.branch.repository.get_ancestry(onto),
                    wt.branch.repository.get_graph(),
                    lambda revid: regenerate_default_revid(wt.branch.repository, revid),
                    not always_rebase_merges
                    )

            if verbose:
                todo = list(rebase_todo(wt.branch.repository, replace_map))
                info('%d revisions will be rebased:' % len(todo))
                for revid in todo:
                    info("%s" % revid)

            # Check for changes in the working tree.
            if (not pending_merges and 
                wt.basis_tree().changes_from(wt).has_changed()):
                raise UncommittedChanges(wt)

            if not dry_run:
                # Write plan file
                write_rebase_plan(wt, replace_map)

                # Start executing plan
                try:
                    rebase(wt.branch.repository, replace_map, 
                           workingtree_replay(wt, merge_type=merge_type))
                except ConflictsInTree:
                    raise BzrCommandError("A conflict occurred replaying a commit. Resolve the conflict and run 'bzr rebase-continue' or run 'bzr rebase-abort'.")
                # Remove plan file
                remove_rebase_plan(wt)
        finally:
            wt.unlock()


class cmd_rebase_abort(Command):
    """Abort an interrupted rebase

    """
    
    @display_command
    def run(self):
        from rebase import read_rebase_plan, remove_rebase_plan, complete_revert
        from bzrlib.workingtree import WorkingTree
        wt = WorkingTree.open_containing('.')[0]
        wt.lock_write()
        try:
            # Read plan file and set last revision
            try:
                last_rev_info = read_rebase_plan(wt)[0]
            except NoSuchFile:
                raise BzrCommandError("No rebase to abort")
            complete_revert(wt, [last_rev_info[1]])
            remove_rebase_plan(wt)
        finally:
            wt.unlock()


class cmd_rebase_continue(Command):
    """Continue an interrupted rebase after resolving conflicts

    """
    takes_options = ['merge-type']
    
    @display_command
    def run(self, merge_type=None):
        from rebase import (commit_rebase, rebase, rebase_plan_exists, 
                            read_rebase_plan, read_active_rebase_revid, 
                            remove_rebase_plan, workingtree_replay)
        from bzrlib.workingtree import WorkingTree
        wt = WorkingTree.open_containing('.')[0]
        wt.lock_write()
        try:
            # Abort if there are any conflicts
            if len(wt.conflicts()) != 0:
                raise BzrCommandError("There are still conflicts present. "
                                      "Resolve the conflicts and then run "
                                      "'bzr resolve' and try again.")
            # Read plan file
            try:
                replace_map = read_rebase_plan(wt)[1]
            except NoSuchFile:
                raise BzrCommandError("No rebase to continue")
            oldrevid = read_active_rebase_revid(wt)
            if oldrevid is not None:
                oldrev = wt.branch.repository.get_revision(oldrevid)
                commit_rebase(wt, oldrev, replace_map[oldrevid][0])
            try:
                # Start executing plan from current Branch.last_revision()
                rebase(wt.branch.repository, replace_map, 
                        workingtree_replay(wt, merge_type=merge_type))
            except ConflictsInTree:
                raise BzrCommandError("A conflict occurred replaying a commit. Resolve the conflict and run 'bzr rebase-continue' or run 'bzr rebase-abort'.")
            # Remove plan file  
            remove_rebase_plan(wt)
        finally:
            wt.unlock()


class cmd_rebase_todo(Command):
    """Print list of revisions that still need to be replayed as part of the 
    current rebase operation.

    """
    
    def run(self):
        from rebase import (rebase_todo, read_rebase_plan, 
                            read_active_rebase_revid)
        from bzrlib.workingtree import WorkingTree
        wt = WorkingTree.open_containing('.')[0]
        wt.lock_read()
        try:
            try:
                replace_map = read_rebase_plan(wt)[1]
            except NoSuchFile:
                raise BzrCommandError("No rebase in progress")
            currentrevid = read_active_rebase_revid(wt)
            if currentrevid is not None:
                info("Currently replaying: %s" % currentrevid)
            for revid in rebase_todo(wt.branch.repository, replace_map):
                info("%s -> %s" % (revid, replace_map[revid][0]))
        finally:
            wt.unlock()


class cmd_replay(Command):
    """Replay commits from another branch on top of this one.

    """
    
    takes_options = ['revision', 'merge-type']
    takes_args = ['location']

    def run(self, location, revision=None, merge_type=None):
        from bzrlib.branch import Branch
        from bzrlib.workingtree import WorkingTree
        from bzrlib import ui
        from rebase import regenerate_default_revid, replay_delta_workingtree

        from_branch = Branch.open_containing(location)[0]

        if revision is not None:
            if len(revision) == 1:
                if revision[0] is not None:
                    todo = [revision[0].as_revision_id(from_branch)]
            elif len(revision) == 2:
                from_revno, from_revid = revision[0].in_history(from_branch)
                to_revno, to_revid = revision[1].in_history(from_branch)
                if to_revid is None:
                    to_revno = from_branch.revno()
                todo = []
                for revno in range(from_revno, to_revno + 1):
                    todo.append(from_branch.get_rev_id(revno))
            else:
                raise BzrCommandError(
                    "--revision takes only one or two arguments")
        else:
            raise BzrCommandError("--revision is mandatory")

        wt = WorkingTree.open(".")
        wt.lock_write()
        pb = ui.ui_factory.nested_progress_bar()
        try:
            for revid in todo:
                pb.update("replaying commits", todo.index(revid), len(todo))
                wt.branch.repository.fetch(from_branch.repository, revid)
                newrevid = regenerate_default_revid(wt.branch.repository, revid)
                replay_delta_workingtree(wt, revid, newrevid, 
                                         [wt.last_revision()], 
                                         merge_type=merge_type)
        finally:
            pb.finished()
            wt.unlock()


for cmd in [cmd_replay, cmd_rebase, cmd_rebase_abort, cmd_rebase_continue, 
            cmd_rebase_todo]:
    register_command(cmd)


def test_suite():
    """Determine the testsuite for bzr-rebase."""
    from unittest import TestSuite
    from bzrlib.tests import TestUtil

    loader = TestUtil.TestLoader()
    suite = TestSuite()
    testmod_names = ['test_blackbox', 'test_rebase', 'test_maptree']
    suite.addTest(loader.loadTestsFromModuleNames(
                              ["%s.%s" % (__name__, i) for i in testmod_names]))

    return suite

