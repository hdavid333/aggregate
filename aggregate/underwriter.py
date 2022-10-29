# from collections import namedtuple
import numpy as np
import logging
import pandas as pd
from pathlib import Path
import re
from IPython.display import HTML, display
# from inspect import signature

from .portfolio import Portfolio
from .distributions import Aggregate, Severity
from .spectral import Distortion
from .parser import UnderwritingLexer, UnderwritingParser
from .utilities import logger_level, round_bucket, Answer, LoggerManager, pprint, show_fig

logger = logging.getLogger(__name__)


# rejected: immutable
# WriteAnswer = namedtuple('WriteAnswer', ['kind', 'name', 'spec', 'program', 'object'])


class Underwriter(object):
    """
    The ``Underwriter`` class manages the creation of Aggregate and Portfolio objects, and
    maintains a database of standard Severity (curves) and Aggregate (unit or line level) objects.
    The ``Underwriter`` knows about all the business that is written!

    * Handles persistence to and from agg files
    * Is interface into program parser
    * Handles safe lookup from database for parser

    Objects have a kind (agg, port, or sev) and a name. E.g. agg MyAgg ... has kind agg and name MyAgg.
    They have a representation as a program. When the program is interpreted it produces a string spec
    that can be used to create the object. The static method factory can create any object from the
    (kind, name, spec, program) quartet, though, strictly, program is not needed.

    The underwriter knowledge is stored in a dataframe indexed by kind and name with columns
    spec and program.

    """

    def __init__(self, name='Rory', databases=None, update=False, log2=10, debug=False):
        """
        Create underwriter object.

        :param name: Name of the underwriter object. Default is named after Rory Cline, the best
        underwriter I know.
        :param databases: if None: nothing loaded; if 'default' (installed) or 'site' (user,
        in ~/aggregate/databases) database *.agg files in default or site directory are loaded.
        If 'all' both default and site databases loaded. A string refers to a single database;
        an interable of strings is also valid. See `read_database` for search path.
        :param update: If True objects are updated after being created.
        :param log2: Default log2 value.
        :param debug: run parser in debug mode
        that are within Portfolios.
        """

        self.name = name
        self.update = update
        if log2 <= 0:
            raise ValueError('log2 must be > 0. The number of buckets used equals 2**log2.')
        self.log2 = log2
        self.debug = debug
        self.lexer = UnderwritingLexer()
        self.parser = UnderwritingParser(self.safe_lookup, debug)
        # stop pyCharm complaining
        # knowledge - accounts and line known to the underwriter
        self._knowledge = pd.DataFrame(columns=['kind', 'name', 'spec', 'program'], dtype=object).set_index(
            ['kind', 'name'])
        if databases == 'all':
            databases = ['default', 'site']
        elif type(databases) == str:
            databases = [databases]
        # default_dir is installed by pip and contain installation files
        default_dir = Path(__file__).parent / 'agg'
        # site dir is in Users's home directory and stores their files
        site_dir = Path.home() / 'aggregate/databases'

        self.site_dir = site_dir
        # check site dir exists
        self.site_dir.mkdir(parents=True, exist_ok=True)

        self.default_dir = default_dir

        # make sure all database entries are stored:
        if databases is None:
            # nothing to do
            databases = []

        if 'default' in databases:
            # add all databases in default_dir
            databases.remove('default')
            for fn in default_dir.glob('*.agg'):
                self.read_database(fn)

        if 'site' in databases:
            # add all user databases
            databases.remove('site')
            databases += list(site_dir.glob('*.agg'))

        for fn in databases:
            self.read_database(fn)

    def read_database(self, fn):
        """
        read database of curves, aggs and portfolios. These can live in the default directory
        that is part of the instalation or ~/aggregate/

        fn can be a string filename, with or without extension. A .agg extension is
        added if there is no suffix. Search path:

        * in the current dir
        * in site_dir (user)
        * in default_dir (installation)

        :param fn: database file name

        """

        p = Path(fn)
        if p.suffix == '':
            p = p.with_suffix('.agg')
        if p.exists():
            db_path = p
        elif (self.site_dir / p).exists():
            db_path = self.site_dir / fn
        elif (self.default_dir / p).exists():
            db_path = self.default_dir / p
        else:
            logger.error(f'Database {fn} not found. Ignoring.')
            return

        try:
            program = db_path.read_text(encoding='utf-8')
        except:
            logger.error(f'Error reading requested database {db_path.name}. Ignoring.')
        else:
            # read in, parse, save to sev/agg/port dictionaries
            # throw away answer...not creating anything
            logger.info(f'Reading database {fn}...')
            n = len(self._knowledge)
            self.interpret_program(program)
            n = len(self._knowledge) - n
            logger.info(f'Database {fn} read into knowledge, adding {n} entries.')

    def __getitem__(self, item):
        """
        handles self[item]

        item = 'Name' for all objects called Name
        item = ("kind", "name") for object of kind kind called name

        subscriptable: try user portfolios, b/in portfolios, line, severity
        to access specifically use severity or line methods

        :param item:
        :return:
        """
        # much less fancy version:
        if not isinstance(item, (str, tuple)):
            raise ValueError(f'item must be a str (name of object) or tuple (kind, name), not {type(item)}.')

        try:
            if type(item) == str:
                # name == item, any type
                rows = self._knowledge.xs(item, axis=0, level=1, drop_level=False)
            elif type(item) == tuple:
                # return a dataframe row
                rows = self._knowledge.loc[[item]]
        except KeyError:
            raise KeyError(f'Item {item} not found.')
        except TypeError as e:
            # TODO fix this "TypeError: unhashable type: 'slice'"
            raise KeyError(f'getitem TypeError looking for {item}, {e}')
        else:
            if len(rows) == 1:
                kind, name, spec, program = rows.reset_index().iloc[0]
                return Answer(kind=kind, name=name, spec=spec, program=program, object=None)
            else:
                raise KeyError(f'Error: no unique object found matching {item}. Found {len(rows)} objects.')

    def _repr_html_(self):
        s = [f'<p><h3>Underwriter {self.name}</h3>',
             f'Knowledge contains {len(self._knowledge)} programs. '
             'Run <code>build.knowledge</code> for a DataFrame listing by kind and name. '
             'Run <code>build.show(name)</code> for more details entry <code>name</code>, '
             'accepts wildcards and regular expressions.'
             '</p>'
             # '<br>',
             # self.knowledge.to_html(),
             f'<p>Settings: '
             ]
        for k in ['log2', 'update', 'debug']:
            s.append(f'<span style="color: red;">{k}</span>: {getattr(self, k)}; ')
        return '\n'.join(s) + '</p>'

    def factory(self, answer):
        """
        Create object of kind from spec, a dictionary.
        Creating from uw obviously needs the uw, so this is NOT a staticmethod!

        :param answer an Answer class with members kind, name, spec, and program
        :return: creates answer.object
        """

        kind, name, spec, program, obj = answer.values()

        if obj is not None:
            logger.error(f'Surprising: obj from Answer not None, type {type(obj)}. It will be overwritten.')

        if kind == 'agg':
            obj = Aggregate(**spec)
            obj.program = program
        elif kind == 'port':
            # Portfolio expects name, agg_list, uw
            # agg list is a list of spects that can be passed to Aggregate
            # need to drop the 'agg', name before the spec that gets returned
            # by the parser. Hence:
            agg_list = [k for i, j, k in spec['spec']]
            obj = Portfolio(name, agg_list, uw=self)
            obj.program = program
        elif kind == 'sev':
            if 'sev_wt' in spec and spec['sev_wt'] != 1:
                logger.warning(
                    f'Mixed severity cannot be created, returning spec. You had {spec["sev_wt"]}, expected 1')
                obj = None
            else:
                obj = Severity(**spec)
                # ? set outside...
                obj.program = program
        elif kind == 'distortion':
            obj = Distortion(**spec)
            # ? set outside
            obj.program = program
        else:
            ValueError(f'Cannot build {kind} objects')

        # update the object
        answer['object'] = obj
        return answer

    @property
    def knowledge(self):
        return self._knowledge.sort_index()[['program', 'spec']]

    def write(self, portfolio_program, log2=0, bs=0, update=None, **kwargs):
        """
        Write a natural language program. Write carries out the following steps.

        1. Read in the program and cleans it (e.g. punctuation, parens etc. are
           removed and ignored, replace ; with new line etc.)
        2. Parse line by line to create a dictionary definition of sev, agg or port objects.
        3. Replace sev.name, agg.name and port.name references with their objects.
        5. If update set, update all created objects.

        Sample input

        ::

            port MY_PORTFOLIO
                agg Line1 20  loss 3 x 2 sev gamma 5 cv 0.30 mixed gamma 0.4
                agg Line2 10  claims 3 x 2 sevgamma 12 cv 0.30 mixed gamma 1.2
                agg Line 3100  premium at 0.4 3 x 2 sev 4 @ lognormal 3 cv 0.8 fixed 1

        The indents are required if each agg item appears on a new line.

        See parser for full language spec! See Aggregate class for many examples.

        Reasonable kwargs:

        * **bs**
        * **log2**
        * **update** overrides class default
        * **add_exa** should port.add_exa add the exa related columns to the output?

        :param portfolio_program:
        :param update: override class default
        :param kwargs: passed to object's update method if update==True
        :return: single created object or dictionary name: object
        """

        # prepare for update
        # what / how to do; little awkward: to make easier for user have to strip named update args
        # out of kwargs
        if update is None:
            update = self.update

        if update is True and log2 == 0:
            log2 = self.log2

        # first see if portfolio_program refers to a built-in object
        try:
            # calls __getitem__
            answer = self[portfolio_program]
        except (LookupError, TypeError):
            logger.debug(f'underwriter.write | object not found, processing as a program.')
        else:
            logger.debug(f'underwriter.write | {answer.kind} object found.')
            answer = self.factory(answer)
            if update:
                answer.object.update(log2, bs, **kwargs)
            # rationalize return to be the same as parsed programs
            # TODO test this code
            return [answer]

        # if you fall through to here then the portfolio_program did not refer to a built-in object
        # run the program, get the interpreter return value, the irv, which contains kind/name->spec,program
        irv = self.interpret_program(portfolio_program)
        rv = []
        for answer in irv:
            # create objects and update if needed
            answer = self.factory(answer)
            if answer not in irv:
                logger.error('OK THAT FAILED' * 20)
            if answer.object is not None:
                # this can fail for named mixed severities, which can only
                # be created in context of an agg... that behaviour is
                # useful for named severities though... hence:
                if update:
                    update_method = getattr(answer.object, 'update', None)
                    if update_method is not None:
                        update_method(log2, bs, **kwargs)
            rv.append(answer)

        # report on what has been done
        if rv is None:
            logger.warning(f'Program did not contain any output')
        else:
            if len(rv):
                logger.info(f'Program created {len(rv)} objects.')

        # return created objects
        return rv

    def write_from_file(self, file_name, log2=0, bs=0, update=False, **kwargs):
        """
        Read program from file. Delegates to write.

        :param file_name:
        :param log2:
        :param bs:
        :param update:
        :param kwargs:
        :return:
        """
        portfolio_program = Path(file_name).read_text(encoding='utf-8')
        return self.write(portfolio_program, log2=log2, bs=bs, update=update, **kwargs)

    def interpret_program(self, portfolio_program):
        """
        Preprocess and then parse a program one line at a time. Each output is
        stored in the Underwriter's knowledge database. No objects are created.

        Error handling through parser.

        :param portfolio_program:
        :return:
        """

        # Preprocess ---------------------------------------------------------------------
        portfolio_program = self.lexer.preprocess(portfolio_program)

        # create return value list
        rv = []

        # Parse and Postprocess-----------------------------------------------------------
        # self.parser.reset()
        # program_line_dict = {}
        for program_line in portfolio_program:
            logger.debug(program_line)
            # preprocessor only returns lines of length > 0
            try:
                # parser returns the type, name, and spec of the object
                # this is where you can marry up with the program
                kind, name, spec = self.parser.parse(self.lexer.tokenize(program_line))
            except ValueError as e:
                if isinstance(e.args[0], str):
                    logger.error(e)
                    raise e
                else:
                    t = e.args[0].type
                    v = e.args[0].value
                    i = e.args[0].index
                    txt2 = program_line[0:i] + f'>>>' + program_line[i:]
                    logger.error(f'Parse error in input "{txt2}"\nValue {v} of type {t} not expected')
                    raise e
            else:
                # store in uw dictionary and create if needed
                logger.info(f'answer out: {kind} object {name} parsed successfully...adding to knowledge')
                self._knowledge.loc[(kind, name), :] = [spec, program_line]
                rv.append(Answer(kind=kind, name=name, spec=spec, program=program_line, object=None))

        return rv

    # @staticmethod
    # def add_defaults(dict_in, kind='agg'):
    #     """
    #     add default values to dict_inin. Leave existing values unchanged
    #     Used to output to a data frame, where you want all columns completed
    #
    #     :param dict_in:
    #     :param kind:
    #     :return:
    #     """
    #
    #     print('running add_defaults\n' * 10)
    #
    #     # use inspect to get the defaults
    #     # obtain signature
    #     sig = signature(Aggregate.__init__)
    #
    #     # self and name --> bound signature
    #     bs = sig.bind(None, '')
    #     bs.apply_defaults()
    #     # remove self
    #     bs.arguments.pop('self')
    #     defaults = bs.arguments
    #
    #     if kind == 'agg':
    #         defaults.update(dict_in)
    #
    #     elif kind == 'sev':
    #         for k, v in defaults.items():
    #             if k[0:3] == 'sev' and k not in dict_in and k != 'sev_wt':
    #                 dict_in[k] = v

    def safe_lookup(self, buildinid):
        """
        Lookup buildinid=kind.name in uw to find expected kind and merge safely into self.arg_dict.

        Different from getitem because it splits the item into kind and name and double
        checks you get the expected kind.

        :param buildinid:  a string in kind.name format
        :return:
        """

        kind, name = buildinid.split('.')
        try:
            # lookup in Underwriter
            answer = self[(kind, name)]
            found_kind, found_name, spec, program, _ = answer.values()
        except LookupError as e:
            logger.error(f'ERROR id {kind}.{name} not found in the knowledge.')
            raise e
        logger.debug(f'UnderwritingParser.safe_lookup | retrieved {kind}.{name} as type {found_kind}.{found_name}')
        if found_kind != kind:
            raise ValueError(f'Error: type of {name} is  {found_kind}, not expected {kind}')
        # don't want to pass back the original
        spec = spec.copy()
        return spec

    @staticmethod
    def logger_level(level):
        """
        Convenience function.

        :param level:
        :return:
        """
        # set logger_level for all aggregate loggers
        logger_level(level)

    def build(self, program, update=True, log2=0, bs=0, log_level=None, **kwargs):
        """
        Convenience function to make work easy for the user. Intelligent auto updating.
        Detects discrete distributions and sets ``bs = 1``.

        ``build`` method sets loger level to 30 by default.

        ``__call__`` is set equal to ``build``.

        :param program:
        :param update: build's update
        :param log2: 0 is default: Estimate log2 for discrete and self.log2 for all others. Inupt value over-rides
        and cancels discrete computation (good for large discrete outcomes where bucket happens to be 1.)
        :param bs:
        :param log_level:
        :param kwargs: passed to update, e.g., padding. Note force_severity=True is applied automatically
        :return: created object(s)
        """

        # automatically puts level back at the end
        if log_level is not None:
            lm = LoggerManager(log_level)

        # make stuff
        # write will return a dict with keys (kind, name) and value a WriteAnswer namedtuple
        rv = self.write(program, update=False, force_severity=True)

        if rv is None or len(rv) == 0:
            logger.warning('build produced no output')
            return None

        # in this loop bs_ and log2_ are the values actually used for each update;
        # they do not overwrite the input default values
        for answer in rv:
            if answer.object is None:
                # object not created
                logger.info(f'Object {answer.name} of kind {answer.kind} returned as '
                            'a spec; no further processing.')
            elif isinstance(answer.object, Aggregate) and update is True:
                # try to guess good defaults
                d = answer.spec
                if d['sev_name'] == 'dhistogram' and log2 == 0:
                    bs_ = 1
                    # how big?
                    if d['freq_name'] == 'fixed':
                        max_loss = np.max(d['sev_xs']) * d['exp_en']
                    elif d['freq_name'] == 'empirical':
                        max_loss = np.max(d['sev_xs']) * max(d['freq_a'])
                    else:
                        # normal approx on count
                        max_loss = np.max(d['sev_xs']) * d['exp_en'] * (1 + 3 * d['exp_en'] ** 0.5)
                    # binaries are 0b111... len-2 * 2 is len - 1
                    log2_ = len(bin(int(max_loss))) - 1
                    logger.info(f'({answer.kind}, {answer.name}): Discrete mode, '
                                'using bs=1 and log2={log2_}')
                else:
                    if log2 == 0:
                        log2_ = self.log2
                    else:
                        log2_ = log2
                    if bs == 0:
                        bs_ = round_bucket(answer.object.recommend_bucket(log2_))
                    else:
                        bs_ = bs
                    logger.info(f'({answer.kind}, {answer.name}): Normal mode, using bs={bs_} (1/{1/bs_}) and log2={log2_}')
                try:
                    answer.object.update(log2=log2_, bs=bs_, debug=self.debug, force_severity=True, **kwargs)
                except ZeroDivisionError as e:
                    logger.error(e)
                except AttributeError as e:
                    logger.error(e)
            elif isinstance(answer.object, Severity):
                # there is no updating for severities
                pass
            elif isinstance(answer.object, Portfolio) and update is True:
                # figure stuff
                if log2 == -1:
                    log2_ = 13
                else:
                    log2_ = log2
                if bs == 0:
                    bs_ = answer.object.best_bucket(log2_)
                else:
                    bs_ = bs
                logger.info(f'updating with {log2}, bs=1/{1 / bs_}')
                logger.info(f'({answer.kind}, {answer.name}): bs={bs_} and log2={log2_}')
                answer.object.update(log2=log2_, bs=bs_, remove_fuzz=True, force_severity=True,
                                     debug=self.debug, **kwargs)
            elif isinstance(answer.object, Distortion):
                pass
            elif isinstance(answer.object, (Aggregate, Portfolio)) and update is False:
                pass
            else:
                logger.warning(f'Unexpected: output kind is {type(answer.object)}. (expr/number?)')
                pass

        if len(rv) == 1:
            # only one output...just return that
            # retun object if it exists, otherwise the ans namedtuple
            for answer in rv:
                if answer.object is None:
                    return answer
                else:
                    return answer.object
        else:
            # multiple outputs, see if there is just one portfolio...this is not ideal?!
            ports_found = 0
            for answer in rv:
                if answer.kind == 'port':
                    ports_found += 1
            if ports_found == 1:
                # if only one, it must be answer
                if answer.object is None:
                    return answer
                else:
                    return answer.object
        # in all other cases, return the full list
        return rv

    __call__ = build

    def interpreter_file(self, *, filename='', where=''):
        """
        Run a suite of test programs. For detailed analysis, run_one.
        filename is a string or Path. If a csv it is read into
        a dataframe, with the first column used as index. If it
        is an agg file (e.g. an agg database), it is preprocessed
        to remove comments and replace \n\t agg with a space, then
        split on new lines and converted to a dataframe.
        Other file formats are rejected.

        These methods are called interpreter_... rather than
        interpret_... because they are for testing and debugging
        the interpreter, not for actually interpreting anything!

        """
        if filename == '':
            filename = Path.home() / 'aggregate/tests/test_suite.csv'
        elif type(filename) == str:
            filename = Path(filename)
        if filename.suffix == '.csv':
            df = pd.read_csv(filename, index_col=0)
        elif filename.suffix == '.agg':
            txt = filename.read_text(encoding='utf-8')
            stxt = re.sub('\n\tagg', ' agg', txt, flags=re.MULTILINE)
            stxt = stxt.split('\n')
            stxt = [i for i in stxt if len(i) and i[0] != '#']
            df = pd.DataFrame(stxt, columns=['program'])
        else:
            raise ValueError(f'File suffix must be .csv or .agg, not {filename.suffix}')
        if where != '':
            df = df.loc[df.index.str.match(where)]
        # add One severity if not input
        # if txt.find('sev One dsev [1]') < 0:
        #     logger.info('Adding One to knowledge.')
        self.write('sev One dsev [1]')
        return self._interpreter_work(df.iterrows())

    def interpreter_line(self, program, name='one off', debug=True):
        """
        Interpret single line of code  in debug mode.
        name is index of output
        """

        return self._interpreter_work(iter([(name, program)]), debug=debug)

    def interpreter_list(self, program_list):
        """
        Interpret elements in a list in debug mode.
        """
        return self._interpreter_work(list(enumerate(program_list)), debug=True)

    def _interpreter_work(self, iterable, debug=False):
        """
        Do all the work for the test, allows input to be marshalled into the tester
        in different ways. Unlike production interpret_program, runs one line at a time.
        Each line is preprocessed and then run through a clean parser, and the output
        analyzed.

        Last column, program as input is only changed if the preprocessor changes the program

        :return: DataFrame
        """
        lexer = UnderwritingLexer()
        parser = UnderwritingParser(self.safe_lookup, debug)
        ans = {}
        errs = 0
        no_errs = 0
        # detect non-trivial change
        f = lambda x, y: 'same' if x.replace(' ', '') == y.replace(' ', '').replace('\t', '') else y
        for test_name, program in iterable:
            if type(program) != str:
                program_in = program[0]
                program = lexer.preprocess(program_in)
            else:
                program_in = program
                program = lexer.preprocess(program_in)
            err = 0
            if len(program) == 1:
                program = program[0]
                try:
                    # print(program)
                    kind, name, spec = parser.parse(lexer.tokenize(program))
                except (ValueError, TypeError) as e:
                    errs += 1
                    err = 1
                    kind = program.split()[0]
                    # get something to say about the error
                    ea = getattr(e, 'args', None)
                    if ea is not None:
                        # t = getattr(ea[0], 'type', ea[0])
                        # v = getattr(ea[0], 'value', ea[0])
                        i = getattr(ea[0], 'index', 0)
                        if type(i) != int:  i = 0
                        # print(i, ea)
                        txt = program[0:i] + f'>>>' + program[i:]
                        name = 'parse error'
                    else:
                        txt = str(e)
                        name = 'other error'
                    spec = txt
                else:
                    no_errs += 1
                ans[test_name] = [kind, err, name, spec, program, f(program, program_in)]
            elif len(program) > 1:
                logger.info(f'{program_in} preprocesses to {len(program)} lines; not processing.')
                logger.info(program)
                ans[test_name] = ['multiline', err, None, None, program, program_in]
            else:
                logger.info(f'{program_in} preprocesses to a blank line; ignoring.')
                ans[test_name] = ['blank', err, None, None, program, program_in]

        df_out = pd.DataFrame(ans,
                              index=['kind', 'error', 'name', 'output', 'preprocessed program',
                                     'program']).T
        df_out.index.name = 'index'
        return df_out

    def show(self, regex, kind='agg', plot=True, show=True, logger_level=30):
        """
        Create from knowledge by name or match to name.
        Optionally plot. Returns the created object plus dataframe with more detailed information.
        ??How diff from describe??
        Allows exploration of pre-loaded databases.

        Eg regex = "A.*[234] for A...2, 3 and 4.

        Examples.
        ::
            from aggregate.utilities import pprint
            # pretty print all prgrams starting A; no object creation
            build.show('^A.*', 'agg', False, False).program.apply(lambda x: pprint(x, html=True));

            # build and plot A..234
            ans, df = build.show('^A.*')

        :param regex: for filtering name
        :param kind: the kind of object, port, agg, etc.
        :param plot:
        :param logger_level: work silently!
        :return: dictionary of created objects and DataFrame with info about each.
        """
        # too painful getting the one thing out!
        ans = []
        # temp logger level
        lm = LoggerManager(logger_level)
        if kind is None or kind == '':
            df = self.knowledge.droplevel('kind').filter(regex=regex, axis=0).copy()
        else:
            df = self.knowledge.loc[kind].filter(regex=regex, axis=0).copy()

        if plot is False and show is False:
            # just act like a filtered listing on knowledge
            return df.sort_values('name')

        # added detail columns
        df['log2'] = 0
        df['bs'] = 0.
        df['agg_m'] = 0.
        df['agg_cv'] = 0.
        df['agg_sd'] = 0.
        df['emp_m'] = 0.
        df['emp_cv'] = 0.
        df['emp_sd'] = 0.

        for n, row in df.iterrows():
            p = row.program
            try:
                a = self.build(p)
                ans.append(a)
            except NotImplementedError:
                logger.error(f'skipping {n}...element not implemented')
            else:
                if show:
                    display(a)
                    display(HTML('<h4>Program</h4>'))
                    pprint(p, split=60, html=True)
                if plot is True:
                    a.plot(figsize=(10, 3))
                    display(HTML('<h4>Density and Quantiles</h4>'))
                    show_fig(a.figure, format='svg')
                if show:
                    display(HTML('<br>'))
                # info
                m, cv = a.describe.loc['Agg', ['Est E(X)', 'Est CV(X)']]
                df.loc[n, ['log2', 'bs', 'agg_m', 'agg_cv', 'agg_sd',
                            'emp_m', 'emp_cv', 'emp_sd']] =\
                        (a.log2, a.bs, a.agg_m, a.agg_cv, a.agg_sd, m, cv, '')
        # if only one item, return it...much easier to use
        if len(ans) == 1: ans = a
        return ans, df

    def dir(self, filter=''):
        """
        List all agg databases in site and default directories.
        If entries is True then read them and return named objects.

        Filter = glob filter for filename; .agg is added

        """

        if filter=='':
            filter = '*.agg'
        else:
            filter += '.agg'

        entries = []

        for dn, d in zip(['site', 'default'], [self.site_dir, self.default_dir]):
            for fn in d.glob(filter):
                txt = fn.read_text(encoding='utf-8')
                stxt = txt.split('\n')
                for r in stxt:
                    rs = r.split(' ')
                    if rs[0] in ['agg', 'port', 'dist', 'distortion', 'sev']:
                        entries.append([dn, fn.name] + rs[:2])

        ans = pd.DataFrame(entries, columns=['Directory', 'Database', 'kind', 'name'])
        return ans

    # def list(self, regex='', kind=''):
    #     """
    #     Not sure what the best name is!
    #     """
    #
    #     # catch called with kind = these seem to work...
    #     if regex in ('agg', 'port', 'sev'):
    #         kind = regex
    #         regex = ''
    #
    #     if regex == '' and kind == '':
    #         return self.knowledge
    #     elif kind == '':
    #         return self.knowledge.filter(regex=regex, axis=0)
    #     elif regex == '':
    #         return self.knowledge.loc[kind]
    #     else:
    #         return self.knowledge.loc[kind].filter(regex=regex, axis=0)

    # def describe(self, kind='agg'):
    #     """
    #     TODO this is not rationlized...and as a prop it needs to return something
    #     More informative version of knowledge showing the agg programs. Only Aggregates that have a program.
    #
    #     TODO Add severities
    #
    #     :return:
    #     """
    #     from IPython.display import HTML
    #
    #     if kind == 'all':
    #         kinds = ['agg', 'port']
    #     else:
    #         kinds = [kind]
    #
    #     if 'port' in kinds:
    #         cols = ['Name', 'Type', 'Agg1', 'Agg2', 'Agg3', 'Notes']
    #         df_port = pd.DataFrame(columns=cols)
    #         df_port = df_port.set_index('Name')
    #         for (kind, name), (spec, program) in self._knowledge.xs('port', axis=0, level=0,
    #                                                                 drop_level=False).iterrows():
    #             aggs = spec['spec'][:3]  # the list of agg items
    #             if len(aggs) == 1:
    #                 aggs.extend(['', ''])
    #             elif len(aggs) == 2:
    #                 aggs.extend([''])
    #             note = spec['note']
    #             df_port.loc[name, :] = [kind] + aggs + [note]
    #         df_port = df_port.sort_index()
    #         return df_port
    #         # display(HTML('<h3>Known Portfolios</h3>'))
    #         # display(df_port)
    #
    #     if 'agg' in kinds:
    #         cols = ['Name', 'Type', 'ELoss', 'Severity', 'ESev', 'SevCV', 'Sev_a', 'Sev_b', 'Freq', 'EN', 'Freq_a',
    #                 'Notes']
    #         # what they are actually called
    #         cols_agg = ['exp_el', 'sev_name', 'sev_mean', 'sev_cv', 'sev_a', 'sev_b', 'freq_name', 'exp_en', 'freq_a',
    #                     'note']
    #         df_agg = pd.DataFrame(columns=cols)
    #         df_agg = df_agg.set_index('Name')
    #         for (kind, name), (spec, program) in self._knowledge.xs('agg', axis=0, level=0,
    #                                                                 drop_level=False).iterrows():
    #             df_agg.loc[name, :] = [kind] + [str(spec.get(f, '')) for f in cols_agg]
    #         df_agg = df_agg.sort_index()
    #         # compute E loss... but careful about arrays
    #         # TODO must be a better way
    #         for i in df_agg.query('ELoss == ""').index:
    #             es, en = df_agg.loc[i, ['ESev', 'EN']]
    #             try:
    #                 es = float(es)
    #                 en = float(en)
    #             except (ValueError, TypeError):
    #                 df_agg.loc[i, 'ELoss'] = '...'
    #             else:
    #                 df_agg.loc[i, 'ELoss'] = es * en
    #
    #         df_agg = df_agg.drop(columns='Sev_b')
    #         return df_agg
    #         # display(HTML('<h3>Known Aggregates</h3>'))
    #         # display(df_agg)
    #
    #     if 'distortion' in kinds:
    #         return self._knowledge.loc['distortion']

    # @property
    # def programs(self):
    #     """
    #     Return the knowledge as a nice dataframe
    #
    #     :return:
    #     """
    #     bit = self._knowledge[['program']]
    #     bit['note'] = [s['note'] for s in self._knowledge.spec]
    #     bit['clean program'] = bit.program.str.replace(r'note\{[^}]*\}|[ ]{2,}|\t+', ' ')
    #     bit = bit.sort_index(ascending=[False, True])
    #     bit = bit.reset_index(0, drop=False)
    #     bit = bit[['kind', 'note', 'clean program']].query('`clean program` != ""')
    #     bit = bit[['kind', 'clean program', 'note']]
    #     return bit


# exported instance
# self = dbuild = None
logger_level(30)
build = Underwriter(databases='test_suite', update=True, debug=False, log2=16)
# differentiate later!
actuary_build = student_build = capital_build = dev_build = build
debug_build = Underwriter(name='Debug', update=True, debug=True, log2=13)
