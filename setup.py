#!/usr/bin/env python

from info import *

if __name__ == '__main__':
    from distutils.core import setup

    version_string = ".".join([str(v) for v in bzr_plugin_version[:3]])

    setup(name='bzr-rewrite',
          description='Rebase plugin for Bazaar',
          keywords='plugin bzr rebase',
          version=version_string,
          url='http://bazaar-vcs.org/Rebase',
          download_url='http://samba.org/~jelmer/bzr/bzr-rebase-%s.tar.gz' % version_string,
          license='GPLv3 or later',
          author='Jelmer Vernooij',
          author_email='jelmer@samba.org',
          long_description="""
          Hooks into Bazaar and provides commands for rebasing.
          """,
          package_dir={'bzrlib.plugins.rebase':'.'},
          packages=['bzrlib.plugins.rebase']
    )
