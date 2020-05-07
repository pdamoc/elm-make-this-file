
import json
import os
import sublime
import sublime_plugin
import subprocess
import string
import threading

USER_SETTING_PREFIX = 'elm_language_support_'
ELM_SETTINGS_FILE = 'Elm Make this File.sublime-settings' 

def get_popen_startupinfo():
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo
    else:
        return None
 
class ElmMakeCommand(sublime_plugin.WindowCommand):

    encoding = 'utf-8'
    panel_lock = threading.Lock()
    killed = False
    proc = None

    def run(self, cmd=[], kill=False):
        if kill:
            if self.proc:
                self.killed = True
                self.proc.terminate()
            return

        working_dir = self.window.extract_variables()['file_path']
        self.create_panel(working_dir)

        if self.proc is not None:
            self.proc.terminate()
            self.proc = None

        self.proc = subprocess.Popen(
            self.format_cmd(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=working_dir,
            startupinfo=get_popen_startupinfo()
        )
        self.killed = False

        threading.Thread(
            target=self.read_handle,
            args=(self.proc.stdout,)
        ).start()

    def format_cmd(self, cmd):
        binary, command, file, output = cmd[0:4]

        binary = binary.format(elm_binary=self.get_setting('elm_binary'))

        return [binary, command, file, output] + cmd[4:]

    def create_panel(self, working_dir):
        # Only allow one thread to touch output panel at a time
        with self.panel_lock:
            # implicitly clears previous contents
            self.panel = self.window.create_output_panel('exec')

            settings = self.panel.settings()

            self.panel.assign_syntax('Packages/Elm Make this File/Elm Compile Messages.sublime-syntax')
            settings.set('gutter', False)
            settings.set('scroll_past_end', False)
            settings.set('word_wrap', False) 

            # Enable result navigation
            settings.set(
                'result_file_regex',
                r'^\-\- \w+: (?=.+ \- (.+?):(\d+)(?=:(\d+))?)(.+) \- .*$'
            )
            settings.set('result_base_dir', working_dir)

        preferences = sublime.load_settings('Preferences.sublime-settings')
  
        show_panel_on_build = preferences.get('show_panel_on_build', True)
        if show_panel_on_build:
            self.window.run_command('show_panel', {'panel': 'output.exec'})

    def read_handle(self, handle):
        chunk_size = 2 ** 13
        output = b''
        while True:
            try:
                chunk = os.read(handle.fileno(), chunk_size)
                output += chunk

                if chunk == b'':
                    if output != b'':
                        self.queue_write(self.format_output(output.decode(self.encoding)))
                    raise IOError('EOF')

            except UnicodeDecodeError as e:
                msg = 'Error decoding output using %s - %s'
                self.queue_write(msg % (self.encoding, str(e)))
                break

            except IOError:
                if self.killed:
                    msg = 'Cancelled'
                else:
                    msg = 'Finished'
                    sublime.set_timeout(lambda: self.finish(), 0)
                self.queue_write('[%s]' % msg)
                break

    def queue_write(self, text):
        # Calling set_timeout inside this function rather than inline ensures
        # that the value of text is captured for the lambda to use, and not
        # mutated before it can run.
        sublime.set_timeout(lambda: self.do_write(text), 1)

    def do_write(self, text):
        with self.panel_lock:
            self.panel.set_read_only(False)
            self.panel.run_command('append', {'characters': text})
            self.panel.set_read_only(True)
            sublime.set_timeout(lambda: self.panel.run_command("move_to", {"to": "bof"}), 1)


    def format_output(self, output):
        try:
            data = json.loads(output)
            if data['type'] == 'compile-errors':
                return self.format_errors(data['errors'])
            elif data['type'] == 'error':
                return self.format_compiler_error(data)
            else:
                return 'Unrecognized compiler output:\n' + str(output) + '\n\nPlease report this bug in Elm Language Support.\n\n'
        except ValueError as e:
            return ''

    def format_errors(self, errors):
        return '\n'.join(map(self.format_error, errors)) + '\n'

    def format_error(self, error):
        file = error['path']
        return '\n'.join(map(lambda problem: self.format_problem(file, problem), error['problems']))

    def format_problem(self, file, problem):
        error_format = string.Template('-- $type: $title - $file:$line:$column\n\n$message\n')

        type = 'error'
        title = problem['title']
        line = problem['region']['start']['line']
        column = problem['region']['start']['column']
        message = self.format_message(problem['message'])

        vars = locals()
        vars.pop('self') # https://bugs.python.org/issue23671
        return error_format.substitute(**vars)

    def format_compiler_error(self, error):
        error_format = string.Template('-- $type: $title - $file:1\n\n$message\n')

        type = 'error'
        title = error['title']
        file = error['path']
        message = self.format_message(error['message'])

        vars = locals()
        vars.pop('self') # https://bugs.python.org/issue23671
        return error_format.substitute(**vars)

    def format_message(self, message):
        format = lambda msg: msg if isinstance(msg, str) else msg['string']

        return ''.join(map(format, message))

    def finish(self):
        errs = self.panel.find_all_results()
        if len(errs) == 0:
            sublime.status_message('Build finished')
        else:
            sublime.status_message('Build finished with %d errors' % len(errs))


    def get_setting(self, key, user_key=None):
        package_settings = sublime.load_settings(ELM_SETTINGS_FILE)
        user_settings = self.window.active_view().settings()

        return user_settings.get(user_key or (USER_SETTING_PREFIX + key), package_settings.get(key))
