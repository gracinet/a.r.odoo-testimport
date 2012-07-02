# coding: utf-8
import os
from os.path import join
import stat
import sys, logging
import subprocess
import zc.buildout
from anybox.recipe.openerp.base import BaseRecipe

logger = logging.getLogger(__name__)

class ServerRecipe(BaseRecipe):
    """Recipe for server install and config
    """
    archive_filenames = { '6.0': 'openerp-server-%s.tar.gz',
                         '6.1': 'openerp-%s.tar.gz'}
    recipe_requirements = ('babel',)
    requirements = ('pychart',)
    ws = None

    def __init__(self, *a, **kw):
        super(ServerRecipe, self).__init__(*a, **kw)
        self.gunicorn_entry = dict(direct='core', proxied='proxied').get(
            self.options.get('gunicorn', '').strip().lower())

    def merge_requirements(self):
        """Prepare for installation by zc.recipe.egg

         - add Pillow iff PIL not present in eggs option.
         - (OpenERP >= 6.1) develop the openerp distribution and require it
         - gunicorn's related dependencies if needed

        For PIL, extracted requirements are not taken into account. This way,
        if at some point, 
        OpenERP introduce a hard dependency on PIL, we'll still install Pillow.
        The only case where PIL will have precedence over Pillow will thus be
        the case of a legacy buildout.
        See https://bugs.launchpad.net/anybox.recipe.openerp/+bug/1017252

        Once 'openerp' is required, zc.recipe.egg will take it into account
        and put it in needed scripts, interpreters etc.
        """
        if not 'PIL' in self.options.get('eggs', '').split():
            self.requirements.append('Pillow')
        if self.version_detected[:3] == '6.1':
            develop_dir = self.b_options['develop-eggs-directory']
            zc.buildout.easy_install.develop(self.openerp_dir, develop_dir)
            self.requirements.append('openerp')

        if self.gunicorn_entry:
            self.requirements.extend(('psutil','gunicorn'))

        BaseRecipe.merge_requirements(self)

    def _create_default_config(self):
        """Create a default config file
        """
        if self.version_detected.startswith('6.0'):
            subprocess.check_call([self.script_path, '--stop-after-init', '-s'])
        else:
            sys.path.extend([self.openerp_dir])
            sys.path.extend([egg.location for egg in self.ws])
            from openerp.tools.config import configmanager
            configmanager(self.config_path).save()

    def _create_gunicorn_conf(self):
        """Put a gunicorn_PART.conf.py script in /etc.

        Derived from the standard gunicorn.conf.py shipping with OpenERP.
        """
        port = self.options.get('xmlrpc_port', '8069')
        interface = self.options.get('xmlrpc_interface', '0.0.0.0')
        bind = '%s:%s' % (interface, port)
        qualified_name = 'gunicorn_%s' % self.name
        f = open(join(self.etc, qualified_name + '.conf.py'), 'w')
        conf = """'''Gunicorn configuration script.
Generated by buildout. Do NOT edit.'''
import openerp
bind = %(bind)r
pidfile = %(qualified_name)r + '.pid'
workers = %(workers)d
on_starting = openerp.wsgi.core.on_starting
try:
  when_ready = openerp.wsgi.core.when_ready
except AttributeError: # not in current head of 6.1
  pass
pre_request = openerp.wsgi.core.pre_request
post_request = openerp.wsgi.core.post_request
timeout = %(timeout)d
max_requests = %(max_requests)d

openerp.conf.server_wide_modules = ['web']
conf = openerp.tools.config
""" % dict(bind=bind, qualified_name=qualified_name,
           workers=4, timeout=240, max_requests=2000)

        # forwarding specified options
        prefix = 'options.'
        for opt, val in self.options.items():
            if not opt.startswith(prefix):
                continue
            if opt == 'options.log_level':
                # blindly following the sample script
                val = dict(DEBUG=10, DEBUG_RPC=8, DEBUG_RPC_ANSWER=6,
                           DEBUG_SQL=5, INFO=20, WARNING=30, ERROR=40,
                           CRITICAL=50).get(val.strip().upper(), 30)

            conf += 'conf[%r] = %r' % (opt[len(prefix):], val) + os.linesep
        f.write(conf)
        f.close()

    def _dump_gunicorn_start(self):
        """Dump a gunicorn foreground start script.

        Suitable for external management, such as provided by supervisor.
        TODO XXX GR: relies on bin/gunicorn, which is the latest installed
        version (big mess if several of them)
        """
        qualified_name = 'gunicorn_%s' % self.name
        options = self.options.copy()
        options['scripts'] = 'gunicorn=' + qualified_name
        # gunicorn's main() does not take arguments, that's why we have
        # to resort on hacking sys.argv
        options['initialization'] = (
            "from sys import argv; "
            "argv[1:] = ['openerp:wsgi.%s.application', "
            "            '-c', '%s.conf.py']") % (
            self.gunicorn_entry, join(self.etc, qualified_name))

        zc.recipe.egg.Scripts(self.buildout, '', options).install()

    def _create_startup_script(self):
        """Return startup_script content
        """
        paths = [egg.location for egg in self.ws]
        if self.version_detected[:3] == '6.0':
            paths.append(self.openerp_dir, 'openerp')
            ext = '.py'
            bindir = join(self.openerp_dir, 'bin')
        else:
            ext = ''
            bindir = self.openerp_dir
            # GR TODO: inconsistency, script and conf directly written
            # and not listed in install() return value.
            if self.gunicorn_entry:
                self._create_gunicorn_conf()
                self._dump_gunicorn_start()

        script = ('#!/bin/sh\n'
                  'export PYTHONPATH=%s\n'
                  'cd "%s"\n'
                  'exec %s openerp-server%s -c %s $@') % (
                    ':'.join(paths),
                    bindir,
                    self.buildout['buildout']['executable'],
                    ext,
                    self.config_path)
        return script


